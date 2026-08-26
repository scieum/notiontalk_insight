"""A4 입력 준비: 구간에서 처리할 스레드를 고르고 LLM 호출 단위로 묶는다.

왜 필요한가 — D11 백필로 `period_2025-01-26_A` 하나에 19개월(스레드 6,036개)이
들어 있다. 이걸 통째로 A4에 넣는 것은 토큰 예산으로도, 발행 단위(주 2회)로도
맞지 않는다. 그래서 A4는 구간 전체가 아니라 **날짜로 잘라낸 슬라이스**를 받는다.

이 모듈은 LLM을 호출하지 않는다 — 순수 규칙이라 API 키 없이 검증된다.

용어:
- 슬라이스: `--since`/`--until`로 잘라낸 스레드 집합. 발행 1회분에 대응한다.
- 배치: LLM 호출 1회에 들어가는 스레드 묶음. 메시지 수·글자 수 상한으로 나눈다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from lib.common.paths import MESSAGES_DB_PATH, THREADS_DIR

# 배치 상한. 넘기면 스레드 하나가 통째로 잘리는 게 아니라 다음 배치로 넘어간다.
# (스레드는 A4의 최소 단위이므로 절대 쪼개지 않는다 — 쪼개면 근거 대조가 깨진다.)
MAX_MESSAGES_PER_BATCH = 80
MAX_CHARS_PER_BATCH = 12_000
# 스레드 수 상한이 따로 필요한 이유: 응답 JSON 길이는 입력 글자 수가 아니라
# **스레드 개수**에 비례한다(스레드마다 객체 하나). 짧은 스레드 40개짜리 배치는
# 입력 상한에 안 걸리면서 응답만 길어져, 2026-08-26 실측에서 호출 하나가
# 10분 넘게 안 끝났다.
MAX_THREADS_PER_BATCH = 12

# 이보다 짧은 스레드는 인사이트가 나올 수 없다("ㅋㅋ", "넵" 같은 반응만 있는 경우).
# 버리는 게 아니라 세어서 로그로 남긴다 — 조용히 잘라내면 "전부 처리했다"로 읽힌다.
MIN_CONTENT_CHARS = 30


def load_threads(period_id: str) -> dict:
    path = THREADS_DIR / f"period_{period_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"스레드 파일이 없습니다: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_messages(message_ids: list[int], db_path: Path = MESSAGES_DB_PATH) -> dict[int, dict]:
    """messages.sqlite에서 id로 메시지를 읽어온다. 읽기 전용으로만 연다."""
    if not message_ids:
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        out: dict[int, dict] = {}
        # SQLite 변수 개수 상한(기본 999)을 피해 나눠 조회한다.
        chunk = 500
        for i in range(0, len(message_ids), chunk):
            part = message_ids[i : i + chunk]
            placeholders = ",".join("?" * len(part))
            rows = conn.execute(
                f"SELECT id, ts, nickname, text, is_system, links, reply_to "
                f"FROM messages WHERE id IN ({placeholders})",
                part,
            ).fetchall()
            for row in rows:
                out[row["id"]] = dict(row)
        return out
    finally:
        conn.close()


def _in_range(ts: str, since: str | None, until: str | None) -> bool:
    if since and ts < since:
        return False
    if until and ts >= until:
        return False
    return True


def select(
    period_id: str,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """슬라이스를 만든다. 반환값의 threads는 메시지 본문이 붙은 상태다."""
    data = load_threads(period_id)
    threads = data["threads"]

    in_range = [t for t in threads if _in_range(t["start_ts"], since, until)]

    all_ids: list[int] = []
    for t in in_range:
        all_ids.extend(t["message_ids"])
    messages = load_messages(all_ids)

    kept: list[dict] = []
    dropped_system = 0
    dropped_short = 0
    missing_ids = 0

    for t in in_range:
        msgs = []
        for mid in t["message_ids"]:
            m = messages.get(mid)
            if m is None:
                missing_ids += 1
                continue
            msgs.append(m)

        if t.get("is_system_only") or all(m["is_system"] for m in msgs):
            dropped_system += 1
            continue

        content = [m for m in msgs if not m["is_system"]]
        content_chars = sum(len(m["text"]) for m in content)
        if content_chars < MIN_CONTENT_CHARS:
            dropped_short += 1
            continue

        kept.append(
            {
                "thread_id": t["thread_id"],
                "start_ts": t["start_ts"],
                "end_ts": t["end_ts"],
                "messages": [
                    {
                        "id": m["id"],
                        "ts": m["ts"],
                        "nickname": m["nickname"],
                        "text": m["text"],
                        "is_system": bool(m["is_system"]),
                    }
                    for m in msgs
                ],
                "content_chars": content_chars,
            }
        )

    return {
        "period_id": period_id,
        "since": since,
        "until": until,
        "threads": kept,
        "stats": {
            "threads_in_range": len(in_range),
            "threads_selected": len(kept),
            "dropped_system_only": dropped_system,
            "dropped_too_short": dropped_short,
            "missing_message_ids": missing_ids,
            "message_count": sum(len(t["messages"]) for t in kept),
            "participant_count": len(
                {
                    m["nickname"]
                    for t in kept
                    for m in t["messages"]
                    if not m["is_system"] and m["nickname"]
                }
            ),
        },
    }


def make_batches(
    threads: list[dict],
    max_messages: int = MAX_MESSAGES_PER_BATCH,
    max_chars: int = MAX_CHARS_PER_BATCH,
    max_threads: int = MAX_THREADS_PER_BATCH,
) -> list[list[dict]]:
    """스레드를 LLM 호출 단위로 묶는다. 스레드 하나는 절대 쪼개지 않는다.

    상한을 혼자서 이미 넘는 큰 스레드는 자기 혼자 한 배치가 된다(잘리지 않는다).
    """
    batches: list[list[dict]] = []
    current: list[dict] = []
    cur_msgs = 0
    cur_chars = 0

    for t in threads:
        n_msgs = len(t["messages"])
        n_chars = t["content_chars"]
        if current and (
            cur_msgs + n_msgs > max_messages
            or cur_chars + n_chars > max_chars
            or len(current) >= max_threads
        ):
            batches.append(current)
            current, cur_msgs, cur_chars = [], 0, 0
        current.append(t)
        cur_msgs += n_msgs
        cur_chars += n_chars

    if current:
        batches.append(current)
    return batches


def slice_id(period_id: str, since: str | None, until: str | None) -> str:
    """슬라이스 산출물 파일명에 쓸 식별자. 구간 id와 헷갈리지 않게 접미사를 붙인다."""
    if not since and not until:
        return period_id
    a = (since or "처음").replace("-", "")[:8]
    b = (until or "끝").replace("-", "")[:8]
    return f"{period_id}__{a}-{b}"


def main() -> None:
    ap = argparse.ArgumentParser(description="A4 입력 슬라이스 선택·배치 분할 (LLM 미호출)")
    ap.add_argument("--period-id", required=True)
    ap.add_argument("--since", help="ISO 날짜/시각. 이 시각 이후 시작한 스레드만 (예: 2026-08-01)")
    ap.add_argument("--until", help="ISO 날짜/시각. 이 시각 이전 시작한 스레드만 (미포함)")
    ap.add_argument("--out", help="슬라이스 JSON 저장 경로 (생략하면 저장하지 않고 통계만 출력)")
    args = ap.parse_args()

    sl = select(args.period_id, args.since, args.until)
    batches = make_batches(sl["threads"])
    sl["batch_count"] = len(batches)

    st = sl["stats"]
    print(f"[slice] {slice_id(args.period_id, args.since, args.until)}")
    print(f"  범위 내 스레드   {st['threads_in_range']}")
    print(f"  선택            {st['threads_selected']}")
    print(f"  제외(시스템만)   {st['dropped_system_only']}")
    print(f"  제외(너무 짧음)  {st['dropped_too_short']}  (기준 {MIN_CONTENT_CHARS}자 미만)")
    if st["missing_message_ids"]:
        print(f"  !! 본문 없는 message_id {st['missing_message_ids']}건")
    print(f"  메시지          {st['message_count']}")
    print(f"  참여자          {st['participant_count']}")
    print(f"  LLM 호출 배치    {len(batches)}  (상한 {MAX_THREADS_PER_BATCH}스레드 / "
          f"{MAX_MESSAGES_PER_BATCH}메시지 / {MAX_CHARS_PER_BATCH}자)")

    if batches:
        sizes = [len(b) for b in batches]
        print(f"  배치당 스레드    최소 {min(sizes)} / 최대 {max(sizes)} / 평균 {sum(sizes)/len(sizes):.1f}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(sl, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  -> {out}")


if __name__ == "__main__":
    main()
