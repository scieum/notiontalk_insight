"""A1 (csv 경로): Mac 카카오톡 공식 내보내기 CSV를 공통 정규화 스키마로 변환한다.

설계서 §2.4 A1. **설계서가 가정한 txt 형식(형식 A/B)이 아니다** — Mac 카카오톡
26.7.0의 "텍스트 파일로 저장"은 이름과 달리 실제로 `.csv`를 만든다
(2026-08-26 실기기 확인, ../../kakao-export/references/export_ui.md).

형식:
    Date,User,Message
    2025-01-26 09:49:48,"닉네임","본문"

txt 형식보다 오히려 다루기 쉽다 — 초 단위 타임스탬프가 그대로 있고 오전/오후
변환이 필요 없으며, 날짜 헤더 상태를 들고 다닐 필요도 없다. 개행이 든 본문은
CSV 인용부호 안에 그대로 들어오므로 표준 csv 모듈이 알아서 한 레코드로 읽는다.

## Date가 빈 행 (본문 없는 자리표시자)

내보내기에는 Date·User가 모두 빈 행이 섞여 있다:

    "메시지가 삭제되었습니다."      (실측 265건)
    "관리자가 메시지를 가렸습니다."  (실측 17건)

내용도 시각도 없어 타임라인 위에 놓을 수 없다. 그래서 **버리되**, 파싱 실패로
세지 않는다 — 이걸 실패로 세면 파싱률이 부당하게 떨어져 §5의 에스컬레이션이
헛돈다. `skipped_placeholder`로 따로 집계한다.

## 시스템 메시지 판정

입퇴장은 `Message`가 `User + "님이 들어왔습니다."` 형태다(실측 2176건 전부 일치).
그래서 **구조로** 판정한다 — 단순 키워드 포함으로 보면 "삭제된 메시지 너무
궁금한 것..." 같은 **평범한 사용자 발언을 시스템 메시지로 오분류**한다
(실측에 실제로 그런 행이 있다).

성공 기준 (설계서 §2.4 A1):
  - 파싱률 >= 99%: 정상
  - 95%~99%: 미파싱 라인 로그 후 진행 (skip+log)
  - < 95% 또는 형식 자체 미인식: 에스컬레이션
"""

from __future__ import annotations

import argparse
import csv
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

STAGE = "A1"

EXPECTED_HEADER = ["Date", "User", "Message"]
DATE_FMT = "%Y-%m-%d %H:%M:%S"

URL_RE = re.compile(r"https?://\S+")

# Date·User가 비어 오는 자리표시자. 정확 일치로만 인정한다 —
# 사용자가 같은 문장을 직접 칠 수도 있으므로 부분 일치는 쓰지 않는다.
PLACEHOLDER_TEXTS = frozenset({
    "메시지가 삭제되었습니다.",
    "관리자가 메시지를 가렸습니다.",
})

# "<닉네임>님이 ...했습니다." 구조로만 판정하는 시스템 이벤트 어미.
SYSTEM_SUFFIXES = (
    "님이 들어왔습니다.",
    "님이 나갔습니다.",
    "님을 초대했습니다.",
    "님이 방장으로 지정되었습니다.",
    "님을 내보냈습니다.",
)

# 발신자가 비어 있는데 Date는 있는 순수 시스템 공지 (드묾).
SYSTEM_STANDALONE = (
    "채팅방 관리자",
    "운영정책을 위반",
)


def is_system_message(user: str, text: str) -> bool:
    """구조 기반 시스템 메시지 판정.

    키워드 '포함'이 아니라 '<User>님이 ...했습니다.' 전체 구조와 맞는지 본다.
    """
    if user:
        for suffix in SYSTEM_SUFFIXES:
            if text == f"{user}{suffix}":
                return True
    else:
        if any(k in text for k in SYSTEM_STANDALONE):
            return True
    return False


