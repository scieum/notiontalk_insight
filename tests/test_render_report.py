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
    content = render_report.render(data, "2026.08.19 ~ 2026.08.26", 1)
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
        self.assertIn("font-size: clamp(1.9rem, 3.5vw, 2.75rem)", render_report.CSS)
        self.assertNotIn("4.35rem", render_report.CSS)
        self.assertIn("grid-template-columns: repeat(auto-fit, minmax(7rem, 1fr))", render_report.CSS)
        self.assertIn("border-top: 1px solid var(--color-edge)", render_report.CSS)


if __name__ == "__main__":
    unittest.main()
