"""final.json을 사람이 읽는 주간 리포트 HTML로 렌더링한다 (A7 발행 전 미리보기).

docs/report_format.md가 정한 섹션 순서·톤을 따른다. 노션 발행(A7)이 구현되기
전까지 운영자가 G-R 게이트에서 검토할 화면이 이것이다.

설계 원칙 셋:

1. **뼈대는 매 호 고정이다.** 섹션 목록·순서·라벨·설명문이 항상 같다. 이번 주에
   해당 항목이 0건이면 섹션을 없애는 게 아니라 "이번 주에는 없었습니다"를 적는다.
   독자가 매주 같은 자리에서 같은 것을 찾을 수 있어야 하고, 0건이라는 사실 자체가
   정보이기 때문이다.
2. **글꼴은 Pretendard 하나다.** 위계는 서체를 바꿔서가 아니라 굵기(가변축 200~800)로
   만든다. Pretendard는 Google Fonts에 없고 아티팩트 CSP가 외부 호스트를 막으므로
   **이 리포트에 실제로 쓰인 글자만 서브셋해서 data URI로 심는다** — 전체 2.0MB가
   실제 사용분만 남기면 100KB 안팎이 된다.
3. **이 파일은 발행이 아니다.** 상태는 항상 `초안`으로 찍힌다 — 리포트를 사람 검토
   없이 발행 상태로 만드는 경로는 만들지 않는다(C5).
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.common.paths import DOCS_DIR, INSIGHTS_DIR, MESSAGES_DB_PATH, OUTPUT_DIR

_CODE_RE = re.compile(r"`([^`]+)`")
_URL_RE = re.compile(r"(https?://[^\s<>`\"']+)")
# URL 끝에 붙은 문장부호·조사는 주소가 아니다. "(https://...)를 참고" 같은 문장에서
# 닫는 괄호와 조사까지 링크로 먹으면 깨진 주소가 발행된다. 다만 한글을 통째로
# 떼면 경로에 한글이 든 주소(위키백과 등)가 망가지므로 **조사 목록**으로만 자른다.
_PARTICLES = (
    "으로부터", "에게서", "에서부터", "이라고", "라고", "으로써", "로써", "으로서", "로서",
    "까지", "부터", "에서", "에게", "처럼", "보다", "라도", "이나", "이란", "이라", "으로",
    "만큼", "조차", "마저", "밖에",
    "를", "을", "은", "는", "이", "가", "의", "에", "로", "와", "과", "도", "만", "나", "요",
)
_TRAILING_PUNCT = ".,;:!?\u2026'\"』」”’"
# 알려진 한계: 경로가 한글로 끝나는 주소 뒤에 조사가 붙으면(".../도움말을") 조사를
# 떼지 못한다. 반대 방향 오류(멀쩡한 한글 경로를 자르는 것)가 더 나쁘다고 봤다.

_TAG_RE = re.compile(r"<[^>]+>")


# 방 로고. **현재 렌더링하지 않는다** (운영자 요청, 2026-08-26 "일단 빼줘").
# 다시 넣으려면 머리말 .kicker와 판권 .sec-label에 {LOGO_SVG}를 끼우면 된다.
# 외부 이미지는 아티팩트 CSP가 막으므로 인라인 SVG로 그려둔 것이다.
LOGO_SVG = """<svg class="logo" viewBox="0 0 100 100" role="img" aria-label="노션 블록 로고">
  <g transform="translate(2 2)">
    <rect x="4" y="4" width="88" height="88" rx="17" fill="#F0F0F0"/>
    <rect x="9" y="9" width="78" height="78" rx="13" fill="#3E3563"/>
    <rect x="14" y="14" width="68" height="68" rx="9" fill="#7681C6"/>
    <path d="M22 22.5 L74 20 a2 2 0 0 1 1.6 3.4 L70 29 a4 4 0 0 1-2.6 1.1 L25 32 a3 3 0 0 1-3-3z" fill="#F0F0F0"/>
    <rect x="27" y="35" width="48" height="42" rx="5" fill="#F0F0F0"/>
    <text x="51" y="70" text-anchor="middle" fill="#7681C6"
          font-family="ui-serif, Georgia, 'Times New Roman', serif"
          font-size="42" font-weight="600">N</text>
  </g>
