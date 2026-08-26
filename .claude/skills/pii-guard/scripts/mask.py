"""A6 (PII 마스킹): 전화번호·이메일·주민번호·노션 공유 토큰을 항상 마스킹한다.

설계서 §2.4 A6, references/pii_patterns.md. consent.yaml 모드와 무관하게 항상
적용된다. apply.py가 이 모듈의 mask_text()/rescan()을 호출해 전체 인사이트
JSON을 순회하며 사용한다.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# 순서가 중요하다: 휴대폰 번호를 먼저 마스킹해야 이후 패턴이 이미 마스킹된
# 자리표시자를 오탐하지 않는다 (references/pii_patterns.md 참조).
_PATTERNS: list[tuple[str, "re.Pattern", str]] = [
    ("mobile_phone", re.compile(r"01[0-9][- .]?\d{3,4}[- .]?\d{4}"), "[전화번호 마스킹]"),
    ("landline_phone", re.compile(r"0(?:2|[3-6][1-5])[- .]?\d{3,4}[- .]?\d{4}"), "[전화번호 마스킹]"),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[이메일 마스킹]"),
    ("rrn", re.compile(r"\d{6}-?[1-8]\d{6}"), "[주민번호 마스킹]"),
]

_NOTION_URL_QUERY_RE = re.compile(r"(https?://[\w.-]*notion\.so/\S+?)\?[^\s\]\)]*")


def mask_text(text: str) -> tuple[str, dict[str, int]]:
    """텍스트 1건에 모든 PII 패턴을 적용한다. (마스킹된 텍스트, 패턴별 매치 수)를 반환."""
    counts: dict[str, int] = {}
    for name, pattern, replacement in _PATTERNS:
        text, n = pattern.subn(replacement, text)
        if n:
            counts[name] = counts.get(name, 0) + n

    text, n = _NOTION_URL_QUERY_RE.subn(r"\1", text)
    if n:
        counts["notion_share_token"] = counts.get("notion_share_token", 0) + n

    return text, counts


def rescan(text: str) -> dict[str, int]:
    """마스킹 이후 재검사 — 남아 있는 PII 패턴 매치를 센다 (성공 기준: 0건).

    notion_share_token은 정보 유출이 아니라 링크 정리이므로 재검사 대상에서 제외한다.
    """
    counts: dict[str, int] = {}
    for name, pattern, _ in _PATTERNS:
        n = len(pattern.findall(text))
        if n:
            counts[name] = n
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="A6: 텍스트 파일의 PII를 마스킹해 stdout에 출력 (단독 테스트용)")
    parser.add_argument("input", type=Path)
    args = parser.parse_args()

    text = args.input.read_text(encoding="utf-8")
    masked, counts = mask_text(text)
    remaining = rescan(masked)

    print(masked)
    print(f"--- 마스킹 {sum(counts.values())}건: {counts} ---", file=sys.stderr)
    if remaining:
        print(f"--- 경고: 재검사에서 {remaining} 남음 ---", file=sys.stderr)


if __name__ == "__main__":
    main()
