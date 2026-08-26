"""A0' 검증: 내보내기 txt가 실제로 쓸 수 있는 산출물인지 확인한다.

설계서 §2.4 A0' "파일 존재·크기·최신 메시지 시각 확인". JXA 자동화(export.jxa)가
"완료" 신호를 줬더라도 실제 파일이 비었거나, 엉뚱한 방이 저장됐거나, 새 메시지가
하나도 없을 수 있다. A1(parse_txt)에 넘기기 전에 여기서 걸러 낸다.

이 스크립트는 UI를 건드리지 않으므로 export.jxa와 독립적으로 동작한다 —
운영자가 수동으로 내보내 /inbox/raw/에 넣은 파일(CLAUDE.md §8)도 같은 검증을 받는다.

판정:
  ok         - A1로 진행
  empty      - 파일은 있으나 메시지 0건. "주고받은 대화 내용이 없습니다" 상황.
               재시도해도 같으므로 재시도하지 않고 skip 처리한다.
  stale      - 최신 메시지가 --since 이전. 신규 구간이 없다는 뜻이라 역시 skip.
  suspect    - 파일이 너무 작거나 형식이 인식되지 않음. 에스컬레이션.
  missing    - 파일 없음. 호출자(A0')가 재시도 로직을 돈다.

형식 인식은 kakao-parser와 같은 기준을 쓴다 — 여기서 통과한 파일은 A1에서도
파싱돼야 한다.

인식 형식:
  CSV  - Mac 카톡 26.7.0 "텍스트 파일로 저장"의 실제 산출물 (Date,User,Message).
         2026-08-26 실기기 확인. **현재의 주 형식.**
  A/B  - 구버전/타 플랫폼 내보내기 txt. 실기기 미확인이나 폴백으로 남겨 둔다.

CSV를 먼저 시도하는 이유는 확장자가 .txt여도 내용이 CSV일 수 있기 때문이다 —
메뉴 이름이 "텍스트 파일로 저장"이라 파일명만으로는 판단할 수 없다.
"""

from __future__ import annotations

import argparse
import csv
import json
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
from lib.common.paths import INBOX_RAW_DIR

STAGE = "A0'"

# 파일이 이보다 작으면 헤더만 있고 대화가 없는 것으로 본다.
MIN_USABLE_BYTES = 64

# kakao-parser/parse_csv.py와 동일 기준.
CSV_HEADER = ["Date", "User", "Message"]
CSV_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# kakao-parser의 형식 A/B와 동일 기준 (parse_txt.py FORMAT_A_RE / FORMAT_B_MSG_RE).
FORMAT_A_RE = re.compile(
    r"^(?P<y>\d{4})\.\s*(?P<mo>\d{1,2})\.\s*(?P<d>\d{1,2})\.\s*"
    r"(?P<ap>오전|오후)\s*(?P<h>\d{1,2}):(?P<mi>\d{2}),\s*"
    r"(?P<name>[^:]+?)\s*:\s*(?P<text>.*)$"
)
FORMAT_B_DATE_RE = re.compile(r"(?P<y>\d{4})년\s*(?P<mo>\d{1,2})월\s*(?P<d>\d{1,2})일")
FORMAT_B_MSG_RE = re.compile(
    r"^\[(?P<name>.+?)\]\s*\[(?P<ap>오전|오후)\s*(?P<h>\d{1,2}):(?P<mi>\d{2})\]\s*(?P<text>.*)$"
)


def _to_24h(ap: str, h: int) -> int:
    h = h % 12
    return h + 12 if ap == "오후" else h


