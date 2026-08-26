"""Common normalized message schema (설계서 §2.4 A1, §3.1).

Both A0 경로(DB)와 A0' 경로(공식 내보내기 txt)의 결과물은 kakao-parser의
normalize_db.py / parse_txt.py를 거쳐 이 스키마로 수렴한다. A2(merge.py)는
이 모듈의 dedup_key()만으로 중복을 판단하므로, 두 파서는 반드시 동일한 방식으로
NormalizedMessage를 생성해야 한다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional

# "csv"는 Mac 카카오톡 26.7.0의 공식 "텍스트 파일로 저장" 산출물이다.
# 이름과 달리 실제 확장자는 .csv이며 Date,User,Message 3컬럼이다
# (2026-08-26 실기기 확인, .claude/skills/kakao-export/references/export_ui.md).
# txt는 구버전/타 플랫폼 내보내기 형식(형식 A/B)을 위해 남겨 둔다.
Source = Literal["db", "txt", "csv"]


@dataclass
class NormalizedMessage:
    ts: datetime
    nickname: Optional[str]
    text: str
    is_system: bool
    src: Source
    links: list[str] = field(default_factory=list)
    reply_to: Optional[str] = None
    native_id: Optional[str] = None

    def to_json_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return d

    @staticmethod
    def from_json_dict(d: dict[str, Any]) -> "NormalizedMessage":
        d = dict(d)
        d["ts"] = datetime.fromisoformat(d["ts"])
        return NormalizedMessage(**d)


def dedup_key(msg: NormalizedMessage) -> str:
    """메시지 병합(A2)에 쓰는 중복 판정 키 (설계서 §2.4 A2).

    native_id가 있으면 그것을 우선 사용한다. 없으면 ts를 초 단위로 정규화한 뒤
    ts+nickname+text를 해시한다 — DB→txt 폴백 전환 구간에서 두 경로의 레코드가
    같은 메시지를 서로 다른 정밀도의 ts로 담고 있어도 같은 키로 수렴하게 하기 위함.
    """
    if msg.native_id:
        return f"native:{msg.native_id}"
    ts_sec = msg.ts.replace(microsecond=0)
    raw = f"{ts_sec.isoformat()}|{msg.nickname or ''}|{msg.text}"
    return "hash:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def write_jsonl(messages: list[NormalizedMessage], path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps(m.to_json_dict(), ensure_ascii=False) + "\n")


def read_jsonl(path) -> list[NormalizedMessage]:
    messages = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            messages.append(NormalizedMessage.from_json_dict(json.loads(line)))
    return messages