</svg>"""

# 매 호 고정되는 섹션 뼈대. (id, 영문 라벨, 한글 제목, 설명문, 0건일 때 문구)
SECTIONS = [
    ("topics", "This Week", "이번 주 주요 주제",
     "이번 주 오픈채팅방에서 가장 많이 오간 이야기입니다.",
     "이번 주에는 두드러진 주제가 없었습니다."),
    ("solved", "Solved", "해결된 질문",
     "질문과, 오픈채팅방에서 실제로 채택된 답을 옮겼습니다.",
     "이번 주에는 해결된 질문이 없었습니다."),
    ("open", "Open", "아직 답이 없는 질문",
     "답이 달리지 않은 질문입니다. 아시는 분은 오픈채팅방에 남겨주세요.",
     "이번 주에는 답을 기다리는 질문이 없었습니다."),
    ("tips", "Tips", "이번 주의 팁",
     "누군가 공유한 노하우를 기능별로 묶었습니다.",
     "이번 주에는 공유된 팁이 없었습니다."),
    ("actions", "Actions", "하기로 한 것",
     "오픈채팅방에서 누가 무엇을 하기로 했는지 남겨둡니다.",
     "이번 주에는 정해진 약속이 없었습니다."),
    ("involved", "Get Involved", "선생님, 노션 같이 배워요",
     "다음 주 리포트는 이번 주 대화로 만들어집니다.", ""),
]


def esc(text: str) -> str:
    """이스케이프한 뒤 백틱은 <code>로, 링크는 <a>로 바꾼다."""
    out = html.escape(text or "")
    out = _CODE_RE.sub(r"<code>\1</code>", out)

    def _link(m):
        url, tail = m.group(1), ""
        while url:
            for particle in _PARTICLES:          # 조사 (긴 것부터)
                if url.endswith(particle) and len(url) > len(particle):
                    # 조사 앞이 한글이면 주소의 일부일 수 있어 건드리지 않는다
                    # (".../한글경로"의 '로'). 영문·숫자·기호 뒤의 한글은 조사로 본다.
                    before = url[-len(particle) - 1]
                    if "가" <= before <= "힣":
                        continue
                    url, tail = url[: -len(particle)], particle + tail
                    break
            else:
                if url[-1] in _TRAILING_PUNCT:   # 문장부호
                    url, tail = url[:-1], url[-1] + tail
                    continue
                # 닫는 괄호는 짝이 안 맞을 때만 문장 부호로 본다
                if url[-1] in ")]}" and url.count(url[-1]) > url.count({")": "(", "]": "[", "}": "{"}[url[-1]]):
                    url, tail = url[:-1], url[-1] + tail
                    continue
                break
        shown = url if len(url) <= 52 else url[:49] + "…"
        return f'<a href="{url}" target="_blank" rel="noopener">{shown}</a>' + html.escape(tail)

    return _URL_RE.sub(_link, out)


def room_name() -> str:
    path = DOCS_DIR / "room_profile.md"
    if path.exists():
        m = re.search(r"^- 방 이름:\s*(.+)$", path.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1).strip().strip("*").strip()
    return "커뮤니티"


def issue_number(period_id: str) -> int:
    """몇 호인가. 지금까지 만들어진 final.json을 세되, 이번 호를 포함해 1부터 센다."""
    finals = sorted(p.name for p in INSIGHTS_DIR.glob("period_*.final.json"))
    target = f"period_{period_id}.final.json"
    return (finals.index(target) + 1) if target in finals else len(finals) + 1


REPORTS_DIR = OUTPUT_DIR / "reports"

# 참여 링크. 매 호 같은 자리에 같은 문구로 나가는 고정 요소다.
OPENCHAT_URL = "https://open.kakao.com/o/gpSvPKGg"
HOMEPAGE_URL = "https://www.notiontalk.com/"


def _yymmdd(iso_date: str) -> str:
    """ISO 날짜 -> yymmdd 6자 (파일명용)."""
    d = iso_date.replace("-", "")
    return d[2:8] if len(d) >= 8 else "000000"


def _display(iso_date: str) -> str:
    """ISO 날짜 -> 2026.08.19 (화면용). 매 호 같은 형식으로 찍는다."""
    return iso_date.replace("-", ".") if len(iso_date) == 10 else iso_date


def data_range(period_id: str, since: str | None, until: str | None) -> tuple[str, str]:
    """이 호가 실제로 다룬 기간을 ISO 날짜 두 개로 확정한다.

    열린 범위("~ 오늘")를 그대로 두면 나중에 어느 기간의 리포트인지 알 수 없다.
    실제 메시지의 처음·마지막 시각으로 닫아준다. 화면 표기와 보관 파일명이 같은
    값을 쓰게 해서 둘이 어긋나지 않도록 한다.
    """
    import sqlite3

    lo = hi = None
    if MESSAGES_DB_PATH.exists():
        conn = sqlite3.connect(f"file:{MESSAGES_DB_PATH}?mode=ro", uri=True)
        try:
            sql = "SELECT MIN(ts), MAX(ts) FROM messages WHERE period_id = ?"
            params: list = [period_id]
            if since:
                sql += " AND ts >= ?"
                params.append(since)
            if until:
                sql += " AND ts < ?"
                params.append(until)
            lo, hi = conn.execute(sql, params).fetchone()
        except sqlite3.DatabaseError:
            pass
        finally:
            conn.close()

    start = (since or lo or "")[:10]
    end = (until or hi or lo or since or "")[:10]
    return start, end


def archive_path(issue: int, start_iso: str, end_iso: str) -> Path:
    """보관 파일명: `1호_260819~260826.html`. 호수와 기간만으로 정렬·식별된다."""
    return REPORTS_DIR / f"{issue}호_{_yymmdd(start_iso)}~{_yymmdd(end_iso)}.html"


def _tip(text) -> str:
    if not text:
        return ""
    return (f'<p class="tip"><span class="tip-label">이렇게 써보세요</span>'
            f'<mark>{esc(text)}</mark></p>')


def _empty(msg: str) -> str:
    return f'<p class="empty">{esc(msg)}</p>' if msg else ""


def _section(sid: str, label: str, ko: str, desc: str, inner: str, count=None) -> str:
    badge = f'<span class="sec-count">{count}</span>' if count else ""
    return f"""<section id="{sid}">
  <div class="sec-head">
    <p class="sec-label">{esc(label)}{badge}</p>
    <h2>{esc(ko)}</h2>
    <p class="sec-desc">{esc(desc)}</p>
  </div>
  <div class="sec-body">{inner}</div>
