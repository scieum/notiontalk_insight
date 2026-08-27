"""A7(노션 발행)에서 네트워크 없이 검증 가능한 부분.

토큰이 없어도 도는 것만 담았다 — 블록 변환, 설정 파서, 스키마 대조, C5 게이트.
실제 API 호출은 통합·토큰이 준비된 환경에서 `--dry-run` 없이 한 번 확인해야 한다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".claude/skills/notion-publish/scripts"))

import blocks as blk
import notion_client as nc
import publish_report as pub

from lib.common.simple_yaml import loads as yaml_loads

SAMPLE = {
    "period_id": "2025-01-26_A__20260819-끝",
    "report": {
        "hook_title": "AI로 수업 자료 만들기, 원하는 결과는 어떻게 얻을까요?",
        "title": "노션하는 교사톡 커뮤니티 리포트",
        "intro": "이번 주는 AI 활용 이야기가 많았습니다.",
        "sections": {
            "topics": [{"topic": "AI 수업 자료", "why": "질문이 많았습니다"}],
            "unresolved": [{"question": "공직자 메일로 가입 되나요?"}],
            "decisions": [],
            "stats": {"메시지 수": 323, "함께한 선생님": 37},
        },
    },
    "faq": [{"question": "교육용 요금제 되나요?", "answer": "대학 메일만 됩니다.",
             "practical_tip": "대학원 메일을 쓰세요."}],
    "tips": [{"title": "HTML로 먼저 만들기", "body": "시각 자유도가 높습니다.",
              "feature_tags": ["AI"], "practical_tip": None}],
    "actions": [{"text": "규칙 정리해 공유", "owner_nickname": "1000쌤(정보부장)", "due": "2026-08-20"}],
}


class TestBlocks(unittest.TestCase):
    def setUp(self):
        self.body = blk.build(SAMPLE, "2026.08.19 ~ 2026.08.26", 1)
        self.texts = self._all_text(self.body)

    @staticmethod
    def _all_text(body):
        out = []
        for b in body:
            payload = b.get(b["type"], {})
            for rt in payload.get("rich_text", []):
                out.append(rt["text"]["content"])
        return "\n".join(out)

    def test_every_block_is_wellformed(self):
        for b in self.body:
            self.assertEqual(b["object"], "block")
            self.assertIn(b["type"], b, f"{b['type']} 페이로드가 없습니다")
            for rt in b[b["type"]].get("rich_text", []):
                self.assertLessEqual(len(rt["text"]["content"]), blk.MAX_TEXT)

    def test_fixed_sections_present_in_order(self):
        want = ["이번 주 주요 주제", "해결된 질문", "아직 답이 없는 질문",
                "이번 주의 팁", "하기로 한 것", "선생님, 같이 노션 배워볼래요?"]
        found = [t for t in self.texts.splitlines() if t in want]
        self.assertEqual(found, want)

    def test_empty_section_says_so_instead_of_vanishing(self):
        empty = dict(SAMPLE)
        empty["faq"] = []
        body = blk.build(empty, "라벨", 1)
        self.assertIn(blk.EMPTY_TEXT["solved"], self._all_text(body))
        self.assertIn("해결된 질문", self._all_text(body))

    def test_cta_is_fixed_and_has_both_links(self):
        links = [rt["text"].get("link", {}).get("url")
                 for b in self.body for rt in b[b["type"]].get("rich_text", [])
                 if rt["text"].get("link")]
        self.assertIn(blk.OPENCHAT_URL, links)
        self.assertIn(blk.HOMEPAGE_URL, links)
        # 그 주 값(미해결 개수 등)이 CTA로 새어들지 않는다
        self.assertNotIn("1개 있어요", self.texts)

    def test_long_text_is_split_not_truncated(self):
        long_answer = "가" * 5000
        data = dict(SAMPLE)
        data["faq"] = [{"question": "q", "answer": long_answer, "practical_tip": None}]
        joined = self._all_text(blk.build(data, "라벨", 1))
        # 문서의 다른 고정 문구에도 같은 글자가 있으므로 개수가 아니라
        # 원문 전체가 통째로 남아 있는지를 본다(잘라내지 않는다는 확인).
        self.assertIn(long_answer, joined.replace("\n", ""))

    def test_batching_respects_notion_limit(self):
        many = [blk.paragraph(str(i)) for i in range(250)]
        chunks = list(blk.batched(many))
        self.assertEqual([len(c) for c in chunks], [100, 100, 50])


class TestConfig(unittest.TestCase):
    def test_yaml_subset_parses_nesting_and_lists(self):
        parsed = yaml_loads(
            "database: \"https://x/abc\"\n"
            "properties:\n  title: 이름\n  status: null\n  title_prefix_issue: true\n"
            "auto_tags:\n  - 자동생성\n  - 주간\n"
            "duplicate_check: both  # 주석\n"
        )
        self.assertEqual(parsed["database"], "https://x/abc")
        self.assertEqual(parsed["properties"]["title"], "이름")
        self.assertIsNone(parsed["properties"]["status"])
        self.assertTrue(parsed["properties"]["title_prefix_issue"])
        self.assertEqual(parsed["auto_tags"], ["자동생성", "주간"])
        self.assertEqual(parsed["duplicate_check"], "both")

    def test_shipped_config_is_parseable(self):
        cfg = pub.load_yaml(pub.CONFIG_PATH)
        self.assertIn("database", cfg)
        self.assertIn("properties", cfg)

    def test_title_prefix_works_from_either_place(self):
        """title_source/title_prefix_issue를 properties 아래에 적어도 동작해야 한다.
        실제로 그 자리에 두는 바람에 접두어가 조용히 빠진 적이 있다."""
        report = {"hook_title": "후킹", "title": "사무적"}
        top = {"title_source": "hook", "title_prefix_issue": True}
        nested = {"properties": {"title_source": "hook", "title_prefix_issue": True}}
        for cfg in (top, nested):
            self.assertEqual(pub._title_text(report, cfg, 3, "기간"), "3호 · 후킹")
        self.assertEqual(pub._title_text(report, {"title_source": "formal"}, 3, "기간"), "사무적")
        self.assertEqual(pub._title_text(report, {"title_source": "issue"}, 3, "기간"), "3호 · 기간")
        with self.assertRaises(pub.ConfigError):
            pub._title_text(report, {"title_source": "이상한값"}, 3, "기간")

    def test_url_and_bare_id_normalize_to_same_uuid(self):
        url = "https://app.notion.com/p/x/37ddd1dcd64480bc99ccca2cafcd31ed?v=37ddd1dcd64480e88042000c8519b06a"
        self.assertEqual(nc.normalize_id(url), "37ddd1dc-d644-80bc-99cc-ca2cafcd31ed")
        self.assertEqual(nc.normalize_id("37ddd1dcd64480bc99ccca2cafcd31ed"),
                         nc.normalize_id(url))


class TestSchemaValidation(unittest.TestCase):
    SCHEMA = {
        "이름": {"type": "title"},
        "상태": {"type": "select"},
        "호수": {"type": "number"},
        "만든 사람": {"type": "created_by"},
    }

    def test_accepts_matching_config(self):
        cfg = {"properties": {"title": "이름", "status": "상태", "issue_number": "호수"},
               "status_draft_value": "초안"}
        self.assertEqual(pub.validate_config(cfg, self.SCHEMA)["title"], "이름")

    def test_rejects_missing_property(self):
        cfg = {"properties": {"title": "이름", "status": "없는속성"}, "status_draft_value": "초안"}
        with self.assertRaises(pub.ConfigError) as e:
            pub.validate_config(cfg, self.SCHEMA)
        self.assertIn("없는속성", str(e.exception))

    def test_rejects_wrong_type(self):
        cfg = {"properties": {"title": "이름", "issue_number": "상태"}, "status_draft_value": "초안"}
        with self.assertRaises(pub.ConfigError):
            pub.validate_config(cfg, self.SCHEMA)

    def test_rejects_missing_title_mapping(self):
        with self.assertRaises(pub.ConfigError):
            pub.validate_config({"properties": {"status": "상태"}}, self.SCHEMA)

    def test_refuses_auto_publish_status(self):
        """C5: 리포트를 자동으로 발행 상태로 만들 수 없다."""
        for bad in ("발행", "published", "공개"):
            cfg = {"properties": {"title": "이름", "status": "상태"}, "status_draft_value": bad}
            with self.assertRaises(pub.ConfigError) as e:
                pub.validate_config(cfg, self.SCHEMA)
            self.assertIn("C5", str(e.exception))

    def test_builds_properties_without_touching_absent_ones(self):
        cfg = {"properties": {"title": "이름", "status": "상태"},
               "status_draft_value": "초안", "auto_tags": ["자동생성"]}
        props = pub.build_properties(cfg, cfg["properties"], self.SCHEMA,
                                     title="제목", issue=1,
                                     start_iso="2026-08-19", end_iso="2026-08-26")
        self.assertEqual(set(props), {"이름", "상태"})
        self.assertEqual(props["상태"], {"select": {"name": "초안"}})


if __name__ == "__main__":
    unittest.main()
