"""Project-root-relative path helpers shared by all TalkInsight skill scripts.

Every skill script imports from here instead of hardcoding relative paths,
so scripts can be invoked from any working directory (Claude Code, launchd,
or a manual shell) and still resolve /output, /inbox, /docs consistently.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DOCS_DIR = ROOT / "docs"
INBOX_DB_EXTRACT_DIR = ROOT / "inbox" / "db_extract"
INBOX_RAW_DIR = ROOT / "inbox" / "raw"

OUTPUT_DIR = ROOT / "output"
MESSAGES_DIR = OUTPUT_DIR / "messages"
MESSAGES_PARSED_DIR = MESSAGES_DIR / "parsed"
MESSAGES_DB_PATH = MESSAGES_DIR / "messages.sqlite"
THREADS_DIR = OUTPUT_DIR / "threads"
INSIGHTS_DIR = OUTPUT_DIR / "insights"
PUBLISHED_DIR = OUTPUT_DIR / "published"
KB_DIR = OUTPUT_DIR / "kb"
KB_RAW_DIR = KB_DIR / "raw"
KB_DB_PATH = KB_DIR / "kb.sqlite"
ANSWERS_DIR = OUTPUT_DIR / "answers"
QUESTIONS_DIR = OUTPUT_DIR / "questions"
LOGS_DIR = OUTPUT_DIR / "logs"
ESCALATIONS_DIR = OUTPUT_DIR / "escalations"


def ensure_output_dirs() -> None:
    """Create every /output subdirectory this project writes to, if missing.

    Safe to call at the start of any script; append-only dirs are never
    deleted or truncated here.
    """
    for d in (
        MESSAGES_PARSED_DIR,
        THREADS_DIR,
        INSIGHTS_DIR,
        PUBLISHED_DIR,
        KB_RAW_DIR,
        ANSWERS_DIR,
        QUESTIONS_DIR,
        LOGS_DIR,
        ESCALATIONS_DIR,
        INBOX_DB_EXTRACT_DIR,
        INBOX_RAW_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