</section>"""


def render_body(data: dict) -> str:
    report = data.get("report") or {}
    sections = report.get("sections") or {}
    blocks: list[str] = []

    # 1. 주요 주제
    topics = sections.get("topics") or []
    inner = "".join(
        # h3와 p를 감싸는 div가 반드시 있어야 한다. 없으면 li의 그리드 항목이
        # ::before/h3/p 셋이 되어 p가 번호 칸(좁은 열)으로 밀리고, 본문이 한 단어씩
        # 줄바꿈된다(2026-08-26에 실제로 그렇게 깨졌다).
        f'<li><span class="t-num" aria-hidden="true"></span>'
        f'<div class="t-body"><h3>{esc(t.get("topic",""))}</h3>'
        f'<p>{esc(t.get("why",""))}</p></div></li>'
        for t in topics
    )
    inner = f'<ol class="topics">{inner}</ol>' if topics else _empty(SECTIONS[0][4])
    blocks.append(_section(*SECTIONS[0][:4], inner, len(topics) or None))

    # 2. 해결된 질문
    faq = data.get("faq") or []
    inner = "".join(
        f'<article class="qa"><h3 class="q">{esc(f.get("question",""))}</h3>'
        f'<div class="a">{esc(f.get("answer",""))}</div>{_tip(f.get("practical_tip"))}'
        + (
            '<p class="chips">'
            + "".join(f'<span class="chip">{esc(t)}</span>' for t in (f.get("tags") or []))
            + "</p>"
            if f.get("tags") else ""
        )
        + "</article>"
        for f in faq
    )
    inner = f'<div class="qa-list">{inner}</div>' if faq else _empty(SECTIONS[1][4])
    blocks.append(_section(*SECTIONS[1][:4], inner, len(faq) or None))

    # 3. 미해결
    unresolved = sections.get("unresolved") or data.get("unresolved") or []
    inner = "".join(f'<li>{esc(u.get("question",""))}</li>' for u in unresolved)
    inner = f'<ul class="open-list">{inner}</ul>' if unresolved else _empty(SECTIONS[2][4])
    blocks.append(_section(*SECTIONS[2][:4], inner, len(unresolved) or None))

    # 4. 팁 (기능 태그별)
    tips = data.get("tips") or []
    if tips:
        by_tag: dict[str, list[dict]] = {}
        for t in tips:
            by_tag.setdefault((t.get("feature_tags") or ["기타"])[0], []).append(t)
        groups = []
        for tag, group in sorted(by_tag.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            cards = "".join(
                f'<article class="tip-card"><h3>{esc(t.get("title",""))}</h3>'
                f'<p>{esc(t.get("body",""))}</p>{_tip(t.get("practical_tip"))}</article>'
                for t in group
            )
            groups.append(
                f'<div class="tip-group"><h3 class="group-head">{esc(tag)}'
                f'<span class="group-count">{len(group)}</span></h3>'
                f'<div class="tip-grid">{cards}</div></div>'
            )
        inner = "".join(groups)
    else:
        inner = _empty(SECTIONS[3][4])
    blocks.append(_section(*SECTIONS[3][:4], inner, len(tips) or None))

    # 5. 액션
    actions = data.get("actions") or []
    inner = "".join(
        f'<li><p class="act">{esc(a.get("text",""))}</p><p class="meta">'
        f'<span>{esc(a.get("owner_nickname") or "미지정")}</span>'
        + (f'<span class="due">{esc(a.get("due"))}</span>' if a.get("due") else "")
        + "</p></li>"
        for a in actions
    )
    inner = f'<ul class="act-list">{inner}</ul>' if actions else _empty(SECTIONS[4][4])
    blocks.append(_section(*SECTIONS[4][:4], inner, len(actions) or None))

    # 6. 함께하기 — 대화에서 생성하지 않는 유일한 섹션. 매 호 같은 자리·같은 문구다.
    n_open = len(unresolved)
    open_line = (
        f"이번 주에 아직 답을 못 찾은 질문이 {n_open}개 있어요. "
        "아시는 게 있으면 한 줄만 남겨주셔도 큰 도움이 됩니다."
        if n_open else
        "궁금한 게 생기면 편하게 물어보세요. 같이 찾아보면 금방입니다."
    )
    _id, _label, _ko, _desc = SECTIONS[5][:4]
    blocks.append(f"""<section id="{_id}" class="cta">
  <p class="sec-label">{esc(_label)}</p>
  <h2>{esc(_ko)}</h2>
  <p class="cta-lede">혼자 헤매면 오래 걸리는 일도 함께하면 금방 풀립니다.<br>
     {open_line}</p>
  <div class="cta-buttons">
    <a class="btn btn-primary" href="{OPENCHAT_URL}" target="_blank" rel="noopener">
      노션하는 교사톡 들어가기</a>
    <a class="btn btn-ghost" href="{HOMEPAGE_URL}" target="_blank" rel="noopener">
      노션톡 홈페이지 둘러보기</a>
  </div>
  <p class="cta-foot">근거가 필요한 질문은 노션 질문함에 올려주세요. 공식 문서와 오픈채팅방 대화를 함께 찾아 답을 드립니다.</p>
