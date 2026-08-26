"""A4 프롬프트 조립. LLM을 호출하지 않는다 — 문자열만 만든다.

두 종류를 만든다:
1. 스레드 배치 프롬프트 — 유형 분류 + FAQ/팁/액션 추출 (배치마다 1회)
2. 리포트 프롬프트 — 1번 결과를 모아 구간 리포트 생성 (구간마다 1회)

프롬프트를 스크립트로 분리해 둔 이유: 키 없이도 실제 프롬프트를 눈으로 확인할 수
있고, G-R 반려가 반복될 때 무엇을 고쳐야 하는지 한 파일만 보면 되기 때문이다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from lib.common.paths import DOCS_DIR

# report_format.md §4와 같은 내용이다. 문서가 바뀌면 여기도 같이 바꾼다.
TONE_RULES = """\
- 경어체(`~합니다`)를 쓰고 이모지는 쓰지 않습니다.
- 한 문장은 60자 이내로 씁니다. 두 가지를 말하려면 문장을 나눕니다.
- 대화 원문을 길게 그대로 인용하지 않습니다. 요약하되 왜곡하지 않습니다.
- 오픈채팅방 참여자를 평가하거나 훈계하지 않습니다.
- 단정할 근거가 없으면 추측해서 쓰지 말고 미해결로 분류합니다.
- 닉네임은 원문 그대로 씁니다(익명화는 뒤 단계에서 처리합니다).
- 대화가 오간 곳을 가리킬 때는 "방"이 아니라 **"오픈채팅방"**이라고 씁니다."""

THREAD_SCHEMA_SPEC = """\
{
  "threads": [
    {
      "thread_id": "T123",
      "types": ["질문응답"],
      "faq":  [ {"question": "", "answer": "", "tags": [], "source_message_ids": [1,2], "practical_tip": null} ],
      "tips": [ {"title": "", "body": "", "feature_tags": [], "source_message_ids": [3], "practical_tip": null} ],
      "actions": [ {"type": "decision", "text": "", "owner_nickname": null, "due": null, "source_message_ids": [4]} ],
      "unresolved": [ {"question": "", "source_message_ids": [5]} ]
    }
  ]
}"""

REPORT_SCHEMA_SPEC = """\
{
  "hook_title": "",
  "intro": "",
  "title": "",
  "sections": {
    "topics": [ {"topic": "", "why": "", "source_thread_ids": ["T1"]} ],
    "resolved": [ {"question": "", "answer": "", "source_message_ids": [1]} ],
    "unresolved": [ {"question": "", "source_message_ids": [2]} ],
    "decisions": [ {"text": "", "source_message_ids": [3]} ]
  },
  "source_thread_ids": ["T1", "T2"]
}"""


# 문서에 이 표시가 있으면 그 사이만 프롬프트에 넣는다. 없으면 전체를 정리해서 넣는다.
# 이유: room_profile.md 같은 문서에는 운영자·Claude용 주석("운영자가 채워 넣는다",
# "JXA가 채팅방을 고를 때...")이 섞여 있는데, 그걸 그대로 넣으면 추출 LLM이
# 자기 일과 무관한 지시를 받는다.
_PROMPT_BLOCK_RE = re.compile(
    r"^[^\S\n]*<!--\s*prompt:start\s*-->[^\S\n]*$(.*?)^[^\S\n]*<!--\s*prompt:end\s*-->",
    re.S | re.M,
)

_PLACEHOLDER_RE = re.compile(r"^[-*]\s*\((예|예시)[:：]")
_EMPTY_ROW_RE = re.compile(r"^\|[\s|]*\|$")
_SEPARATOR_ROW_RE = re.compile(r"^\|[\s|:-]+\|$")
# `- 대략적인 활동 참여자 수:` 처럼 콜론 뒤가 비어 있는 항목 = 운영자가 아직 안 채운 칸
_UNFILLED_FIELD_RE = re.compile(r"^[-*]\s*[^:：]+[:：]\s*$")


def _clean_doc(text: str) -> str:
    """문서를 프롬프트에 넣을 수 있게 정리한다.

    - 인용 주석(`>` 줄) 제거 — 사람에게 하는 말이지 LLM에게 하는 말이 아니다
    - 자리표시자(`- (예: ...)`) 와 빈 표 행 제거 — 운영자가 아직 안 채운 칸
    - 그 결과 내용이 비어버린 섹션 제목 제거 — 빈 제목만 남으면 LLM이 채우려 든다
    """
    kept: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(">"):
            continue
        if _PLACEHOLDER_RE.match(s):
            continue
        if _EMPTY_ROW_RE.match(s):
            continue
        if _UNFILLED_FIELD_RE.match(s):
            continue
        kept.append(line.rstrip())

    # 데이터 행이 하나도 없는 표(머리글 + 구분선만)는 통째로 버린다.
    def _is_row(idx: int) -> bool:
        return idx < len(kept) and kept[idx].strip().startswith("|")

    without_empty_tables: list[str] = []
    i = 0
    while i < len(kept):
        if (
            _is_row(i)
            and not _SEPARATOR_ROW_RE.match(kept[i].strip())
            and _is_row(i + 1)
            and _SEPARATOR_ROW_RE.match(kept[i + 1].strip())
            and not _is_row(i + 2)
        ):
            i += 2
            continue
        without_empty_tables.append(kept[i])
        i += 1
    kept = without_empty_tables

    # 내용이 남지 않은 섹션 제목 제거 — 빈 제목만 남으면 LLM이 그 칸을 채우려 든다
    pruned: list[str] = []
    i = 0
    while i < len(kept):
        line = kept[i]
        if line.startswith("#"):
            j = i + 1
            has_body = False
            while j < len(kept) and not kept[j].startswith("#"):
                if kept[j].strip():
                    has_body = True
                j += 1
            if not has_body:
                i = j
                continue
        pruned.append(line)
        i += 1

    out: list[str] = []
    for line in pruned:
        if not line.strip() and (not out or not out[-1].strip()):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _read_doc(name: str) -> str:
    path = DOCS_DIR / name
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    m = _PROMPT_BLOCK_RE.search(text)
    if m:
        text = m.group(1)
    return _clean_doc(text)


def _context_block(rejections: list[str] | None = None) -> str:
    """방 성격·태그 목록·직전 반려 사유. 비어 있는 문서는 통째로 생략한다."""
    parts = []

    room = _read_doc("room_profile.md")
    if room:
        parts.append("## 이 오픈채팅방에 대해\n\n" + room)

    taxonomy = _read_doc("notion_feature_taxonomy.md")
    if taxonomy:
        parts.append("## 팁 태그 목록 (feature_tags는 반드시 이 중에서 고릅니다)\n\n" + taxonomy)

    if rejections:
        listed = "\n".join(f"- {r}" for r in rejections)
        parts.append(
            "## 직전 리포트가 반려된 사유 (같은 문제를 반복하지 마십시오)\n\n" + listed
        )

    return "\n\n".join(parts)


def _render_thread(thread: dict) -> str:
    lines = [f"### {thread['thread_id']}  ({thread['start_ts']} ~ {thread['end_ts']})"]
    for m in thread["messages"]:
        if m["is_system"]:
            continue
        nick = m["nickname"] or "(알 수 없음)"
        lines.append(f"[{m['id']}] {m['ts'][11:16]} {nick}: {m['text']}")
    return "\n".join(lines)


def build_thread_batch_prompt(
    batch: list[dict],
    rejections: list[str] | None = None,
    schema_error: str | None = None,
) -> str:
    """스레드 배치 하나를 분류·추출하는 프롬프트.

    schema_error가 주어지면 재시도 프롬프트가 된다(설계서 A4 실패 처리:
    스키마 오류 시 오류 메시지를 주입해 최대 2회 재시도).
    """
    context = _context_block(rejections)
    conversations = "\n\n".join(_render_thread(t) for t in batch)

    retry_block = ""
    if schema_error:
        retry_block = f"""
