"""A1 (DB 경로): kakao-db-reader(A0)가 만든 jsonl 원시 레코드를 공통 정규화 스키마로 변환한다.

설계서 §2.4 A1. **미구현.** A0가 아직 실기기에서 검증되지 않았으므로(§6 O1) 이
스크립트는 실제 DB 레코드 필드를 본 적이 없다. db_access_reference.md 조사에
따르면 메시지 테이블은 `NTChatMessage`, 본문 컬럼은 `message`로 추정되지만
그 외 컬럼(타입 코드, 답글 참조, 메시지 ID, 발신자)은 미확인이다.

V1에서 A0가 실제 원시 레코드 샘플을 만들면, 아래 TYPE_CODE_MAP과
normalize_record()의 필드 매핑을 실물에 맞게 채운다. 지금은 구조와 인터페이스만
잡아 둔다 — A2(merge.py)는 이 스크립트의 출력 형식(NormalizedMessage jsonl)에만
의존하므로, 이 스크립트가 나중에 채워져도 병합 로직은 바뀌지 않는다.

성공 기준 (설계서 §2.4 A1): DB 경로는 매핑 실패 레코드 0건.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from lib.common.escalate import escalate
from lib.common.log import log_event
from lib.common.schema import NormalizedMessage, write_jsonl

# TODO(V1): 실제 카카오톡 메시지 타입 코드를 확인해 채운다.
# 알려진 값이 없으므로 지금은 빈 매핑 — 매핑에 없는 타입은 is_system=False로 두고
# 텍스트 그대로 보존한다(정보 손실보다 보수적 처리를 우선).
TYPE_CODE_MAP: dict[int, str] = {}


def normalize_record(raw: dict) -> NormalizedMessage:
    """A0가 만든 원시 jsonl 레코드 1건을 NormalizedMessage로 변환한다.

    raw의 정확한 키 이름은 V1에서 A0 구현과 함께 확정된다. 지금은
    db_access_reference.md에서 추정한 최소 필드셋(ts, sender, message, msg_id)을
    가정한 자리표시자다.
    """
    msg_type = raw.get("type")
    is_system = TYPE_CODE_MAP.get(msg_type) == "system"

    return NormalizedMessage(
        ts=datetime.fromisoformat(raw["ts"]),
        nickname=raw.get("sender"),
        text=raw.get("message", ""),
        is_system=is_system,
        src="db",
        links=[],  # TODO(V1): 실제 레코드에서 링크 추출 규칙 확인
        reply_to=raw.get("reply_to"),
        native_id=raw.get("msg_id"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="A1: kakao-db-reader jsonl 정규화 (미구현, 자리표시자)")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    escalate(
        "A1",
        "normalize_db.py는 아직 실제 DB 스키마로 검증되지 않았습니다 (O1 미완료). "
        "A0가 먼저 실기기에서 검증되어야 이 스크립트를 신뢰할 수 있습니다.",
        files=[str(args.input)],
    )
    log_event("A1", "failure", detail="normalize_db.py 미구현 (O1 대기)")
    sys.exit(1)


if __name__ == "__main__":
    main()
