"""A2: 정규화된 메시지를 messages.sqlite에 중복 없이 append하고 구간을 산출한다.

설계서 §2.4 A2, §3.1. append-only(C8) — 기존 행은 절대 갱신·삭제하지 않는다.
중복 판정은 lib.common.schema.dedup_key()의 결과(hash 컬럼, UNIQUE)로만 한다.

성공 기준: 중복 삽입 0건, 신규 메시지 >= 1건.
실패 시: 신규 0건 -> skip+log(발행 사이클 미실행). 동일 해시·다른 원문 감지 시
둘 다 보존하고 로그로 남긴다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from lib.common.log import log_event
from lib.common.paths import MESSAGES_DB_PATH, MESSAGES_DIR
from lib.common.schema import dedup_key, read_jsonl

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT UNIQUE NOT NULL,
    ts TEXT NOT NULL,
    nickname TEXT,
    text TEXT NOT NULL,
    is_system INTEGER NOT NULL,
    links TEXT NOT NULL,
    reply_to TEXT,
    period_id TEXT,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS periods (
    id TEXT PRIMARY KEY,
    start_ts TEXT NOT NULL,
    end_ts TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def _next_period_id(conn: sqlite3.Connection, on_date: str) -> str:
    """같은 날짜에 여러 구간이 생기면 _A, _B, ... 로 구분한다 (설계서 §3.2 예시 형식)."""
    existing = {
        row[0] for row in conn.execute(
            "SELECT id FROM periods WHERE id LIKE ?", (f"{on_date}_%",)
        )
    }
    for i in range(26):
        candidate = f"{on_date}_{chr(ord('A') + i)}"
        if candidate not in existing:
            return candidate
    raise RuntimeError(f"{on_date}에 대해 사용 가능한 구간 문자가 소진되었습니다")


def merge(input_paths: list[Path], db_path: Path = MESSAGES_DB_PATH) -> dict:
    conn = _connect(db_path)
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    inserted_ts: list[datetime] = []
    stats = {"processed": 0, "inserted": 0, "duplicate": 0, "hash_collision_diff_content": 0}

    for path in input_paths:
        for msg in read_jsonl(path):
            stats["processed"] += 1
            key = dedup_key(msg)

            existing = conn.execute(
                "SELECT nickname, text FROM messages WHERE hash = ?", (key,)
            ).fetchone()

            if existing is not None:
                if existing == (msg.nickname, msg.text):
                    stats["duplicate"] += 1
                    continue
                # 동일 키·다른 원문 — 진짜 충돌 의심. 접미사를 붙여 둘 다 보존한다.
                stats["hash_collision_diff_content"] += 1
                suffix = 2
                new_key = f"{key}#{suffix}"
                while conn.execute(
                    "SELECT 1 FROM messages WHERE hash = ?", (new_key,)
                ).fetchone():
                    suffix += 1
                    new_key = f"{key}#{suffix}"
                key = new_key

            conn.execute(
                "INSERT INTO messages (hash, ts, nickname, text, is_system, links, reply_to, period_id, ingested_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    key,
                    msg.ts.isoformat(),
                    msg.nickname,
                    msg.text,
                    int(msg.is_system),
                    json.dumps(msg.links, ensure_ascii=False),
                    msg.reply_to,
                    now,
                ),
            )
            stats["inserted"] += 1
            inserted_ts.append(msg.ts)

    period_id = None
    if stats["inserted"] > 0:
        start_ts = min(inserted_ts)
        end_ts = max(inserted_ts)
        period_id = _next_period_id(conn, start_ts.date().isoformat())
        conn.execute(
            "INSERT INTO periods (id, start_ts, end_ts, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (period_id, start_ts.isoformat(), end_ts.isoformat(), "merged", now),
        )
        conn.execute(
            "UPDATE messages SET period_id = ? WHERE period_id IS NULL", (period_id,)
        )

    conn.commit()
    conn.close()

    stats["period_id"] = period_id
    return stats


def _write_period_summary(stats: dict, source_files: list[Path]) -> Path | None:
    if not stats["period_id"]:
        return None
    out_path = MESSAGES_DIR / f"period_{stats['period_id']}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "period_id": stats["period_id"],
                "new_message_count": stats["inserted"],
                "duplicate_count": stats["duplicate"],
                "hash_collision_diff_content": stats["hash_collision_diff_content"],
                "source_files": [str(p) for p in source_files],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="A2: 정규화 jsonl을 messages.sqlite에 병합")
    parser.add_argument("inputs", nargs="+", type=Path, help="A1이 출력한 정규화 jsonl 파일(들)")
    args = parser.parse_args()

    stats = merge(args.inputs)
    detail = (
        f"처리 {stats['processed']}건, 신규 {stats['inserted']}건, "
        f"중복 {stats['duplicate']}건, 해시충돌 {stats['hash_collision_diff_content']}건"
    )
    print(f"[merge] {detail}")

    if stats["inserted"] == 0:
        log_event("A2", "skip", detail=f"{detail} (신규 0건 — 발행 사이클 미실행)")
        return

    summary_path = _write_period_summary(stats, args.inputs)
    log_event(
        "A2", "success",
        period_id=stats["period_id"],
        detail=detail,
        extra={"summary_path": str(summary_path)},
    )
    print(f"[merge] period {stats['period_id']} -> {summary_path}")


if __name__ == "__main__":
    main()