## 직전 응답이 거부된 이유

{schema_error}

이번에는 위 문제를 고쳐서 다시 출력하십시오. 설명 문장 없이 JSON만 출력합니다.
"""

    return f"""당신은 카카오톡 오픈채팅방 대화에서 커뮤니티에 공유할 만한 인사이트를 뽑아내는 편집자입니다.

아래 대화는 스레드(하나의 주제 단위) 여러 개로 나뉘어 있습니다. 각 스레드를 읽고
유형을 분류한 뒤, 공유할 가치가 있는 항목만 뽑아 주십시오.

{context}

## 작업

각 스레드마다:

1. **유형 분류** — `질문응답` / `팁공유` / `논의결정` / `잡담` 중에서 고릅니다. 여러 개 가능합니다.
2. **`잡담`만 해당하는 스레드에서는 어떤 항목도 만들지 않습니다.** faq/tips/actions/unresolved를 모두 빈 배열로 둡니다.
3. `질문응답`이면: 질문과 **실제로 오픈채팅방에서 채택된 답변**을 faq로 뽑습니다.
   답이 안 나왔거나 결론이 없으면 faq가 아니라 **unresolved**에 넣습니다.
4. `팁공유`면: 노하우를 tips로 뽑고 feature_tags를 위 목록에서 고릅니다.
5. `논의결정`이면: 합의된 것·누가 무엇을 하기로 한 것을 actions로 뽑습니다.
   `type`은 결정이면 `decision`, 할 일이면 `action`입니다.
