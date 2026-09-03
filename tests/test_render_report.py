import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "render_report", ROOT / "scripts" / "render_report.py"
)
render_report = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = render_report
SPEC.loader.exec_module(render_report)


def build_html(data: dict) -> str:
    content = render_report.render(data, "2026.08.19 - 08.26", 1)
    return render_report.TEMPLATE.format(
        title="노션하는 교사톡 주간 리포트",
        font_css="",
        css=render_report.CSS,
        content=content,
        homepage_url=render_report.HOMEPAGE_URL,
        community_url=render_report.COMMUNITY_URL,
        n_batches=0,
        n_threads=0,
        n_pass=0,
        n_discard=0,
        n_esc=0,
        n_nick=0,
    )


def build_email(data: dict) -> str:
    return render_report.render_email_document(data, "2026.08.19 - 08.26", 1)


class ReportDocumentTest(unittest.TestCase):
    def setUp(self):
        self.empty_data = {
            "period_id": "20260819_20260826",
            "report": {
                "hook_title": "한 주의 기록",
                "intro": "선생님들이 나눈 질문과 답을 모았습니다.",
                "sections": {"stats": {"메시지": 0}},
            },
        }
        self.html = build_html(self.empty_data)

    def test_emits_complete_accessible_document(self):
        self.assertTrue(self.html.startswith("<!doctype html>"))
        for fragment in (
            '<html lang="ko">',
            '<meta charset="utf-8">',
            'name="viewport"',
            'class="skip-link"',
            '<main id="main-content" class="sheet">',
            'aria-label="리포트 목차"',
        ):
            self.assertIn(fragment, self.html)
        self.assertNotIn("data:font/woff2;base64,\"", self.html)

    def test_fixed_sections_keep_order_and_empty_states(self):
        positions = [self.html.index(f'id="{sid}"') for sid, *_ in render_report.SECTIONS]
        self.assertEqual(positions, sorted(positions))
        for *_prefix, empty_message in render_report.SECTIONS[:-1]:
            self.assertIn(empty_message, self.html)

    def test_preview_safety_copy_is_preserved(self):
        self.assertIn("초안", self.html)
        self.assertIn("자동생성", self.html)
        self.assertIn("운영자 검토 전", self.html)
        self.assertIn("리포트는 자동 발행되지 않습니다", self.html)

    def test_period_uses_hyphen_not_tilde_or_long_dash(self):
        self.assertEqual(
            render_report.display_period("2026-08-19", "2026-08-26"),
            "2026.08.19 - 08.26",
        )
        self.assertEqual(
            render_report.display_period("2026-12-29", "2027-01-04"),
            "2026.12.29 - 2027.01.04",
        )
        self.assertNotIn("2026.08.19 ~ 2026.08.26", self.html)
        self.assertNotIn("2026.08.19—08.26", self.html)

    def test_period_id_is_used_when_draft_and_message_db_are_absent(self):
        self.assertEqual(
            render_report.data_range("20260819_20260826", None, None),
            ("2026-08-19", "2026-08-26"),
        )

    def test_notiontalk_navigation_targets_canonical_community(self):
        self.assertIn(render_report.COMMUNITY_URL, self.html)
        self.assertNotIn('href="https://www.notiontalk.com/community/', self.html)

    def test_full_report_and_five_stat_layout(self):
        data = {
            "period_id": "20260819_20260826",
            "report": {
                "hook_title": "한 주의 기록",
                "intro": "질문과 답을 모았습니다.",
                "sections": {
                    "stats": {
                        "메시지": 323,
                        "스레드": 84,
                        "해결": 13,
                        "미해결": 3,
                        "함께한 선생님": 47,
                    },
                    "topics": [{"topic": "보기 구성", "why": "질문이 많았습니다."}],
                    "unresolved": [{"question": "아직 답이 없는 질문"}],
                },
            },
            "faq": [{"question": "질문", "answer": "답", "practical_tip": "팁"}],
            "tips": [{"title": "팁 제목", "body": "팁 본문", "feature_tags": ["AI"]}],
            "actions": [{"text": "할 일", "owner_nickname": "함께한 선생님A"}],
        }
        page = build_html(data)
        self.assertIn('class="stats stats-5"', page)
        self.assertIn('<dt class="lbl">메시지</dt><dd class="num">323</dd>', page)
        for text in ("보기 구성", "아직 답이 없는 질문", "질문", "팁 제목", "할 일"):
            self.assertIn(text, page)

    def test_fixed_openchat_cta_exposes_exact_url(self):
        self.assertIn("이 인사이트는 노션하는 교사톡에서 시작됐습니다", self.html)
        self.assertIn("노션하는 교사톡 참여하기", self.html)
        self.assertGreaterEqual(self.html.count(render_report.OPENCHAT_URL), 2)
        self.assertIn('class="openchat-url"', self.html)

    def test_email_preview_renders_every_item_without_truncation(self):
        data = {
            "period_id": "20260819_20260826",
            "report": {
                "hook_title": "한 주의 기록",
                "intro": "질문과 답을 모았습니다.",
                "sections": {
                    "stats": {"메시지": 323},
                    "topics": [
                        {"topic": f"주제 {i}", "why": f"근거 {i}"}
                        for i in range(1, 6)
                    ],
                    "unresolved": [
                        {"question": f"미해결 {i}"} for i in range(1, 4)
                    ],
                },
            },
            "faq": [
                {"question": f"질문 {i}", "answer": f"답 {i}", "practical_tip": f"실무 팁 {i}"}
                for i in range(1, 14)
            ],
            "tips": [
                {"title": f"팁 제목 {i}", "body": f"팁 본문 {i}", "feature_tags": ["AI"]}
                for i in range(1, 27)
            ],
            "actions": [
                {"text": f"할 일 {i}", "owner_nickname": "함께한 선생님A"}
                for i in range(1, 4)
            ],
        }
        email = build_email(data)
        self.assertTrue(email.startswith("<!doctype html>"))
        self.assertIn("max-width:640px", email)
        self.assertIn("검토용 이메일 미리보기", email)
        self.assertIn("자동 발송되지 않습니다", email)
        for text in (
            "주제 5",
            "질문 13",
            "실무 팁 13",
            "미해결 3",
            "팁 제목 26",
            "팁 본문 26",
            "할 일 3",
        ):
            self.assertIn(text, email)
        self.assertGreaterEqual(email.count(render_report.OPENCHAT_URL), 2)


