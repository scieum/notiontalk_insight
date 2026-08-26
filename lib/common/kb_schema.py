"""kb.sqlite 공용 스키마 (설계서 §3.3). kb-builder(A8/B1/B2)와 kb-search(B4) 둘 다
같은 스키마를 봐야 하므로 여기 한 곳에 정의한다.

embedding은 BLOB에 float32 배열을 그대로 패킹해 저장한다 — numpy 의존 없이
표준 라이브러리 struct만으로 인코딩/디코딩한다.
"""

from __future__ import annotations

import sqlite3
import struct

KB_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK(source IN ('chat', 'notion_help', 'notiontalk_site')),
    text TEXT NOT NULL,
    embedding BLOB NOT NULL,
    url TEXT,
    heading_path TEXT,
    message_ids TEXT,
    period_id TEXT,
    updated_at TEXT NOT NULL,
    superseded INTEGER NOT NULL DEFAULT 0
);
"""


def ensure_kb_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(KB_SCHEMA)


def encode_embedding(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def decode_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))