</section>""")
    return "\n".join(blocks)


# 용어 통일 보정. A4 프롬프트는 이제 '주간'으로 쓰게 되어 있지만, 그 전에 만들어진
# draft에는 '구간'이 남아 있다. 표기만 바꾸는 것이라 내용은 건드리지 않는다.
_WEEK_TERMS = (("이번 구간", "이번 주"), ("지난 구간", "지난 주"), ("구간", "주간"),
               ("참여자 수", "함께한 선생님"))


def _to_week(text: str) -> str:
    for a, b in _WEEK_TERMS:
        text = text.replace(a, b)
    return text


def render(data: dict, week_label: str, issue: int) -> str:
    report = data.get("report") or {}
    stats = (report.get("sections") or {}).get("stats") or {}

    cells = "".join(
        f'<div class="stat"><span class="num">{esc(str(v))}</span>'
        f'<span class="lbl">{esc(_to_week(str(k)))}</span></div>'
        for k, v in stats.items()
    )

    head = f"""<header class="masthead">
  <p class="kicker">
    <span class="room">{esc(room_name())}</span>
    <span class="issue">{issue}호</span>
    <span class="period">{esc(week_label)}</span>
  </p>
  <h1>{esc(_to_week(report.get('hook_title', '')))}</h1>
  <p class="intro">{esc(_to_week(report.get('intro', '')))}</p>
  <p class="status"><span class="badge">초안</span>
     <span class="badge badge-quiet">자동생성</span>
     <span class="status-note">운영자 검토 전이며, 이 상태로는 발행되지 않습니다.</span></p>
  <div class="stats">{cells}</div>
</header>"""
    return head + "\n" + render_body(data)


TEMPLATE = """<title>{title}</title>
<style>
@font-face {{
  font-family: "Pretendard";
  src: url("data:font/woff2;base64,{font_b64}") format("woff2-variations");
  font-weight: 200 800;
  font-style: normal;
  font-display: swap;
}}
{css}
</style>
<div class="sheet">
{content}
<footer class="colophon">
  <p class="sec-label">Colophon</p>
  <p class="cline">이 리포트는 카카오톡 오픈채팅 대화에서 자동으로 추출·검증·익명화되었습니다.
     닉네임은 가명으로 바뀌었고, 연락처·이메일 등은 마스킹되었습니다.</p>
  <dl class="pipe">
    <div><dt>추출</dt><dd>스레드 {n_threads}개 · 분류 {n_batches}회</dd></div>
    <div><dt>검증</dt><dd>통과 {n_pass} · 폐기 {n_discard} · 공개보류 {n_esc}</dd></div>
    <div><dt>익명화</dt><dd>가명 {n_nick}명 (anon 모드)</dd></div>
  </dl>
  <p class="cline quiet">발행 전 사람 검토가 필요합니다. 리포트는 자동 발행되지 않습니다.</p>
