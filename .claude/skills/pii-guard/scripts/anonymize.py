"""A6 (닉네임 처리): consent.yaml 모드에 따라 닉네임을 유지하거나 가명으로 치환한다.

설계서 §2.4 A6. mode: anon일 때만 치환한다. 시스템 메시지의 입장/퇴장 문구는
모드와 무관하게 항상 제거한다 — 이건 닉네임 노출 경로이므로 named 모드에서도
지운다(방 활동을 정리한 요약에 "누가 언제 들어왔다" 같은 부수 정보를 남기지
않는다는 취지, C2).
"""

from __future__ import annotations

import re
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SYSTEM_EVENT_RE = re.compile(
    r"[^\s,.!?]+님(?:이|을)\s*(?:들어왔습니다|나갔습니다|초대했습니다)\.?"
)


def _letter(n: int) -> str:
    """0->A, 1->B, ..., 25->Z, 26->AA, 27->AB, ... (엑셀 열 이름 방식)."""
    s = ""
    n += 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(ord("A") + r) + s
    return s


# 가명 접두어. 운영자 지정(2026-08-26): "참여자"보다 "함께한 선생님"이 이 방의
# 성격에 맞는다. 바꾸면 이미 발행된 호와 표기가 달라지므로 자주 바꾸지 않는다.
PSEUDONYM_PREFIX = "함께한 선생님"


def build_nickname_map(nicknames_in_order: list[str]) -> dict[str, str]:
    """첫 등장 순서대로 닉네임 -> "함께한 선생님A" 매핑을 만든다.

    같은 구간(period) 안에서는 항상 같은 순서로 호출해 동일한 매핑이 나오게
    한다 (호출자가 ts 기준으로 정렬된 리스트를 넘겨야 함).
    """
    seen: dict[str, str] = {}
    idx = 0
    for name in nicknames_in_order:
        if name and name not in seen:
            seen[name] = f"{PSEUDONYM_PREFIX}{_letter(idx)}"
            idx += 1

    # 별칭 등록: LLM이 닉네임을 줄여 쓰는 일이 잦다. "1000쌤(정보부장)"이 본문에는
    # "1000쌤"으로만 나와 사전에 없어 그대로 남은 적이 있다(2026-08-26).
    # 괄호 안 부연을 뗀 형태를 같은 가명으로 함께 등록한다.
    for name, pseudo in list(seen.items()):
        for alias in _aliases(name):
            if alias and alias not in seen:
                seen[alias] = pseudo
    return seen


_PAREN_SUFFIX_RE = re.compile(r"\s*[(\uff08][^)\uff09]*[)\uff09]\s*$")


def _aliases(name: str) -> list[str]:
    """닉네임의 짧은 변형들. 두 글자 미만이 되는 변형은 버린다(과잉 치환 위험)."""
    out = []
    base = _PAREN_SUFFIX_RE.sub("", name).strip()
    if base != name and len(base) >= 2:
        out.append(base)
    return out


# 한 글자 닉네임 'A' 때문에 "AI", "A1"이 전부 닉네임 등장으로 잡혀 항목 35건이
# 발행 보류된 적이 있다(2026-08-26). 그렇다고 낱말 경계를 한글까지 요구하면
# 한국어 조사가 바로 붙는 형태("민수와", "A님")를 하나도 못 잡는다.
#
# 그래서 **닉네임 가장자리 글자의 종류에 따라** 경계 조건을 건다:
#   - 가장자리가 영숫자면 그쪽에 영숫자가 붙는 것을 막는다 (A ≠ AI, A1)
#   - 가장자리가 한글이면 조건을 걸지 않는다 (민수 == "민수와"의 민수)
_ALNUM = "0-9A-Za-z"
_ALNUM_RE = re.compile(rf"[{_ALNUM}]")

# 알려진 한계: 한 글자 한글 닉네임("쌤")은 더 긴 낱말 안에서도 잡힌다. 과잉 치환은
# 발행물을 어색하게 만들 뿐 개인정보를 흘리지는 않으므로 이 방향의 오차를 택한다.


def nickname_pattern(name: str) -> "re.Pattern[str]":
    """닉네임 등장을 찾는 정규식. 치환과 재검사가 같은 기준을 쓰게 한다."""
    prefix = rf"(?<![{_ALNUM}])" if _ALNUM_RE.match(name[0]) else ""
    suffix = rf"(?![{_ALNUM}])" if _ALNUM_RE.match(name[-1]) else ""
    return re.compile(prefix + re.escape(name) + suffix)


_PSEUDONYM_PLACEHOLDER = "\x00"


def find_nicknames(text: str, nickname_map: dict[str, str]) -> list[str]:
    """text에 실제로 남아 있는 원본 닉네임 목록. 재검사(verify)가 쓴다.

    검사 전에 **가명을 먼저 가린다.** 가명 '참여자A'는 원본 닉네임 'A'를 문자열로
    포함하므로, 그냥 검사하면 제대로 치환된 텍스트가 '닉네임 잔존'으로 잡힌다.
    """
    scrubbed = text
    for pseudo in sorted(set(nickname_map.values()), key=len, reverse=True):
        scrubbed = scrubbed.replace(pseudo, _PSEUDONYM_PLACEHOLDER)
    # 치환 기준과 같게, 한 글자 닉네임은 문장 속 잔존으로 보지 않는다.
    return [
        name for name in nickname_map
        if name and len(name) >= 2 and nickname_pattern(name).search(scrubbed)
    ]


def anonymize_text(text: str, nickname_map: dict[str, str]) -> str:
    """text 안에 등장하는 알려진 닉네임을 전부 가명으로 치환한다.

    긴 닉네임부터 치환해야 한 닉네임이 다른 닉네임의 부분 문자열인 경우
    잘못 치환되는 것을 막을 수 있다. 여기에 더해 낱말 경계를 요구한다 —
    단순 substring 치환은 'A'라는 닉네임으로 "AI"를 "참여자XI"로 망가뜨린다.
    """
    # 필드 값 전체가 닉네임이면(owner_nickname 같은 경우) 길이와 무관하게 치환한다.
    stripped = text.strip()
    if stripped in nickname_map:
        return text.replace(stripped, nickname_map[stripped])

    for name in sorted(nickname_map, key=len, reverse=True):
        # 한 글자 닉네임을 문장 속에서 치환하면 멀쩡한 낱말을 깨뜨린다.
        # 위의 '필드 전체 일치' 경로로만 처리한다.
        if len(name) < 2:
            continue
        text = nickname_pattern(name).sub(nickname_map[name], text)
    return text


def remove_system_event_mentions(text: str) -> str:
    """"OOO님이 들어왔습니다." 같은 입장/퇴장 문구를 통째로 지운다 (모드 무관, 항상)."""
    return SYSTEM_EVENT_RE.sub("", text)
