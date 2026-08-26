"""A1 (txt 경로): 카카오톡 공식 내보내기 txt를 공통 정규화 스키마로 변환한다.

설계서 §2.4 A1. 형식이 실기기로 검증되지 않았으므로(§6 O1'''),
references/kakao_txt_formats.md에 정리된 두 알려진 형식(A: 쉼표 구분,
B: 대괄호+별도 날짜헤더)을 순서대로 시도한다. 실제 Mac 내보내기 샘플을
확보하면 이 파일의 정규식과 kakao_txt_formats.md를 함께 갱신할 것.

성공 기준 (설계서 §2.4 A1):
  - 파싱률 >= 99%: 정상
  - 95%~99%: 미파싱 라인 로그 후 진행 (skip+log)
  - < 95% 또는 형식 자체 미인식: 에스컬레이션
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from lib.common.escalate import escalate
from lib.common.log import log_event
from lib.common.schema import NormalizedMessage, write_jsonl

URL_RE = re.compile(r"https?://\S+")

SYSTEM_KEYWORDS = (
    "님이 들어왔습니다",
    "님이 나갔습니다",
    "님을 초대했습니다",
    "채팅방 관리자",
    "삭제된 메시지",
)

# 형식 A: "2023. 6. 2. 오후 11:46, 홍길동 : 메시지"
FORMAT_A_RE = re.compile(
    r"^(?P<y>\d{4})\.\s*(?P<mo>\d{1,2})\.\s*(?P<d>\d{1,2})\.\s*"
    r"(?P<ap>오전|오후)\s*(?P<h>\d{1,2}):(?P<mi>\d{2}),\s*"
    r"(?P<name>[^:]+?)\s*:\s*(?P<text>.*)$"
)

# 형식 B 날짜 헤더: "--------------- 2023년 6월 2일 금요일 ---------------"
FORMAT_B_DATE_RE = re.compile(r"(?P<y>\d{4})년\s*(?P<mo>\d{1,2})월\s*(?P<d>\d{1,2})일")

# 형식 B 메시지: "[홍길동] [오후 11:46] 메시지"
FORMAT_B_MSG_RE = re.compile(
    r"^\[(?P<name>.+?)\]\s*\[(?P<ap>오전|오후)\s*(?P<h>\d{1,2}):(?P<mi>\d{2})\]\s*(?P<text>.*)$"
)

# 형식 A의 시스템 메시지 변형: 발신자 없이 "날짜, 텍스트" (이름:콜론 구조가 없음).
# 일반 메시지와 구분하기 위해 SYSTEM_KEYWORDS가 포함될 때만 이 패턴을 인정한다.
FORMAT_A_SYSTEM_RE = re.compile(
    r"^(?P<y>\d{4})\.\s*(?P<mo>\d{1,2})\.\s*(?P<d>\d{1,2})\.\s*"
    r"(?P<ap>오전|오후)\s*(?P<h>\d{1,2}):(?P<mi>\d{2}),\s*(?P<text>.*)$"
)

# 헤더/메타 라인 (파싱률 분모에서 제외)
SKIP_LINE_RE = re.compile(r"^(카카오톡 대화|저장한 날짜\s*:)")


def _to_24h(ap: str, h: int) -> int:
    h = h % 12
    if ap == "오후":
        h += 12
    return h


def _extract_links(text: str) -> list[str]:
    return URL_RE.findall(text)


def _is_system_text(text: str) -> bool:
    return any(kw in text for kw in SYSTEM_KEYWORDS)


def parse_txt(path: Path) -> tuple[list[NormalizedMessage], dict]:
    messages: list[NormalizedMessage] = []
    stats = {"total_content_lines": 0, "matched": 0, "unmatched": 0}

    current_date: Optional[tuple[int, int, int]] = None
    unmatched_samples: list[str] = []

    with open(path, encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n\r")
            stripped = line.strip()
            if not stripped or SKIP_LINE_RE.match(stripped):
                continue

            date_header = FORMAT_B_DATE_RE.search(stripped)
            # 형식 B 날짜 헤더 라인 자체는 메시지가 아니므로 분모에서 제외하고 상태만 갱신
            if date_header and not FORMAT_B_MSG_RE.match(stripped) and not FORMAT_A_RE.match(stripped):
                current_date = (
                    int(date_header.group("y")),
                    int(date_header.group("mo")),
                    int(date_header.group("d")),
                )
                continue

            stats["total_content_lines"] += 1

            m = FORMAT_A_RE.match(stripped)
            if m:
                ts = datetime(
                    int(m.group("y")),
                    int(m.group("mo")),
                    int(m.group("d")),
                    _to_24h(m.group("ap"), int(m.group("h"))),
                    int(m.group("mi")),
                )
                text = m.group("text")
                messages.append(
                    NormalizedMessage(
                        ts=ts,
                        nickname=m.group("name").strip(),
                        text=text,
                        is_system=_is_system_text(text),
                        src="txt",
                        links=_extract_links(text),
                    )
                )
                stats["matched"] += 1
                continue

            m = FORMAT_B_MSG_RE.match(stripped)
            if m and current_date:
                ts = datetime(
                    current_date[0],
                    current_date[1],
                    current_date[2],
                    _to_24h(m.group("ap"), int(m.group("h"))),
                    int(m.group("mi")),
                )
                text = m.group("text")
                messages.append(
                    NormalizedMessage(
                        ts=ts,
                        nickname=m.group("name").strip(),
                        text=text,
                        is_system=_is_system_text(text),
                        src="txt",
                        links=_extract_links(text),
                    )
                )
                stats["matched"] += 1
                continue

            # 형식 A의 발신자 없는 시스템 메시지: "날짜, 텍스트" (콜론 구분자 없음)
            m = FORMAT_A_SYSTEM_RE.match(stripped)
            if m and _is_system_text(m.group("text")):
                ts = datetime(
                    int(m.group("y")),
                    int(m.group("mo")),
                    int(m.group("d")),
                    _to_24h(m.group("ap"), int(m.group("h"))),
                    int(m.group("mi")),
                )
                messages.append(
                    NormalizedMessage(
                        ts=ts,
                        nickname=None,
                        text=m.group("text"),
                        is_system=True,
                        src="txt",
                        links=[],
                    )
                )
                stats["matched"] += 1
                continue

            # 시간 정보 없는 단독 시스템 메시지 라인 — 최선 추정으로 기록
            if _is_system_text(stripped) and current_date:
                ts = datetime(current_date[0], current_date[1], current_date[2])
                messages.append(
                    NormalizedMessage(
                        ts=ts,
                        nickname=None,
                        text=stripped,
                        is_system=True,
                        src="txt",
                        links=[],
                    )
                )
                stats["matched"] += 1
                continue

            stats["unmatched"] += 1
            if len(unmatched_samples) < 10:
                unmatched_samples.append(stripped)

    stats["unmatched_samples"] = unmatched_samples
    return messages, stats


def main() -> None:
    parser = argparse.ArgumentParser(description="A1: 카카오톡 내보내기 txt 정규화")
    parser.add_argument("input", type=Path, help="내보내기 txt 파일 경로")
    parser.add_argument("output", type=Path, help="정규화 jsonl 출력 경로")
    args = parser.parse_args()

    messages, stats = parse_txt(args.input)
    total = stats["total_content_lines"]
    parse_rate = (stats["matched"] / total) if total else 1.0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(messages, args.output)

    detail = f"파싱률 {parse_rate:.1%} ({stats['matched']}/{total})"
    print(f"[parse_txt] {args.input} -> {args.output}: {detail}")

    if total == 0:
        log_event("A1", "skip", detail="입력 라인 없음")
    elif parse_rate >= 0.99:
        log_event("A1", "success", detail=detail)
    elif parse_rate >= 0.95:
        log_event(
            "A1", "skip",
            detail=f"{detail} — 미파싱 라인 존재, 진행함",
            extra={"unmatched_samples": stats["unmatched_samples"]},
        )
    else:
        log_event("A1", "failure", detail=detail)
        escalate(
            "A1",
            f"txt 파싱률이 95% 미만입니다 ({detail}). 형식 변경 의심 — kakao_txt_formats.md 갱신 필요.",
            files=[str(args.input)],
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