</footer>
</div>
"""

CSS = """
/* 팔레트 — huddling.ai AI Report 기조. 거의 검정에 가까운 차가운 회보라 바탕에
   파랑 하나로 강조한다. **다크가 기본 정체성**이라 :root에 다크를 두고 밝은 테마를
   따로 얹는다(테마 3상태 모두 토큰만 재정의). */
:root {
  --bg:        #050510;
  --surface:   #14141F;
  --surface-2: #1B1B26;
  --tint:      #1B2440;   /* 파랑이 섞인 강조 면 — 실무 팁 패널 */
  --border:    #2A2A38;
  --border-soft:#1F1F2B;
  --text:      #E8E8EE;
  --text-2:    #B9B9C4;
  --text-3:    #8B8B98;
  --accent:    #7AA2FF;
  --accent-2:  #0058E0;
  --on-accent: #050510;
  --shadow:    0 1px 2px rgba(0,0,0,.5), 0 12px 32px -20px rgba(0,0,0,.9);
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) {
    --bg: #FFFFFF; --surface: #F7F7FA; --surface-2: #F2F2F7; --tint: #EDF2FF;
    --border: #DCDCE6; --border-soft: #E8E8EE;
    --text: #14141F; --text-2: #4A4A5A; --text-3: #7C7C8A;
    --accent: #0058E0; --accent-2: #0058E0; --on-accent: #FFFFFF;
    --shadow: 0 1px 2px rgba(20,20,31,.05), 0 10px 28px -20px rgba(20,20,31,.4);
  }
}
:root[data-theme="light"] {
  --bg: #FFFFFF; --surface: #F7F7FA; --surface-2: #F2F2F7; --tint: #EDF2FF;
  --border: #DCDCE6; --border-soft: #E8E8EE;
  --text: #14141F; --text-2: #4A4A5A; --text-3: #7C7C8A;
  --accent: #0058E0; --accent-2: #0058E0; --on-accent: #FFFFFF;
  --shadow: 0 1px 2px rgba(20,20,31,.05), 0 10px 28px -20px rgba(20,20,31,.4);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "Pretendard", "Pretendard Variable", -apple-system, BlinkMacSystemFont,
               "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif;
  font-weight: 400;
  font-size: 16px;
  line-height: 1.7;
  word-break: keep-all;
  overflow-wrap: anywhere;
  -webkit-font-smoothing: antialiased;
}
.sheet { max-width: 52rem; margin: 0 auto; padding: clamp(1.5rem,4vw,4.5rem) clamp(1rem,4vw,2.5rem) 5rem; }
a { color: var(--accent); text-underline-offset: 2px; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; border-radius: 4px; }
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: .85em; background: var(--surface-2); color: var(--text-2);
  padding: .12em .4em; border-radius: 4px; word-break: break-all;
}
strong { font-weight: 600; color: var(--text); }

/* ── 머리말 */
.masthead { padding-bottom: 2.25rem; border-bottom: 1px solid var(--border); }
.kicker {
  margin: 0 0 1.5rem; display: flex; align-items: center; gap: .55rem; flex-wrap: wrap;
  font-size: .8125rem; color: var(--text-3);
}
.kicker .room { font-weight: 600; color: var(--text-2); }
.kicker .issue {
  font-weight: 600; color: var(--on-accent); background: var(--accent);
  padding: .1rem .5rem; border-radius: 999px; font-variant-numeric: tabular-nums;
  font-size: .75rem; letter-spacing: .01em;
}
.kicker .period { font-variant-numeric: tabular-nums; }
h1 {
  margin: 0 0 1.1rem; font-weight: 700; letter-spacing: -.033em;
  font-size: clamp(1.9rem, 5.4vw, 3rem); line-height: 1.25; text-wrap: balance;
}
.intro { margin: 0 0 1.75rem; font-size: 1.0625rem; color: var(--text-2); max-width: 36rem; }
.status { margin: 0 0 2rem; display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; font-size: .8125rem; }
.status-note { color: var(--text-3); }
.badge {
  font-size: .6875rem; font-weight: 600; letter-spacing: .04em;
  padding: .2rem .55rem; border-radius: 999px;
  background: var(--tint); color: var(--accent); border: 1px solid var(--border);
}
.badge-quiet { background: transparent; color: var(--text-3); }
.stats {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(6rem, 1fr));
  gap: 1px; background: var(--border-soft); border: 1px solid var(--border-soft);
  border-radius: 10px; overflow: hidden;
}
.stat { display: flex; flex-direction: column; gap: .1rem; padding: .9rem 1rem; background: var(--surface); }
.num { font-size: 1.5rem; font-weight: 600; line-height: 1.15; letter-spacing: -.03em; font-variant-numeric: tabular-nums; }
.lbl { font-size: .75rem; color: var(--text-3); }

