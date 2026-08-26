"""Append-only execution logging for TalkInsight (CLAUDE.md §7).

Every pipeline stage (A0..A8, B1..B7) calls log_event() exactly once per
attempt. Logs are JSONL, one file per calendar date, never rewritten or
truncated -- only appended to, per the append-only constraint (C8).

Never pass decryption keys, account identifiers, or other key material in
`detail` -- log records may be shared with operators for debugging (R4).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .paths import LOGS_DIR

Status = Literal["success", "failure", "skip"]


def log_event(
    stage: str,
    status: Status,
    detail: str | None = None,
    period_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one execution record to today's log file.

    stage: pipeline stage id, e.g. "A0", "A3", "B5".
    status: "success" | "failure" | "skip".
    detail: short human-readable note (no secrets, no full message text).
    period_id: the collection period this record belongs to, if applicable.
    extra: additional structured fields (file paths, counts) -- no PII.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).astimezone()
    record = {
        "ts": now.isoformat(timespec="seconds"),
        "stage": stage,
        "status": status,
    }
    if period_id is not None:
        record["period_id"] = period_id
    if detail is not None:
        record["detail"] = detail
    if extra:
        record["extra"] = extra

    log_path = LOGS_DIR / f"{now.date().isoformat()}.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