def parse_csv(path: Path) -> tuple[list[NormalizedMessage], dict]:
    stats = {
        "total_rows": 0,
        "parsed": 0,
        "skipped_placeholder": 0,
        "unparsed": 0,
        "system": 0,
    }
    unparsed_samples: list[int] = []
    messages: list[NormalizedMessage] = []

    # utf-8-sig: 내보내기 파일 선두에 BOM이 붙어 있다 (실측).
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return messages, stats

        if [h.strip() for h in header] != EXPECTED_HEADER:
            raise ValueError(
                f"CSV 헤더가 예상과 다릅니다: {header!r} (예상 {EXPECTED_HEADER!r})"
            )

        for lineno, row in enumerate(reader, start=2):
            stats["total_rows"] += 1

            if len(row) != 3:
                stats["unparsed"] += 1
                if len(unparsed_samples) < 20:
                    unparsed_samples.append(lineno)
                continue

            date_s, user, text = (c.strip() if i < 2 else c
                                  for i, c in enumerate(row))

            if not date_s:
                # 시각 없는 자리표시자 — 타임라인에 놓을 수 없다.
                if text.strip() in PLACEHOLDER_TEXTS:
                    stats["skipped_placeholder"] += 1
                else:
                    stats["unparsed"] += 1
                    if len(unparsed_samples) < 20:
                        unparsed_samples.append(lineno)
                continue

            try:
                ts = datetime.strptime(date_s, DATE_FMT)
            except ValueError:
                stats["unparsed"] += 1
                if len(unparsed_samples) < 20:
                    unparsed_samples.append(lineno)
                continue

            system = is_system_message(user, text)
            if system:
                stats["system"] += 1

            messages.append(NormalizedMessage(
                ts=ts,
                nickname=user or None,
                text=text,
                is_system=system,
                src="csv",
                links=URL_RE.findall(text),
                reply_to=None,
                native_id=None,
            ))
            stats["parsed"] += 1

    stats["unparsed_sample_lines"] = unparsed_samples
    return messages, stats


def main() -> int:
    parser = argparse.ArgumentParser(description="A1: 카카오톡 내보내기 CSV 정규화")
    parser.add_argument("input", help="내보내기 csv 파일 경로")
    parser.add_argument("output", help="정규화 jsonl 출력 경로")
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)

    try:
        messages, stats = parse_csv(in_path)
    except ValueError as e:
        log_event(STAGE, "failure", detail=str(e), extra={"path": str(in_path)})
        escalate(STAGE, f"CSV 형식을 인식하지 못했다: {e}", files=[str(in_path)])
        print(f"[parse_csv] 실패: {e}", file=sys.stderr)
        return 2

    # 파싱률 분모에서 자리표시자는 뺀다 — 버리는 게 정상 동작이므로.
    denom = stats["total_rows"] - stats["skipped_placeholder"]
    rate = (stats["parsed"] / denom * 100) if denom else 100.0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(messages, out_path)

    # 원문은 남기지 않는다 — 건수와 줄 번호만 (C2, R4).
    extra = {
        "input": str(in_path), "output": str(out_path),
        "total_rows": stats["total_rows"], "parsed": stats["parsed"],
        "skipped_placeholder": stats["skipped_placeholder"],
        "unparsed": stats["unparsed"], "system": stats["system"],
        "parse_rate": round(rate, 2),
    }
    detail = (f"파싱률 {rate:.1f}% ({stats['parsed']}/{denom}), "
              f"시스템 {stats['system']}건, 자리표시자 제외 {stats['skipped_placeholder']}건")

    if rate < 95.0:
        log_event(STAGE, "failure", detail=detail, extra=extra)
        escalate(STAGE,
                 f"CSV 파싱률이 95% 미만이다 ({rate:.1f}%). 내보내기 형식이 바뀌었을 수 있다. "
                 f"미파싱 줄 예시: {stats['unparsed_sample_lines'][:10]}",
                 files=[str(in_path)])
    elif rate < 99.0:
        log_event(STAGE, "skip", detail=detail + f" / 미파싱 줄 예시 {stats['unparsed_sample_lines'][:10]}",
                  extra=extra)
    else:
        log_event(STAGE, "success", detail=detail, extra=extra)

    print(f"[parse_csv] {in_path} -> {out_path}: {detail}")
    return 0 if rate >= 95.0 else 2


if __name__ == "__main__":
    sys.exit(main())
