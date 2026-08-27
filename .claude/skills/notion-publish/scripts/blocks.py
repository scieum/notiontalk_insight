"""final.json → 노션 블록 배열 변환 (A7).

**네트워크를 쓰지 않는다.** 순수 변환이라 토큰 없이 전부 테스트된다.
노션 DB 스키마와도 무관하다 — 이건 페이지 *본문*이고, 속성 매핑은
`publish_report.py`가 별도로 다룬다.

섹션 순서와 문구는 docs/report_format.md §2의 고정 뼈대를 그대로 따른다.
HTML 렌더러(scripts/render_report.py)와 같은 내용이 나와야 하며, 항목이 0건인
섹션도 없애지 않고 "이번 주에는 없었습니다"를 적는다.

노션 API 제약(2026-08-27 기준):
- rich_text 객체 하나당 content 2,000자
- 한 요청에 children 100개
둘 다 여기서 방어한다 — 넘치면 잘라내지 않고 나눈다.
"""

from __future__ import annotations

import re

MAX_TEXT = 2000
MAX_CHILDREN_PER_REQUEST = 100

OPENCHAT_URL = "https://open.kakao.com/o/gpSvPKGg"
HOMEPAGE_URL = "https://www.notiontalk.com/"

# report_format.md §2와 같은 뼈대. 렌더러의 SECTIONS와 문구를 맞춘다.
EMPTY_TEXT = {
    "topics": "이번 주에는 두드러진 주제가 없었습니다.",
    "solved": "이번 주에는 해결된 질문이 없었습니다.",
    "open": "이번 주에는 답을 기다리는 질문이 없었습니다.",
    "tips": "이번 주에는 공유된 팁이 없었습니다.",
    "actions": "이번 주에는 정해진 약속이 없었습니다.",
}

_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _chunks(text: str, size: int = MAX_TEXT):
    """긴 문자열을 노션 한도에 맞게 나눈다. 잘라 버리지 않고 전부 싣는다."""
    text = text or ""
    if len(text) <= size:
        return [text]
    return [text[i : i + size] for i in range(0, len(text), size)]


def rich(text: str, *, bold: bool = False, code: bool = False) -> list[dict]:
    """평문을 rich_text 배열로. 본문에 섞인 URL은 링크로 살린다."""
    out: list[dict] = []
    for piece in _chunks(text):
        pos = 0
        for m in _URL_RE.finditer(piece):
            if m.start() > pos:
                out.append(_text(piece[pos : m.start()], bold=bold, code=code))
            url = m.group(0).rstrip(".,);:")
            out.append(_text(url, link=url))
            pos = m.start() + len(url)
        if pos < len(piece):
            out.append(_text(piece[pos:], bold=bold, code=code))
    return out or [_text("")]


def _text(content: str, *, bold: bool = False, code: bool = False, link: str | None = None) -> dict:
    node: dict = {"type": "text", "text": {"content": content[:MAX_TEXT]}}
    if link:
        node["text"]["link"] = {"url": link}
    annotations = {}
    if bold:
        annotations["bold"] = True
    if code:
        annotations["code"] = True
    if annotations:
        node["annotations"] = annotations
    return node


def _block(kind: str, rich_text: list[dict], **extra) -> dict:
    body = {"rich_text": rich_text}
    body.update(extra)
    return {"object": "block", "type": kind, kind: body}


def heading(text: str, level: int = 2) -> dict:
    return _block(f"heading_{level}", rich(text))


def paragraph(text: str) -> dict:
    return _block("paragraph", rich(text))


def bullet(text: str) -> dict:
    return _block("bulleted_list_item", rich(text))


def numbered(text: str) -> dict:
    return _block("numbered_list_item", rich(text))


def callout(text: str, emoji: str = "💡") -> dict:
    return _block("callout", rich(text), icon={"emoji": emoji})


def quote(text: str) -> dict:
    return _block("quote", rich(text))


def divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _empty(section_key: str) -> list[dict]:
    return [quote(EMPTY_TEXT[section_key])]


def _tip_block(text) -> list[dict]:
    """항목 끝의 실무 팁 한 줄. 없으면 아무것도 넣지 않는다."""
    return [callout(text, "✏️")] if text else []


