"""final.json을 사람이 읽는 주간 리포트 HTML로 렌더링한다 (A7 발행 전 미리보기).

docs/report_format.md가 정한 섹션 순서·톤을 따른다. 노션 발행(A7)이 구현되기
전까지 운영자가 G-R 게이트에서 검토할 화면이 이것이다.

설계 원칙 셋:

1. **뼈대는 매 호 고정이다.** 섹션 목록·순서·라벨·설명문이 항상 같다. 이번 주에
   해당 항목이 0건이면 섹션을 없애는 게 아니라 "이번 주에는 없었습니다"를 적는다.
   독자가 매주 같은 자리에서 같은 것을 찾을 수 있어야 하고, 0건이라는 사실 자체가
   정보이기 때문이다.
2. **시각 언어는 notiontalk.com을 따른다.** 본체의 paper/dark/accent/sub/muted/edge
   토큰과 커뮤니티 페이지의 12열 비대칭 구조를 독립형 HTML용 CSS로 옮긴다.
3. **글꼴은 Pretendard 하나다.** 위계는 서체를 바꿔서가 아니라 굵기(가변축 200~800)로
   만든다. Pretendard는 Google Fonts에 없고 아티팩트 CSP가 외부 호스트를 막으므로
   **이 리포트에 실제로 쓰인 글자만 서브셋해서 data URI로 심는다** — 전체 2.0MB가
   실제 사용분만 남기면 100KB 안팎이 된다.
4. **이 파일은 발행이 아니다.** 상태는 항상 `초안`으로 찍힌다 — 리포트를 사람 검토
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
    ("involved", "Get Involved", "선생님, 같이 노션 배워볼래요?",
     "혼자 헤매면 오래 걸리는 일도 함께하면 금방 풀립니다.", ""),
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
COMMUNITY_URL = "https://www.notiontalk.com/contents/community/"


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

    # 6. 함께하기 — 매 호 **한 글자도 달라지지 않는** 고정 블록이다.
    # 그 주 숫자(미해결 개수 등)를 넣지 않는다. 운영자 지정(2026-08-27):
    # "여기는 항상 통일할거야".
    _id, _label, _ko, _desc = SECTIONS[5][:4]
    blocks.append(f"""<section id="{_id}" class="cta">
  <p class="sec-label">{esc(_label)}</p>
  <h2>{esc(_ko)}</h2>
  <p class="cta-lede">{esc(_desc)}</p>
  <div class="cta-buttons">
    <a class="btn btn-primary" href="{OPENCHAT_URL}" target="_blank" rel="noopener">
      노션하는 교사톡 들어가기</a>
    <a class="btn btn-ghost" href="{HOMEPAGE_URL}" target="_blank" rel="noopener">
      노션톡 홈페이지 둘러보기</a>
  </div>
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
        f'<div class="stat"><dt class="lbl">{esc(_to_week(str(k)))}</dt>'
        f'<dd class="num">{esc(str(v))}</dd></div>'
        for k, v in stats.items()
    )
    stats_class = f"stats stats-{min(len(stats), 6)}"

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
  <dl class="{stats_class}" aria-label="이번 주 통계">{cells}</dl>
</header>"""
    index_links = "".join(
        f'<a href="#{sid}"><span>{esc(label)}</span>{esc(ko)}</a>'
        for sid, label, ko, _desc, _empty_msg in SECTIONS
    )
    index = f'<nav class="section-index" aria-label="리포트 목차">{index_links}</nav>'
    return head + "\n" + index + "\n" + render_body(data)


FONT_FACE = """@font-face {{
  font-family: "Pretendard";
  src: url("data:font/woff2;base64,{font_b64}") format("woff2-variations");
  font-weight: 200 800;
  font-style: normal;
  font-display: swap;
}}"""


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>{title}</title>
<style>
{font_css}
{css}
</style>
</head>
<body>
<a class="skip-link" href="#main-content">본문으로 건너뛰기</a>
<nav class="site-chrome" aria-label="노션톡">
  <div class="site-chrome-inner">
    <a class="wordmark" href="{homepage_url}">노션톡</a>
    <span class="product-name">Community Insight</span>
    <div class="site-links">
      <a href="{community_url}">커뮤니티</a>
      <a href="{homepage_url}">홈페이지</a>
    </div>
  </div>
</nav>
<main id="main-content" class="sheet">
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
</main>
</body>
</html>
"""