class BrandContractTest(unittest.TestCase):
    def test_inherits_notiontalk_brand_tokens(self):
        for token, value in {
            "--color-paper": "#F4F4F4",
            "--color-paper-dark": "#ECECEC",
            "--color-dark": "#1C1917",
            "--color-dark-light": "#292524",
            "--color-accent": "#1F5D5C",
            "--color-accent-hover": "#154645",
            "--color-sub": "#57534E",
            "--color-muted": "#A8A29E",
            "--color-edge": "#E7E5E4",
        }.items():
            self.assertIn(f"{token}: {value}", render_report.CSS)
        self.assertIn("--color-meta: #746F6A", render_report.CSS)

    def test_removes_previous_huddling_palette(self):
        for legacy_color in ("#050510", "#7AA2FF", "#0058E0"):
            self.assertNotIn(legacy_color, render_report.CSS)

    def test_reduced_motion_and_print_rules_exist(self):
        self.assertIn("prefers-reduced-motion: reduce", render_report.CSS)
        self.assertIn("@media print", render_report.CSS)

    def test_issue_header_uses_compact_editorial_hierarchy(self):
        self.assertIn("max-width: 42rem", render_report.CSS)
        self.assertIn("font-size: clamp(1.9rem, 3vw, 2.25rem)", render_report.CSS)
        self.assertNotIn("4.35rem", render_report.CSS)
        self.assertIn("grid-template-columns: repeat(auto-fit, minmax(7rem, 1fr))", render_report.CSS)
        self.assertIn("border-top: 1px solid var(--color-edge)", render_report.CSS)

    def test_web_report_uses_newsletter_single_column(self):
        self.assertIn(".masthead, .section-index, .sheet > section, .colophon { max-width: 42rem; }", render_report.CSS)
        self.assertIn("/* 뉴스레터 상세와 같은 42rem 단일 읽기 열. */", render_report.CSS)
        self.assertIn(".tip-grid { display: grid; grid-template-columns: 1fr;", render_report.CSS)
        self.assertNotIn("grid-template-columns: repeat(12, minmax(0, 1fr))", render_report.CSS)


if __name__ == "__main__":
    unittest.main()
