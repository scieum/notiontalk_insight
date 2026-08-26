"""A5 평가 프롬프트 조립. LLM을 호출하지 않는다 — 문자열만 만든다.

**A4의 프롬프트와 완전히 분리된 파일이다.** C9(생성과 평가 분리)는 "다른 호출"만이
아니라 "다른 프롬프트"를 요구한다. 여기서 A4의 prompt.py를 import해 재사용하면
평가자가 생성자의 지시문을 그대로 물려받아 같은 편향으로 채점하게 된다.

평가 4축 중 여기서 LLM이 보는 것은 3축이다:
- 충실성: 원문을 왜곡·과장하지 않았는가
- 근거 일치: source_message_ids가 실제로 그 내용을 지지하는가
- 공개 적절성: 민감 발언·저격·개인 사정이 섞이지 않았는가

나머지 1축(중복)은 임베딩 유사도라 스크립트가 계산한다 — LLM에게 물을 일이 아니다.
"""

from __future__ import annotations

EVAL_SCHEMA_SPEC = """\
{
  "items": [
    {
      "id": "F1",
      "faithful": true,
      "evidence_supported": true,
      "publish_safe": true,
      "reasons": {
        "faithful": "판단 근거를 한 문장으로",
        "evidence": "판단 근거를 한 문장으로",
        "publish": "판단 근거를 한 문장으로"
      }
    }
  ]
}"""


def _render_messages(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        if m.get("is_system"):
            continue
        nick = m.get("nickname") or "(알 수 없음)"
        lines.append(f"[{m['id']}] {m['ts'][11:16]} {nick}: {m['text']}")
    return "\n".join(lines)


def _render_item(item: dict, kind: str) -> str:
    ids = item.get("source_message_ids", [])
    head = f"### {item['id']}  ({kind})  근거 메시지 {ids}"
    if kind == "FAQ":
        body = f"- 질문: {item.get('question','')}\n- 답변: {item.get('answer','')}"
    elif kind == "팁":
        body = f"- 제목: {item.get('title','')}\n- 본문: {item.get('body','')}"
    else:
        body = f"- 내용: {item.get('text','')}"
    tip = item.get("practical_tip")
    if tip:
        body += f"\n- 실무 팁: {tip}"
    return f"{head}\n{body}"


def build_eval_prompt(
    groups: list[tuple[str, list[tuple[dict, str]], list[dict]]],
    consent_mode: str = "anon",
) -> str:
    """여러 스레드의 항목을 각자의 원문과 대조해 한 번에 채점하는 프롬프트.

    groups는 (thread_id, [(항목, 종류), ...], 그 스레드의 메시지들) 목록이다.
    스레드마다 호출하면 구간 하나에 수십 번을 부르게 되고, 무료 등급 할당량
    (모델당 하루 20요청)에 그대로 걸린다 — 2026-08-26 실측.
    """
    blocks = []
    for tid, items_with_kind, messages in groups:
        blocks.append(
            f"## 스레드 {tid}\n\n### 원문\n\n{_render_messages(messages)}\n\n"
            f"### 이 스레드에서 나온 항목\n\n"
            + "\n\n".join(_render_item(it, kind) for it, kind in items_with_kind)
        )
    rendered = "\n\n---\n\n".join(blocks)

    anon_note = (
        "이 오픈채팅방은 익명 발행 모드입니다. 닉네임 자체는 뒤 단계에서 가명으로 바뀌므로 "
        "닉네임이 적혀 있다는 이유만으로 부적절하다고 판정하지 마십시오. "
        "다만 닉네임을 지워도 누구인지 드러나는 내용(직장·소속·개인 사정)은 부적절입니다."
        if consent_mode == "anon"
        else "이 오픈채팅방은 닉네임을 그대로 발행하는 모드입니다. 특정 인물을 향한 부정적 언급은 더 엄격하게 봅니다."
    )

    return f"""당신은 커뮤니티에 공개될 글을 원문과 대조해 검수하는 사람입니다.
당신은 이 글을 쓰지 않았습니다. 고쳐 쓰지도 마십시오. **판정만** 합니다.

아래에 스레드별로 [원문]과 그 원문에서 뽑아낸 [항목]이 있습니다. 항목 하나하나를
자기 스레드의 원문과 대조해 세 가지를 판정하십시오.

## 판정 기준

1. **`faithful` (충실성)** — 원문에 있는 내용을 왜곡하거나 과장하지 않았는가.
   - 원문에서 "잘 안 되는 것 같다"고 한 것을 "불가능하다"로 단정했다면 false.
   - 원문에 없는 조건·수치·기능명을 덧붙였다면 false.
   - 요약하면서 표현이 달라진 것 자체는 문제가 아닙니다. 뜻이 달라졌는지만 봅니다.

2. **`evidence_supported` (근거 일치)** — 적힌 근거 메시지 번호가 실제로 그 내용을 담고 있는가.
   - 번호가 가리키는 메시지를 읽었을 때 항목 내용이 나오지 않으면 false.
   - 답변이라고 적힌 것이 실제로는 오픈채팅방에서 채택되지 않았다면(반박당했거나 무시됐다면) false.

3. **`publish_safe` (공개 적절성)** — 오픈채팅방 밖에 공개해도 되는가.
   - 특정인 저격·비난, 개인 사정(건강·가족·직장 갈등), 제3자 험담, 사적 연락처,
     내부 정보로 보이는 것이 섞였다면 false.
   - {anon_note}

## 태도

- 애매하면 **통과시키지 말고 false**로 두십시오. 여기서 놓친 것은 그대로 공개됩니다.
- 다만 "더 좋게 쓸 수 있었다"는 이유로 false를 주지는 마십시오. 판정 대상은 품질이 아니라
  왜곡·근거·안전 세 가지뿐입니다.
- `reasons`는 각 축마다 한 문장입니다. true인 경우에도 왜 그렇게 봤는지 적습니다.

## 출력 형식

설명이나 머리말 없이 **JSON만** 출력합니다. 코드 펜스도 쓰지 않습니다.
모든 스레드의 모든 항목 id를 빠짐없이 넣습니다.

{EVAL_SCHEMA_SPEC}

## 채점 대상

각 스레드의 항목은 **그 스레드의 원문하고만** 대조합니다. 다른 스레드의 원문을
근거로 삼지 마십시오.

{rendered}
"""