/* ── 섹션 (매 호 같은 뼈대) */
section { padding: 3rem 0; border-bottom: 1px solid var(--border-soft); }
section:last-of-type { border-bottom: none; }
.sec-head { margin-bottom: 1.5rem; }
.sec-label {
  margin: 0 0 .6rem; font-size: .6875rem; font-weight: 600;
  letter-spacing: .18em; text-transform: uppercase; color: var(--accent);
  display: flex; align-items: center; gap: .5rem;
}
.sec-count {
  font-weight: 500; letter-spacing: 0; color: var(--text-3);
  background: var(--surface-2); padding: .1rem .45rem; border-radius: 999px;
  font-variant-numeric: tabular-nums;
}
h2 { margin: 0 0 .45rem; font-size: clamp(1.3rem, 3vw, 1.6rem); font-weight: 700; letter-spacing: -.028em; }
.sec-desc { margin: 0; font-size: .9375rem; color: var(--text-3); max-width: 34rem; }
.sec-body { display: flex; flex-direction: column; gap: 1.25rem; }
.empty {
  margin: 0; padding: 1.5rem; text-align: center; font-size: .875rem;
  color: var(--text-3); background: var(--surface);
  border: 1px dashed var(--border); border-radius: 10px;
}

/* ── 주제 */
.topics { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: .75rem; counter-reset: t; }
.topics li {
  counter-increment: t; display: grid; grid-template-columns: 1.9rem minmax(0,1fr); gap: .9rem;
  background: var(--surface); border: 1px solid var(--border-soft);
  border-radius: 10px; padding: 1.05rem 1.15rem;
}
.t-num::before {
  content: counter(t, decimal-leading-zero);
  font-size: .8125rem; font-weight: 600; color: var(--accent);
  font-variant-numeric: tabular-nums;
}
.t-body { min-width: 0; }
.topics h3 { margin: 0 0 .2rem; font-size: 1.0625rem; font-weight: 600; letter-spacing: -.02em; }
.topics p { margin: 0; font-size: .9375rem; color: var(--text-2); }

/* ── Q&A */
.qa-list { display: flex; flex-direction: column; gap: .75rem; }
.qa {
  background: var(--surface); border: 1px solid var(--border-soft); border-radius: 12px;
  padding: 1.25rem 1.35rem; display: flex; flex-direction: column; gap: .6rem;
}
.q { margin: 0; font-size: 1.0625rem; font-weight: 600; line-height: 1.5; letter-spacing: -.022em; text-wrap: pretty; }
.q::before {
  content: "Q"; display: inline-block; width: 1.35rem; height: 1.35rem; line-height: 1.35rem;
  text-align: center; margin-right: .5rem; font-size: .75rem; font-weight: 600;
  color: var(--on-accent); background: var(--accent); border-radius: 5px; vertical-align: .1em;
}
.a { font-size: .9375rem; color: var(--text-2); }
.tip {
  margin: .2rem 0 0; padding: .7rem .85rem; background: var(--tint);
  border-radius: 8px; font-size: .875rem;
  display: flex; flex-direction: column; gap: .25rem;
}
.tip-label { font-size: .6875rem; font-weight: 600; letter-spacing: .1em; text-transform: uppercase; color: var(--accent); }
mark { background: none; color: var(--text); padding: 0; }
.chips { margin: .1rem 0 0; display: flex; gap: .35rem; flex-wrap: wrap; }
.chip { font-size: .6875rem; color: var(--text-3); border: 1px solid var(--border); border-radius: 999px; padding: .08rem .55rem; }

