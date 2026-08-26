"""Append-only escalation log for TalkInsight (CLAUDE.md §5, §7).

Escalations are the G-E gate's input: an operator reads
/output/escalations/<date>.md and macOS notifications, then takes action
(manual export drop-in, parser fix, publish-appropriateness judgment,
writing an answer by hand).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .paths import ESCALATIONS_DIR


def escalate(
    stage: str,
    reason: str,
    files: list[str] | None = None,
    period_id: str | None = None,
) -> Path:
    """Append one escalation entry to today's escalation file.

    stage: pipeline stage id, e.g. "A0'", "A5", "B1".
    reason: why an operator needs to look at this.
    files: related file paths (relative to project root), if any.
    period_id: the collection period this escalation belongs to, if applicable.

    Returns the path written to, so callers can also trigger a local
    notification pointing at it.
    """
    ESCALATIONS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).astimezone()
    esc_path = ESCALATIONS_DIR / f"{now.date().isoformat()}.md"

    lines = [f"## {now.isoformat(timespec='seconds')} · {stage}"]
    if period_id is not None:
        lines.append(f"- 구간: {period_id}")
    lines.append(f"- 사유: {reason}")
    if files:
        lines.append("- 관련 파일:")
        lines.extend(f"  - {f}" for f in files)
    lines.append("")

    is_new = not esc_path.exists()
    with open(esc_path, "a", encoding="utf-8") as f:
        if is_new:
            f.write(f"# 에스컬레이션 로그 — {now.date().isoformat()}\n\n")
        f.write("\n".join(lines) + "\n")

    return esc_path
