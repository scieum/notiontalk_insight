"""A3 1차: 시간 간격·시스템 메시지·답글 참조로 스레드 경계를 규칙 기반으로 정한다.

설계서 §2.4 A3, §2.3. 이 스크립트는 "확정" 경계만 실제로 분할하고, 애매한
경계(15~30분 공백, 연속 질문)는 분할하지 않은 채 ambiguous_boundaries로만
기록한다 — insight-extractor(LLM 2차)가 없으면 애매 구간은 그냥 하나의
스레드로 남는다. 이게 설계서가 말하는 "LLM 판정 실패 시 1차 규칙 결과로
대체"의 자연스러운 기본값이다.

확정 경계:
  - 직전/현재 메시지가 시스템 메시지 (입장/퇴장 등)
  - 메시지 간 공백 >= 30분

애매 경계(기록만, 분할 안 함):
  - 메시지 간 공백 15~30분
  - 연속된 두 메시지가 모두 질문형(물음표로 끝남)

성공 기준: 모든 메시지가 정확히 1개 스레드에 속함, 스레드 크기 1~200.
200 초과 시 시간 기준으로 강제 재분할한다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from lib.common.log import log_event
from lib.common.paths import MESSAGES_DB_PATH, THREADS_DIR

GAP_HARD_SPLIT_SECONDS = 30 * 60
GAP_AMBIGUOUS_MIN_SECONDS = 15 * 60
MAX_THREAD_SIZE = 200


def load_period_messages(period_id: str, db_path: Path = MESSAGES_DB_PATH) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, ts, nickname, text, is_system, reply_to FROM messages"
        " WHERE period_id = ? ORDER BY ts, id",
        (period_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _is_question_like(text: str) -> bool:
    return text.rstrip().endswith(("?", "？"))


def _gap_seconds(prev_ts: str, cur_ts: str) -> float:
    from datetime import datetime

    return (datetime.fromisoformat(cur_ts) - datetime.fromisoformat(prev_ts)).total_seconds()


def split_threads(messages: list[dict]) -> tuple[list[list[dict]], list[dict]]:
    """1차 규칙으로 확정 분할한 스레드 목록과, 애매 경계 후보 목록을 반환한다."""
    threads: list[list[dict]] = []
    current: list[dict] = []
    ambiguous: list[dict] = []

    for msg in messages:
        if not current:
            current = [msg]
            continue

        prev = current[-1]
        gap = _gap_seconds(prev["ts"], msg["ts"])

        if prev["is_system"] or msg["is_system"] or gap >= GAP_HARD_SPLIT_SECONDS:
            threads.append(current)
            current = [msg]
            continue

        if GAP_AMBIGUOUS_MIN_SECONDS <= gap < GAP_HARD_SPLIT_SECONDS:
            ambiguous.append(
                {"before_message_id": msg["id"], "reason": "gap_15_30min", "gap_seconds": gap}
            )
        elif _is_question_like(prev["text"]) and _is_question_like(msg["text"]):
            ambiguous.append(
                {"before_message_id": msg["id"], "reason": "consecutive_questions"}
            )

        current.append(msg)

    if current:
        threads.append(current)

    return threads, ambiguous


def _enforce_max_size(threads: list[list[dict]]) -> tuple[list[list[dict]], int]:
    """스레드 크기가 MAX_THREAD_SIZE를 넘으면 시간 순서대로 강제 재분할한다."""
    result = []
    forced_splits = 0
    for th in threads:
        if len(th) <= MAX_THREAD_SIZE:
            result.append(th)
            continue
        forced_splits += 1
        for i in range(0, len(th), MAX_THREAD_SIZE):
            result.append(th[i : i + MAX_THREAD_SIZE])
    return result, forced_splits


def build_output(period_id: str, threads: list[list[dict]], ambiguous: list[dict]) -> dict:
    msg_id_to_thread_id: dict[int, str] = {}
    thread_objs = []
    for i, th in enumerate(threads, start=1):
        thread_id = f"T{i}"
        for msg in th:
            msg_id_to_thread_id[msg["id"]] = thread_id
        thread_objs.append(
            {
                "thread_id": thread_id,
                "message_ids": [m["id"] for m in th],
                "start_ts": th[0]["ts"],
                "end_ts": th[-1]["ts"],
                "is_system_only": all(m["is_system"] for m in th),
            }
        )

    for item in ambiguous:
        item["thread_id"] = msg_id_to_thread_id.get(item["before_message_id"])

    return {"period_id": period_id, "threads": thread_objs, "ambiguous_boundaries": ambiguous}


def run(period_id: str, db_path: Path = MESSAGES_DB_PATH, output_dir: Path = THREADS_DIR) -> Path:
    messages = load_period_messages(period_id, db_path)
    if not messages:
        log_event("A3", "skip", period_id=period_id, detail="구간에 메시지 없음")
        raise SystemExit(f"구간 {period_id}에 메시지가 없습니다")

    threads, ambiguous = split_threads(messages)
    threads, forced_splits = _enforce_max_size(threads)
    output = build_output(period_id, threads, ambiguous)

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"period_{period_id}.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    total_msgs = sum(len(t["message_ids"]) for t in output["threads"])
    assert total_msgs == len(messages), "모든 메시지가 정확히 1개 스레드에 속해야 합니다"

    detail = (
        f"메시지 {len(messages)}건 -> 스레드 {len(output['threads'])}개, "
        f"애매 경계 {len(ambiguous)}건, 강제분할 {forced_splits}건"
    )
    status = "success" if forced_splits == 0 else "skip"
    log_event("A3", status, period_id=period_id, detail=detail)
    print(f"[split_threads] {detail} -> {out_path}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="A3 1차: 규칙 기반 스레드 분할")
    parser.add_argument("period_id")
    args = parser.parse_args()
    run(args.period_id)


if __name__ == "__main__":
    main()