CSS = """
/* notiontalk.com/app/globals.css의 브랜드 토큰을 그대로 계승한다. */
:root {
  --color-paper: #F4F4F4;
  --color-paper-dark: #ECECEC;
  --color-dark: #1C1917;
  --color-dark-light: #292524;
  --color-accent: #1F5D5C;
  --color-accent-hover: #154645;
  --color-sub: #57534E;
  --color-muted: #A8A29E;
  --color-meta: #746F6A;
  --color-edge: #E7E5E4;
  --color-accent-soft: #E8F0EF;
  --color-white: #FFFFFF;
  --shadow-float: 0 16px 34px -26px rgba(28, 25, 23, .35);
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: var(--color-paper);
  color: var(--color-dark);
  font-family: "Pretendard", "Pretendard Variable", -apple-system, BlinkMacSystemFont,
               "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif;
  font-weight: 400;
  font-size: 16px;
  line-height: 1.7;
  letter-spacing: -.012em;
  word-break: keep-all;
  overflow-wrap: anywhere;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
a { color: var(--color-accent); text-underline-offset: 3px; }
strong { color: var(--color-dark); font-weight: 600; }
code {
  padding: .12em .4em;
  border-radius: 4px;
  background: var(--color-paper-dark);
  color: var(--color-dark-light);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: .85em;
  word-break: break-all;
}
:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 3px; }

.skip-link {
  position: fixed; top: .5rem; left: .5rem; z-index: 100;
  padding: .65rem .9rem; border-radius: .5rem;
  background: var(--color-dark); color: var(--color-white);
  transform: translateY(-150%); transition: transform .2s ease;
}
.skip-link:focus { transform: translateY(0); }

/* notiontalk.com의 56px 고정 내비게이션 문법을 독립형 리포트에 축약 적용한다. */
.site-chrome {
  position: fixed; inset: 0 0 auto; z-index: 40;
  height: 3.5rem;
  border-bottom: 1px solid color-mix(in srgb, var(--color-edge) 85%, transparent);
  background: color-mix(in srgb, var(--color-paper) 90%, transparent);
  -webkit-backdrop-filter: blur(10px);
  backdrop-filter: blur(10px);
}
.site-chrome-inner {
  max-width: 80rem; height: 100%; margin: 0 auto; padding: 0 2.5rem;
  display: flex; align-items: center;
}
.wordmark {
  color: var(--color-dark); font-size: 1.125rem; font-weight: 600;
  letter-spacing: -.03em; text-decoration: none;
}
.product-name {
  margin-left: .75rem; padding-left: .75rem; border-left: 1px solid var(--color-edge);
  color: var(--color-meta); font-size: .75rem; letter-spacing: .08em;
}
.site-links { margin-left: auto; display: flex; gap: 1.75rem; }
.site-links a {
  color: var(--color-sub); font-size: .875rem; text-decoration: none;
  transition: color .2s ease;
}
.site-links a:hover { color: var(--color-dark); }

.sheet {
  max-width: 72rem; margin: 0 auto;
  padding: clamp(5.25rem, 6vw, 6rem) 2.5rem 6rem;
}

/* 머리말: 랜딩 히어로가 아니라 계속 쌓이는 호별 로그의 컴팩트한 발행 머리말. */
.masthead {
  padding: .75rem 0 2.25rem;
}
.kicker {
  margin: 0 0 1rem; display: flex; align-items: center; gap: .65rem; flex-wrap: wrap;
  color: var(--color-meta); font-size: .75rem; letter-spacing: .02em;
}
.kicker .room { color: var(--color-accent); font-weight: 600; }
.kicker .issue {
  padding-left: .65rem; border-left: 1px solid var(--color-edge);
  color: var(--color-dark); font-size: .75rem; font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.kicker .period { font-variant-numeric: tabular-nums; }
h1 {
  margin: 0 0 1rem; max-width: 50rem;
  font-size: clamp(1.9rem, 3.5vw, 2.75rem); font-weight: 600;
  line-height: 1.14; letter-spacing: -.04em; text-wrap: balance;
}
.intro {
  margin: 0 0 1.25rem; max-width: 44rem;
  color: var(--color-sub); font-size: 1rem; font-weight: 300;
  line-height: 1.75; letter-spacing: -.02em;
}
.status {
  margin: 0; display: flex; align-items: center; gap: .5rem; flex-wrap: wrap;
  color: var(--color-meta); font-size: .75rem;
}
.status-note { margin-left: .15rem; }
.badge {
  padding: .2rem .55rem; border: 1px solid var(--color-edge); border-radius: 999px;
  background: var(--color-white); color: var(--color-accent);
  font-size: .6875rem; font-weight: 600; letter-spacing: .03em;
}
.badge-quiet { background: transparent; color: var(--color-meta); }
.stats {
  margin: 2rem 0 0; padding: .9rem 0;
  display: grid; grid-template-columns: repeat(auto-fit, minmax(7rem, 1fr));
  border-top: 1px solid var(--color-edge); border-bottom: 1px solid var(--color-edge);
}
.stats-5 .stat:last-child { grid-column: auto; min-height: auto; }
.stat {
  min-height: auto; padding: .25rem 1.25rem;
  display: flex; flex-direction: column; justify-content: flex-start; gap: .25rem;
  border-right: 1px solid var(--color-edge);
}
.stat:first-child { padding-left: 0; }
.stat:last-child { border-right: 0; }
.num {
  order: -1; margin: 0;
  font-size: 1.4rem; font-weight: 600;
  line-height: 1; letter-spacing: -.04em; font-variant-numeric: tabular-nums;
}
.lbl { color: var(--color-meta); font-size: .75rem; }

.section-index {
  position: sticky; top: 3.5rem; z-index: 30;
  margin: 0 0 1rem; padding: .75rem 0;
  display: flex; gap: .5rem; overflow-x: auto; scrollbar-width: none;
  border-top: 1px solid var(--color-edge); border-bottom: 1px solid var(--color-edge);
  background: color-mix(in srgb, var(--color-paper) 94%, transparent);
  -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px);
}
.section-index::-webkit-scrollbar { display: none; }
.section-index a {
  flex: 0 0 auto; padding: .45rem .75rem; border-radius: .55rem;
  color: var(--color-sub); font-size: .75rem; text-decoration: none;
  transition: background-color .2s ease, color .2s ease;
}
.section-index a span {
  margin-right: .35rem; color: var(--color-meta);
  font-size: .625rem; font-weight: 600; letter-spacing: .11em; text-transform: uppercase;
}
.section-index a:hover { background: var(--color-paper-dark); color: var(--color-dark); }

/* 섹션: 왼쪽 설명 4열 + 오른쪽 콘텐츠 8열. */
section {
  display: grid; grid-template-columns: repeat(12, minmax(0, 1fr));
  column-gap: 2.5rem;
  padding: clamp(4rem, 7vw, 6.5rem) 0;
  border-bottom: 1px solid var(--color-edge);
  scroll-margin-top: 7rem;
}
section:last-of-type { border-bottom: none; }
.sec-head { grid-column: 1 / span 4; align-self: start; }
.sec-body { grid-column: 5 / -1; min-width: 0; }
.sec-label {
  margin: 0 0 .7rem; display: flex; align-items: center; gap: .5rem;
  color: var(--color-accent); font-size: .6875rem; font-weight: 600;
  letter-spacing: .18em; text-transform: uppercase;
}
.sec-count {
  padding: .08rem .42rem; border-radius: 999px;
  background: var(--color-accent-soft); color: var(--color-accent);
  font-size: .625rem; letter-spacing: 0; font-variant-numeric: tabular-nums;
}
h2 {
  margin: 0 0 .65rem;
  font-size: clamp(1.35rem, 2.8vw, 1.75rem); font-weight: 600;
  line-height: 1.25; letter-spacing: -.03em;
}
.sec-desc { margin: 0; max-width: 18rem; color: var(--color-meta); font-size: .875rem; }
.empty {
  margin: 0; padding: 2rem; border: 1px dashed var(--color-edge); border-radius: 1rem;
  background: color-mix(in srgb, var(--color-white) 55%, transparent);
  color: var(--color-meta); font-size: .875rem; text-align: center;
}

.topics { margin: 0; padding: 0; list-style: none; counter-reset: t; }
.topics li {
  display: grid; grid-template-columns: 2.2rem minmax(0, 1fr); gap: 1rem;
  padding: 1.25rem 0; border-top: 1px solid var(--color-edge); counter-increment: t;
}
.topics li:last-child { border-bottom: 1px solid var(--color-edge); }
.t-num::before {
  content: counter(t, decimal-leading-zero); color: var(--color-accent);
  font-size: .75rem; font-weight: 600; font-variant-numeric: tabular-nums;
}
.t-body { min-width: 0; }
.topics h3 {
  margin: 0 0 .25rem; font-size: 1rem; font-weight: 500;
  line-height: 1.45; letter-spacing: -.02em;
}
.topics p { margin: 0; color: var(--color-sub); font-size: .875rem; }

.qa-list { display: flex; flex-direction: column; gap: .75rem; }
.qa {
  padding: 1.4rem 1.5rem; border: 1px solid var(--color-edge); border-radius: 1rem;
  display: flex; flex-direction: column; gap: .75rem;
  background: var(--color-white);
  transition: border-color .25s ease, transform .25s ease, box-shadow .25s ease;
}
.qa:hover {
  border-color: color-mix(in srgb, var(--color-accent) 35%, var(--color-edge));
  transform: translateY(-2px); box-shadow: var(--shadow-float);
}
.q {
  margin: 0; font-size: 1rem; font-weight: 500;
  line-height: 1.5; letter-spacing: -.022em; text-wrap: pretty;
}
.q::before {
  content: "Q"; display: inline-grid; place-items: center;
  width: 1.35rem; height: 1.35rem; margin-right: .55rem; border-radius: .35rem;
  background: var(--color-accent); color: var(--color-white);
  font-size: .6875rem; font-weight: 600; vertical-align: .1em;
}
.a { color: var(--color-sub); font-size: .9375rem; }
.tip {
  margin: .1rem 0 0; padding: .8rem .9rem; border-radius: .65rem;
  display: flex; flex-direction: column; gap: .25rem;
  background: var(--color-accent-soft); font-size: .875rem;
}
.tip-label {
  color: var(--color-accent); font-size: .625rem; font-weight: 600;
  letter-spacing: .12em; text-transform: uppercase;
}
mark { padding: 0; background: none; color: var(--color-dark); }
.chips { margin: .1rem 0 0; display: flex; gap: .35rem; flex-wrap: wrap; }
.chip {
  padding: .1rem .5rem; border: 1px solid var(--color-edge); border-radius: 999px;
  color: var(--color-meta); font-size: .6875rem;
}

.open-list, .act-list {
  margin: 0; padding: 0; display: flex; flex-direction: column; gap: .55rem; list-style: none;
}
.open-list li, .act-list li {
  position: relative; padding: 1rem 1.1rem 1rem 2.6rem;
  border: 1px solid var(--color-edge); border-radius: .85rem;
  background: var(--color-white);
}
.open-list li {
  font-size: .9375rem; font-weight: 500; line-height: 1.55; letter-spacing: -.015em;
}
.open-list li::before, .act-list li::before {
  content: ""; position: absolute; left: 1.15rem; top: 1.38rem;
  width: .5rem; height: .5rem; border: 1.5px solid var(--color-accent);
}
.open-list li::before { border-radius: 50%; }
.act-list li::before { border-radius: .15rem; }

.tip-group { display: flex; flex-direction: column; gap: .75rem; }
.tip-group + .tip-group { margin-top: 2rem; }
.group-head {
  margin: 0; display: flex; align-items: center; gap: .5rem;
  color: var(--color-meta); font-size: .6875rem; font-weight: 600;
  letter-spacing: .14em; text-transform: uppercase;
}
.group-count { opacity: .7; letter-spacing: 0; font-variant-numeric: tabular-nums; }
.tip-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .75rem; }
.tip-card {
  padding: 1.25rem; border: 1px solid var(--color-edge); border-radius: 1rem;
  display: flex; flex-direction: column; gap: .5rem;
  background: var(--color-white); box-shadow: var(--shadow-float);
}
.tip-card h3 {
  margin: 0; font-size: .9375rem; font-weight: 500;
  line-height: 1.45; letter-spacing: -.02em;
}
.tip-card p { margin: 0; color: var(--color-sub); font-size: .875rem; }

.act-list li { display: flex; flex-direction: column; gap: .3rem; }
.act { margin: 0; font-size: .9375rem; }
.meta { margin: 0; display: flex; gap: .6rem; color: var(--color-meta); font-size: .75rem; }
.due { color: var(--color-accent); font-weight: 500; }

.cta {
  display: block; margin-top: clamp(4rem, 7vw, 6.5rem); padding: clamp(2rem, 5vw, 3.5rem);
  border: 0; border-radius: 1.5rem;
  background: var(--color-accent); color: var(--color-white);
}
.cta .sec-label { margin-bottom: .7rem; color: rgba(255,255,255,.82); }
.cta h2 {
  margin: 0 0 .8rem; max-width: 38rem; color: var(--color-white);
  font-size: clamp(1.65rem, 4vw, 2.35rem); letter-spacing: -.035em;
}
.cta-lede { margin: 0 0 1.75rem; max-width: 34rem; color: rgba(255,255,255,.78); }
.cta-buttons { display: flex; gap: .65rem; flex-wrap: wrap; }
.btn {
  display: inline-flex; align-items: center; justify-content: center;
  min-height: 2.9rem; padding: .75rem 1.25rem; border: 1px solid transparent;
  border-radius: 999px; font-size: .875rem; font-weight: 600;
  letter-spacing: -.01em; text-align: center; text-decoration: none;
  transition: background-color .25s ease, color .25s ease, transform .25s ease;
}
.btn:hover { transform: translateY(-2px); }
.btn:active { transform: none; }
.btn-primary { background: var(--color-white); color: var(--color-accent); }
.btn-primary:hover { background: var(--color-paper); }
.btn-ghost { border-color: rgba(255,255,255,.6); color: var(--color-white); }
.btn-ghost:hover { background: rgba(255,255,255,.1); }

.colophon {
  margin-top: 4rem; padding-top: 2rem; border-top: 1px solid var(--color-edge);
  display: flex; flex-direction: column; gap: 1rem;
}
.colophon .sec-label { margin: 0; }
.cline { margin: 0; max-width: 50rem; color: var(--color-sub); font-size: .8125rem; }
.cline.quiet { color: var(--color-meta); }
.pipe {
  margin: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: .85rem 1.5rem;
}
.pipe div { display: flex; flex-direction: column; gap: .1rem; }
.pipe dt {
  color: var(--color-meta); font-size: .625rem; font-weight: 600;
  letter-spacing: .12em; text-transform: uppercase;
}
.pipe dd { margin: 0; color: var(--color-sub); font-size: .8125rem; font-variant-numeric: tabular-nums; }

@media (min-width: 54.01rem) {
  .sec-head { position: sticky; top: 8rem; }
}
@media (max-width: 54rem) {
  .site-chrome-inner { padding: 0 1.5rem; }
  .sheet { padding: 4.75rem 1.5rem 5rem; }
  section { display: block; padding: 4rem 0; }
  .sec-head { margin-bottom: 1.75rem; }
  .sec-desc { max-width: 34rem; }
}
@media (max-width: 40rem) {
  .site-links a:last-child { display: none; }
  .site-links { gap: 1rem; }
  .product-name { display: none; }
  .sheet { padding-right: 1rem; padding-left: 1rem; }
  .masthead { padding: .75rem 0 2rem; }
  h1 { font-size: clamp(1.75rem, 8.5vw, 2.15rem); }
  .status-note { flex-basis: 100%; margin: .15rem 0 0; }
  .stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    padding: 0; overflow: hidden; border: 1px solid var(--color-edge); border-radius: .75rem;
    background: var(--color-edge); gap: 1px;
  }
  .stat { min-height: 5rem; padding: .85rem 1rem; border: 0; background: var(--color-white); }
  .stat:first-child { padding-left: 1rem; }
  .stats-5 .stat:last-child { grid-column: 1 / -1; }
  .section-index { margin-right: -1rem; margin-left: -1rem; padding-right: 1rem; padding-left: 1rem; }
  section { padding: 3.5rem 0; }
  .tip-grid, .pipe { grid-template-columns: 1fr; }
  .qa { padding: 1.2rem; }
  .cta { padding: 2rem 1.35rem; border-radius: 1.25rem; }
  .cta-buttons { flex-direction: column; }
  .btn { width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
@media print {
  body { background: #fff; }
  .skip-link, .site-chrome, .section-index { display: none; }
  .sheet { max-width: none; padding: 1rem; }
  .masthead { padding-bottom: 2rem; }
  section { padding: 2rem 0; break-inside: auto; }
  .topics li, .qa, .open-list li, .tip-card, .act-list li, .cta, .colophon {
    break-inside: avoid;
  }
  .sec-head { position: static; }
  .qa, .tip-card { box-shadow: none; }
  .cta { background: #fff; color: var(--color-dark); border: 1px solid var(--color-edge); }
  .cta h2, .cta .sec-label, .cta-lede { color: var(--color-dark); }
  .cta-buttons { display: none; }
}
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
    template_args = {
        "title": html.escape(title),
        "css": CSS,
        "content": content,
        "homepage_url": HOMEPAGE_URL,
        "community_url": COMMUNITY_URL,
        "n_batches": n_batches,
        "n_threads": n_threads,
        "n_pass": counts.get("pass", 0),
        "n_discard": counts.get("discard", 0),
        "n_esc": counts.get("escalate", 0),
        "n_nick": n_nick,
    }

    font_b64 = ""
    if args.font:
        # 고정 내비게이션·건너뛰기 링크·판권 문구까지 실제 문서 전체에서 글자를
        # 수집한다. content만 보면 TEMPLATE의 한글이 빠져 혼합 폰트가 된다.
        unstyled_html = TEMPLATE.format(font_css="", **template_args)
        used_text = _TAG_RE.sub(" ", unstyled_html)
        font_b64 = subset_font(Path(args.font), used_text)
    font_css = FONT_FACE.format(font_b64=font_b64) if font_b64 else ""

    out_html = TEMPLATE.format(font_css=font_css, **template_args)
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