6. `practical_tip`은 그 항목을 읽은 사람이 바로 써먹을 수 있는 한 줄입니다(60자 이내).
   억지로 만들지 말고, 덧붙일 말이 없으면 `null`로 두십시오.

## 반드시 지킬 것

- **모든 항목에 `source_message_ids`를 1개 이상 넣습니다.** 대화에 근거가 없는 항목은 만들지 않습니다.
- `source_message_ids`에는 아래 대화에 실제로 나온 `[숫자]` 메시지 번호만 씁니다. 번호를 지어내지 마십시오.
- 대화에 없는 내용을 채워 넣지 않습니다. 뽑을 것이 없는 스레드는 빈 배열로 둡니다.
- 아래 스레드 전부를 `threads` 배열에 넣습니다(항목이 없어도 thread_id와 types는 넣습니다).

## 문체

{TONE_RULES}

## 출력 형식

설명이나 머리말 없이 **JSON만** 출력합니다. 코드 펜스도 쓰지 않습니다.

{THREAD_SCHEMA_SPEC}
{retry_block}
## 대화

{conversations}
"""


def build_report_prompt(
    items: dict,
    stats: dict,
    period_label: str,
    rejections: list[str] | None = None,
    schema_error: str | None = None,
) -> str:
    """배치 결과를 모아 주간 리포트를 쓰는 프롬프트.

    items에는 원문 대화가 아니라 1단계에서 추출된 항목만 들어간다 — 리포트는
    이미 근거가 확인된 항목 위에서 쓰는 것이지 원문을 다시 해석하는 단계가 아니다.
    """
    context = _context_block(rejections)

    retry_block = ""
    if schema_error:
        retry_block = f"""
## 직전 응답이 거부된 이유

{schema_error}

이번에는 위 문제를 고쳐서 다시 출력하십시오. 설명 문장 없이 JSON만 출력합니다.
"""

    return f"""당신은 커뮤니티 리포트를 쓰는 편집자입니다.

아래는 이번 주({period_label})의 대화에서 이미 뽑아낸 항목들입니다. 이것을 바탕으로
주간 리포트를 작성하십시오. **여기 없는 내용을 새로 지어내지 마십시오.**

{context}

## 작성 지침

- `hook_title`: 이번 주에 가장 많이 다뤄졌거나 가장 막혔던 주제를 **질문형**으로 씁니다.
  40자 이내. 오픈채팅방 이름이나 날짜는 넣지 않습니다.
  (예시 형태: "필터가 걸어놓은 대로 안 걸리는 이유는 뭘까요?")
- `intro`: 이번 주가 어떤 한 주였는지 2~3문장, 120자 내외.
  **'구간'이라는 말은 쓰지 않습니다. '이번 주'라고 씁니다.**
- `title`: 노션 페이지 제목으로 쓸 사무적인 제목입니다(후킹하지 않습니다).
- `sections.topics`: 상위 3~5개. 각 항목은 주제 한 줄 + 왜 화제였는지 한 줄.
  메시지 수·참여자 수·미해결 여부를 근거로 고릅니다.
- `sections.resolved`: 아래 faq 항목에서 가져옵니다. 없으면 빈 배열.
- `sections.unresolved`: 아래 unresolved 항목에서 가져옵니다. 다음 주 참여를 부르는 자리입니다.
- `sections.decisions`: 아래 actions에서 가져옵니다. 없으면 빈 배열.
- 모든 항목은 아래 데이터에 있는 `source_message_ids` / `thread_id`를 그대로 옮겨 담습니다.

## 문체

{TONE_RULES}

## 출력 형식

설명이나 머리말 없이 **JSON만** 출력합니다. 코드 펜스도 쓰지 않습니다.
`stats`는 스크립트가 채우므로 출력에 넣지 않습니다.

{REPORT_SCHEMA_SPEC}
{retry_block}
## 이번 주 숫자

{json.dumps(stats, ensure_ascii=False, indent=2)}

## 추출된 항목

{json.dumps(items, ensure_ascii=False, indent=2)}
"""