/* ── 미해결 */
.open-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: .5rem; }
.open-list li {
  position: relative; padding: .9rem 1.1rem .9rem 2.5rem;
  background: var(--surface); border: 1px solid var(--border-soft); border-radius: 10px;
  font-size: 1rem; font-weight: 500; line-height: 1.55; letter-spacing: -.015em;
}
.open-list li::before {
  content: ""; position: absolute; left: 1.1rem; top: 1.3rem;
  width: .5rem; height: .5rem; border: 1.5px solid var(--accent); border-radius: 50%;
}

/* ── 팁 */
.tip-group { display: flex; flex-direction: column; gap: .75rem; }
.tip-group + .tip-group { margin-top: 1.5rem; }
.group-head {
  margin: 0; font-size: .6875rem; font-weight: 600; letter-spacing: .16em; text-transform: uppercase;
  color: var(--text-3); display: flex; align-items: center; gap: .5rem;
}
.group-count { letter-spacing: 0; color: var(--text-3); opacity: .6; font-variant-numeric: tabular-nums; }
.tip-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(16.5rem, 1fr)); gap: .75rem; }
.tip-card {
  background: var(--surface); border: 1px solid var(--border-soft); border-radius: 12px;
  padding: 1.15rem 1.25rem; box-shadow: var(--shadow);
  display: flex; flex-direction: column; gap: .5rem;
}
.tip-card h3 { margin: 0; font-size: .96rem; font-weight: 600; line-height: 1.45; letter-spacing: -.02em; }
.tip-card p { margin: 0; font-size: .875rem; color: var(--text-2); }

/* ── 액션 · CTA */
.act-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: .5rem; }
.act-list li {
  position: relative; padding: .9rem 1.1rem .9rem 2.5rem;
  background: var(--surface); border: 1px solid var(--border-soft); border-radius: 10px;
  display: flex; flex-direction: column; gap: .3rem;
}
.act-list li::before {
  content: ""; position: absolute; left: 1.1rem; top: 1.3rem;
  width: .55rem; height: .55rem; border: 1.5px solid var(--accent); border-radius: 3px;
}
.act { margin: 0; font-size: .9375rem; }
.meta { margin: 0; display: flex; gap: .6rem; font-size: .75rem; color: var(--text-3); }
.due { color: var(--accent); }
.cta-list li { position: relative; padding-left: 1.5rem; font-size: .9375rem; color: var(--text-2); }
.cta-list li::before { content: "\2192"; position: absolute; left: 0; color: var(--accent); font-weight: 600; }

/* ── 함께하기 (CTA) */
.cta { text-align: center; }
.cta .sec-label { justify-content: center; margin-bottom: .6rem; }
.cta h2 { margin: 0 0 .9rem; font-size: clamp(1.5rem, 4vw, 2rem); letter-spacing: -.03em; }
.cta-lede { margin: 0 auto 1.75rem; max-width: 30rem; font-size: .9375rem; color: var(--text-2); line-height: 1.8; }
.cta-buttons { display: flex; flex-direction: column; gap: .65rem; max-width: 26rem; margin: 0 auto; }
.btn {
  display: block; padding: .95rem 1.25rem; border-radius: 10px;
  font-size: .96rem; font-weight: 600; text-align: center; text-decoration: none;
  border: 1px solid transparent; letter-spacing: -.01em;
  transition: transform .12s ease, filter .12s ease;
}
.btn:hover { filter: brightness(1.08); transform: translateY(-1px); }
.btn:active { transform: none; }
.btn-primary { background: var(--accent-2); color: #fff; }
.btn-ghost { background: transparent; color: var(--accent); border-color: var(--accent); }
.cta-foot { margin: 1.5rem auto 0; max-width: 30rem; font-size: .8125rem; color: var(--text-3); }

/* ── 판권 */
.colophon {
  margin-top: 3rem; padding: 1.5rem 1.5rem 1.75rem; background: var(--surface);
  border: 1px solid var(--border-soft); border-radius: 12px;
  display: flex; flex-direction: column; gap: 1rem;
}
.colophon .sec-label { margin: 0; }
.cline { margin: 0; font-size: .8125rem; color: var(--text-2); max-width: 40rem; }
.cline.quiet { color: var(--text-3); }
.pipe { margin: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr)); gap: .85rem 1.5rem; }
.pipe div { display: flex; flex-direction: column; gap: .1rem; }
.pipe dt { font-size: .6875rem; font-weight: 600; letter-spacing: .14em; text-transform: uppercase; color: var(--text-3); }
.pipe dd { margin: 0; font-size: .8125rem; color: var(--text-2); font-variant-numeric: tabular-nums; }