def looks_like_csv(path: Path) -> bool:
    """첫 줄이 내보내기 CSV 헤더인지 본다 (BOM 허용)."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            first = fh.readline()
    except OSError:
        return False
    return [c.strip() for c in next(csv.reader([first]), [])] == CSV_HEADER


def scan_csv(path: Path) -> dict:
    """CSV 경로 스캔. parse_csv.py와 같은 규칙으로 세되 본문은 보관하지 않는다."""
    count = 0
    latest: Optional[datetime] = None
    placeholders = 0

    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # 헤더
        for row in reader:
            if len(row) != 3:
                continue
            date_s = row[0].strip()
            if not date_s:
                # Date 없는 자리표시자(삭제·가림). 메시지로 세지 않는다.
                placeholders += 1
                continue
            try:
                ts = datetime.strptime(date_s, CSV_DATE_FMT)
            except ValueError:
                continue
            count += 1
            if latest is None or ts > latest:
                latest = ts

    return {"format": "CSV", "message_count": count,
            "latest_ts": latest.isoformat() if latest else None,
            "placeholder_count": placeholders}


def scan(path: Path) -> dict:
    """파일을 한 번 훑어 메시지 수·형식·최신 시각을 구한다.

    본문은 어디에도 보관하지 않는다 (C2) — 카운트와 타임스탬프만 들고 나온다.
    확장자가 아니라 **내용**으로 형식을 정한다.
    """
    if looks_like_csv(path):
        return scan_csv(path)

    fmt: Optional[str] = None
    count = 0
    latest: Optional[datetime] = None
    cur_date = None

    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue

            m = FORMAT_A_RE.match(stripped)
            if m:
                fmt = fmt or "A"
                count += 1
                ts = datetime(
                    int(m["y"]), int(m["mo"]), int(m["d"]),
                    _to_24h(m["ap"], int(m["h"])), int(m["mi"]),
                )
                if latest is None or ts > latest:
                    latest = ts
                continue

            m = FORMAT_B_MSG_RE.match(stripped)
            if m:
                fmt = fmt or "B"
                count += 1
                if cur_date is not None:
                    ts = datetime(
                        cur_date[0], cur_date[1], cur_date[2],
                        _to_24h(m["ap"], int(m["h"])), int(m["mi"]),
                    )
                    if latest is None or ts > latest:
                        latest = ts
                continue

            # 형식 B의 날짜 헤더는 메시지가 아니라 이후 라인의 날짜를 정한다.
            d = FORMAT_B_DATE_RE.search(stripped)
            if d and not FORMAT_A_RE.match(stripped):
                cur_date = (int(d["y"]), int(d["mo"]), int(d["d"]))

    return {"format": fmt, "message_count": count,
            "latest_ts": latest.isoformat() if latest else None}


def verify(path: Path, since: Optional[datetime] = None) -> dict:
    if not path.exists():
        return {"verdict": "missing", "reason": "파일 없음",
                "path": str(path), "message_count": 0,
                "latest_ts": None, "format": None, "size_bytes": 0}

    size = path.stat().st_size
    result = scan(path)
    result.update({"path": str(path), "size_bytes": size})

    if size < MIN_USABLE_BYTES or result["message_count"] == 0:
        if result["format"] is None and size >= MIN_USABLE_BYTES:
            result.update({"verdict": "suspect",
                           "reason": f"형식 미인식 (크기 {size}B, 인식된 메시지 0건)"})
        else:
            result.update({"verdict": "empty",
                           "reason": f"메시지 0건 (크기 {size}B)"})
        return result

    if since is not None and result["latest_ts"] is not None:
        if datetime.fromisoformat(result["latest_ts"]) <= since:
            result.update({"verdict": "stale",
                           "reason": f"최신 메시지({result['latest_ts']})가 "
                                     f"기준 시각({since.isoformat()}) 이후가 아님"})
            return result

    result.update({"verdict": "ok",
                   "reason": f"형식 {result['format']}, 메시지 {result['message_count']}건"})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="A0': 내보내기 txt 검증")
    parser.add_argument("input", nargs="?",
                        help="검증할 txt 경로. 생략하면 /inbox/raw/ 최신 파일")
    parser.add_argument("--since",
                        help="이 ISO 시각보다 새 메시지가 있어야 ok (보통 last_ts)")
    parser.add_argument("--json", action="store_true", help="결과를 JSON으로만 출력")
    args = parser.parse_args()

    if args.input:
        path = Path(args.input)
    else:
        candidates = sorted(INBOX_RAW_DIR.glob("*.txt"))
        if not candidates:
            print(f"[verify_export] /inbox/raw/에 txt 없음", file=sys.stderr)
            log_event(STAGE, "failure", detail="/inbox/raw/에 검증할 txt 없음")
            return 1
        path = candidates[-1]

    since = datetime.fromisoformat(args.since) if args.since else None
    result = verify(path, since)

    # 원문은 넣지 않는다 — 경로·건수·형식만 (C2, R4).
    extra = {k: result[k] for k in
             ("path", "size_bytes", "format", "message_count", "latest_ts")}

    verdict = result["verdict"]
    if verdict == "ok":
        log_event(STAGE, "success", detail=result["reason"], extra=extra)
    elif verdict in ("empty", "stale"):
        log_event(STAGE, "skip", detail=result["reason"], extra=extra)
    else:  # suspect, missing
        log_event(STAGE, "failure", detail=result["reason"], extra=extra)
        if verdict == "suspect":
            escalate(STAGE,
                     f"내보내기 파일 형식을 인식하지 못했다 ({result['reason']}). "
                     f"파서 형식 정의 갱신이 필요할 수 있다.",
                     files=[str(path)])

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"[verify_export] {verdict}: {result['reason']} — {path}")

    return 0 if verdict in ("ok", "empty", "stale") else 2


if __name__ == "__main__":
    sys.exit(main())