def build(data: dict, week_label: str, issue: int) -> list[dict]:
    """final.json 하나를 뉴스레터 페이지 본문 블록 배열로 만든다."""
    report = data.get("report") or {}
    sections = report.get("sections") or {}
    blocks: list[dict] = []

    # ── 머리말
    intro = report.get("intro")
    if intro:
        blocks.append(paragraph(intro))
    stats = sections.get("stats") or {}
    if stats:
        summary = " · ".join(f"{k} {v}" for k, v in stats.items())
        blocks.append(callout(summary, "📊"))
    blocks.append(divider())

    # ── 1. 이번 주 주요 주제
    blocks.append(heading("이번 주 주요 주제"))
    topics = sections.get("topics") or []
    if topics:
        for t in topics:
            blocks.append(numbered(t.get("topic", "")))
            why = t.get("why")
            if why:
                blocks.append(paragraph(why))
    else:
        blocks.extend(_empty("topics"))

    # ── 2. 해결된 질문
    blocks.append(heading("해결된 질문"))
    faq = data.get("faq") or []
    if faq:
        for f in faq:
            blocks.append(heading(f.get("question", ""), level=3))
            blocks.append(paragraph(f.get("answer", "")))
            blocks.extend(_tip_block(f.get("practical_tip")))
    else:
        blocks.extend(_empty("solved"))

    # ── 3. 아직 답이 없는 질문
    blocks.append(heading("아직 답이 없는 질문"))
    unresolved = sections.get("unresolved") or data.get("unresolved") or []
    if unresolved:
        blocks.append(paragraph("아시는 분은 오픈채팅방에 남겨주세요. 다음 주 리포트에 실립니다."))
        for u in unresolved:
            blocks.append(bullet(u.get("question", "")))
    else:
        blocks.extend(_empty("open"))

    # ── 4. 이번 주의 팁
    blocks.append(heading("이번 주의 팁"))
    tips = data.get("tips") or []
    if tips:
        by_tag: dict[str, list[dict]] = {}
        for t in tips:
            by_tag.setdefault((t.get("feature_tags") or ["기타"])[0], []).append(t)
        for tag, group in sorted(by_tag.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            blocks.append(heading(f"{tag} ({len(group)})", level=3))
            for t in group:
                blocks.append(paragraph(t.get("title", "")))
                body = t.get("body")
                if body:
                    blocks.append(bullet(body))
                blocks.extend(_tip_block(t.get("practical_tip")))
    else:
        blocks.extend(_empty("tips"))

    # ── 5. 하기로 한 것
    blocks.append(heading("하기로 한 것"))
    actions = data.get("actions") or []
    if actions:
        for a in actions:
            line = a.get("text", "")
            owner = a.get("owner_nickname")
            due = a.get("due")
            meta = " · ".join(x for x in (owner, due) if x)
            blocks.append(bullet(f"{line} ({meta})" if meta else line))
    else:
        blocks.extend(_empty("actions"))

    # ── 6. 함께하기 (매 호 동일. 가변값을 넣지 않는다 — report_format.md ⑧)
    blocks.append(divider())
    blocks.append(heading("선생님, 같이 노션 배워볼래요?"))
    blocks.append(paragraph("혼자 헤매면 오래 걸리는 일도 함께하면 금방 풀립니다."))
    blocks.append(_block("paragraph", [_text("노션하는 교사톡 들어가기", link=OPENCHAT_URL)]))
    blocks.append(_block("paragraph", [_text("노션톡 홈페이지 둘러보기", link=HOMEPAGE_URL)]))

    # ── 판권
    blocks.append(divider())
    blocks.append(paragraph(
        "이 리포트는 카카오톡 오픈채팅 대화에서 자동으로 추출·검증·익명화되었습니다. "
        "닉네임은 가명으로 바뀌었고, 연락처·이메일 등은 마스킹되었습니다."
    ))
    blocks.append(paragraph(f"{issue}호 · {week_label}"))
    return blocks


def batched(blocks: list[dict], size: int = MAX_CHILDREN_PER_REQUEST):
    """노션은 한 요청에 children 100개까지만 받는다. 나눠서 보낸다."""
    for i in range(0, len(blocks), size):
        yield blocks[i : i + size]