@media (max-width: 40rem) {
  section { padding: 2.25rem 0; }
  .tip-grid { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
"""


def subset_font(font_path: Path, text: str) -> str:
    """Pretendard를 이 리포트에 쓰인 글자만 남겨 woff2로 서브셋하고 base64로 반환.

    실패하면(fonttools 미설치 등) 원본을 통째로 싣는다 — 글꼴이 빠지는 것보다는
    파일이 큰 편이 낫다.
    """
    try:
        import io

        from fontTools import subset
    except ImportError:
        print("[render] fonttools가 없어 Pretendard 전체를 싣습니다 (.venv/bin/pip install fonttools brotli)")
        return base64.b64encode(font_path.read_bytes()).decode("ascii")

    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.drop_tables = []

    font = subset.load_font(str(font_path), opts)
    ss = subset.Subsetter(options=opts)
    # 본문 글자 + 숫자·기호·영문 전체를 넣는다. 다음 호에 어떤 글자가 올지 모르지만
    # 적어도 UI 라벨(영문 대문자 섹션 라벨 등)은 항상 필요하다.
    ss.populate(text=text + "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    ss.subset(font)
    buf = io.BytesIO()
    font.save(buf)
    font.close()
    return base64.b64encode(buf.getvalue()).decode("ascii")


def main() -> None:
    ap = argparse.ArgumentParser(description="final.json -> 주간 리포트 HTML 미리보기 (발행 아님)")
    ap.add_argument("--final", required=True)
    ap.add_argument("--eval", dest="eval_path", help="검증 통계를 판권에 넣으려면 eval.json 경로")
    ap.add_argument("--draft", help="추출 통계를 판권에 넣으려면 draft.json 경로")
    ap.add_argument("--font", default=None, help="Pretendard woff2 경로 (가변축 권장)")
    ap.add_argument("--issue", type=int, default=None, help="몇 호인지. 생략하면 자동으로 센다")
    ap.add_argument("--out", help="추가로 복사해 둘 경로 (생략 가능). 보관본은 항상 output/reports/에 쌓인다")
    ap.add_argument("--no-archive", action="store_true", help="보관본을 남기지 않는다")
    args = ap.parse_args()

    data = json.loads(Path(args.final).read_text(encoding="utf-8"))
    since = until = None
    counts = {"pass": 0, "discard": 0, "escalate": 0}
    n_batches = n_threads = 0

    source_period_id = None
    if args.draft:
        d = json.loads(Path(args.draft).read_text(encoding="utf-8"))
        since, until = d.get("since"), d.get("until")
        # final.json에는 source_period_id가 없다(A6 산출물 스키마). 기간을 DB에서
        # 확정하려면 원본 구간 id가 필요하므로 draft에서 가져온다.
        source_period_id = d.get("source_period_id")
        g = d.get("generation") or {}
        n_batches, n_threads = g.get("batches", 0), g.get("threads_selected", 0)
    if args.eval_path:
        counts.update(json.loads(Path(args.eval_path).read_text(encoding="utf-8")).get("counts") or {})

    dumped = json.dumps(data, ensure_ascii=False)
    n_nick = len(set(re.findall(r"(?:함께한 선생님|참여자)[A-Z]+", dumped)))

    start_iso, end_iso = data_range(source_period_id or data["period_id"], since, until)
    week_label = f"{_display(start_iso)} ~ {_display(end_iso)}"
    issue = args.issue or issue_number(data["period_id"])
    content = render(data, week_label, issue)
    title = f"{room_name()} 주간 리포트"

    font_b64 = ""
    if args.font:
        used_text = _TAG_RE.sub(" ", content + CSS + title)
        font_b64 = subset_font(Path(args.font), used_text)

    out_html = TEMPLATE.format(
        title=html.escape(title), font_b64=font_b64, css=CSS, content=content,
        n_batches=n_batches, n_threads=n_threads,
        n_pass=counts.get("pass", 0), n_discard=counts.get("discard", 0),
        n_esc=counts.get("escalate", 0), n_nick=n_nick,
    )
    size = len(out_html.encode("utf-8"))
    written: list[Path] = []

    if not args.no_archive:
        # 화면 표기와 같은 기간 값을 쓴다. 둘이 어긋나면 파일명과 내용이 안 맞는다.
        target = archive_path(issue, start_iso, end_iso)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(out_html, encoding="utf-8")
        written.append(target)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out_html, encoding="utf-8")
        written.append(Path(args.out))

    print(f"[render] {issue}호 ({size/1024:.0f} KB, 글꼴 {len(font_b64)/1024:.0f} KB)")
    for w in written:
        print(f"  -> {w}")


if __name__ == "__main__":
    main()
