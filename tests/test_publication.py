import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_publication = load_script("build_publication")
validate_publication_script = load_script("validate_publication")
open_publication_pr = load_script("open_publication_pr")
backfill_report = load_script("backfill_report_publication")
pii_apply = load_path("publication_test_pii_apply", ROOT / ".claude/skills/pii-guard/scripts/apply.py")

from lib.publication import (
    FORBIDDEN_KEYS,
    MAX_PUBLICATION_BYTES,
    TOP_LEVEL_KEYS,
    PublicationValidationError,
    content_sha256,
    count_items,
    load_publication,
    pretty_json,
    validate_publication,
)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def project(source: dict, issue: int = 1) -> dict:
    return build_publication.project_publication(
        source,
        start=date(2026, 8, 19),
        end=date(2026, 8, 26),
        issue=issue,
        generated_at="2026-09-03T12:00:00+09:00",
        consent_mode="anon",
    )


def refresh_evidence(source: dict) -> None:
    payload = deepcopy(source)
    payload.pop("privacy_evidence", None)
    source["privacy_evidence"]["payload_sha256"] = build_publication._canonical_sha256(payload)


class ProjectionTest(unittest.TestCase):
    def test_full_fixture_preserves_all_public_item_counts(self):
        publication = project(fixture("a6_final_full.json"))
        self.assertEqual(
            count_items(publication),
            {"topics": 2, "faq": 2, "unresolved": 1, "tips": 2, "actions": 2},
        )
        self.assertEqual(publication["period"]["label"], "2026.08.19 - 08.26")
        self.assertEqual(publication["title"], "노션하는 교사톡 주간 인사이트 1호")
        self.assertEqual(publication["hook_title"], "데이터베이스 보기를 어떻게 정리할까요?")
        self.assertEqual(publication["generated_at"], "2026-09-03T03:00:00Z")
        self.assertEqual(publication["actions"][1]["owner"], "함께한 선생님A")
        validate_publication(publication)

    def test_zero_fixture_keeps_every_empty_section(self):
        publication = project(fixture("a6_final_zero.json"))
        self.assertEqual(
            count_items(publication),
            {"topics": 0, "faq": 0, "unresolved": 0, "tips": 0, "actions": 0},
        )
        validate_publication(publication)

    def test_projection_strips_forbidden_keys_and_source_ids(self):
        publication = project(fixture("a6_final_full.json"))
        rendered = pretty_json(publication)
        forbidden_key_pattern = re.compile(
            r'"(?:source_message_ids|source_thread_ids|merge_into|local_path|evaluation|id)"\s*:'
        )
        self.assertIsNone(forbidden_key_pattern.search(rendered), rendered)
        self.assertTrue(publication["privacy"]["source_ids_stripped"])

        def keys(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield key
                    yield from keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from keys(child)

        self.assertFalse(FORBIDDEN_KEYS.intersection(keys(publication)))

    def test_same_input_is_byte_deterministic_and_second_write_is_noop(self):
        first = project(fixture("a6_final_full.json"))
        second = project(fixture("a6_final_full.json"))
        self.assertEqual(pretty_json(first), pretty_json(second))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "publication.json"
            self.assertEqual(build_publication.write_if_changed(target, first), "created")
            first_mtime = target.stat().st_mtime_ns
            self.assertEqual(build_publication.write_if_changed(target, second), "unchanged")
            self.assertEqual(target.stat().st_mtime_ns, first_mtime)

    def test_summary_count_mismatch_fails_closed(self):
        source = fixture("a6_final_full.json")
        source["report"]["sections"]["stats"]["질문 수"] = 99
        refresh_evidence(source)
        with self.assertRaisesRegex(PublicationValidationError, "approved item count"):
            project(source)

    def test_missing_tampered_and_named_to_anon_evidence_fail_closed(self):
        source = fixture("a6_final_full.json")
        source.pop("privacy_evidence")
        with self.assertRaisesRegex(PublicationValidationError, "privacy_evidence"):
            project(source)

        source = fixture("a6_final_full.json")
        source["report"]["intro"] += " 변조"
        with self.assertRaisesRegex(PublicationValidationError, "does not match"):
            project(source)

        source = fixture("a6_final_full.json")
        source["privacy_evidence"]["consent_mode"] = "named"
        with self.assertRaisesRegex(PublicationValidationError, "consent mode drift"):
            project(source)

    def test_a6_period_provenance_rejects_2030_relabel(self):
        source = fixture("a6_final_full.json")
        with self.assertRaisesRegex(PublicationValidationError, "immutable A6 period"):
            build_publication.project_publication(
                source,
                start=date(2030, 1, 1),
                end=date(2030, 1, 7),
                issue=1,
                generated_at="2030-01-08T00:00:00Z",
                consent_mode="anon",
            )

    def test_actual_a6_output_builds_without_manual_provenance_fabrication(self):
        source = fixture("a6_final_full.json")
        source.pop("privacy_evidence")
        source.pop("period_start")
        source.pop("period_end")
        source["since"] = "2026-08-19"
        source["until"] = "2026-08-26"
        source["actions"][1]["owner_nickname"] = "원본교사"
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "passed.json"
            output_path = Path(tmp) / "final.json"
            input_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(pii_apply, "get_consent_mode", return_value="anon"), mock.patch.object(
                pii_apply, "get_period_nicknames", return_value=["원본교사"]
            ), mock.patch.object(pii_apply, "load_exempt_nicknames", return_value=set()), mock.patch.object(
                pii_apply, "escalate"
            ), mock.patch.object(pii_apply, "log_event"):
                final = pii_apply.run(input_path, output_path)
        publication = project(final)
        self.assertEqual(publication["actions"][1]["owner"], "함께한 선생님A")
        validate_publication(publication)


class LegacyBackfillTest(unittest.TestCase):
    def test_reviewed_reports_recover_expected_public_item_counts(self):
        cases = [
            ("issue-01_2026-08-24_2026-08-30.html", (9, 5, 4, 5, 2)),
            ("issue-02_2026-08-31_2026-09-05.html", (4, 4, 5, 17, 2)),
            ("issue-03_2026-09-07_2026-09-13.html", (5, 6, 2, 3, 4)),
        ]
        for filename, expected in cases:
            with self.subTest(filename=filename):
                publication = backfill_report.project_legacy_report(
                    (ROOT / "reports" / filename).read_text(encoding="utf-8"),
                    generated_at="2026-09-18T00:00:00Z",
                )
                self.assertEqual(
                    tuple(len(publication[key]) for key in ("topics", "faq", "unresolved", "tips", "actions")),
                    expected,
                )
                validate_publication(publication)


class ValidatorTest(unittest.TestCase):
    def setUp(self):
        self.publication = project(fixture("a6_final_full.json"))

    def rehash(self, publication):
        publication["content_sha256"] = content_sha256(publication)

    def test_hash_excludes_only_hash_field_and_detects_tampering(self):
        original_hash = self.publication["content_sha256"]
        self.publication["intro"] += " 변경"
        self.assertNotEqual(content_sha256(self.publication), original_hash)
        with self.assertRaisesRegex(PublicationValidationError, "does not match"):
            validate_publication(self.publication)

    def test_duplicate_json_keys_are_rejected_even_when_pii_value_is_overridden(self):
        rendered = pretty_json(self.publication)
        needle = '  "intro": '
        duplicate = rendered.replace(
            needle,
            '  "intro": "teacher@example.com",\n' + needle,
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "publication.json"
            path.write_text(duplicate, encoding="utf-8")
            with self.assertRaisesRegex(PublicationValidationError, "duplicate JSON object key.*intro"):
                load_publication(path)

    def test_schema_const_does_not_treat_true_as_integer_one(self):
        candidate = deepcopy(self.publication)
        candidate["schema_version"] = True
        self.rehash(candidate)
        with self.assertRaisesRegex(PublicationValidationError, "schema_version"):
            validate_publication(candidate)

    def test_period_and_action_due_require_hyphenated_full_date(self):
        candidate = deepcopy(self.publication)
        candidate["period"]["start"] = "20260819"
        self.rehash(candidate)
        with self.assertRaisesRegex(PublicationValidationError, "full-date"):
            validate_publication(candidate)

        candidate = deepcopy(self.publication)
        candidate["actions"][1]["due"] = "20260910"
        self.rehash(candidate)
        with self.assertRaisesRegex(PublicationValidationError, "full-date"):
            validate_publication(candidate)

    def test_pii_local_paths_and_forbidden_keys_fail_closed(self):
        cases = [
            ("문의: teacher@example.com", "email"),
            ("파일: /home/operator/output/final.json", "local_path"),
            ("문의: +82 10 1234 5678", "phone"),
            ("참고: https://example.com/private-token", "url"),
            ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "bearer_token"),
            ("민수쌤이 공유했습니다.", "person-like"),
            ("Call +1 (202) 555-0199", "international_phone"),
            ("연락은 (010) 1234-5678", "phone"),
            ("파일 /mnt/c/Users/operator/private.txt", "sensitive_posix_path"),
            ("notion.so/0123456789abcdef0123456789abcdef?pvs=4", "bare_notion_share"),
            ("ntn_abcdefghijklmnopqrstuvwxyz123456", "notion_token"),
            ("invite/abcdefghijklmnopQRST", "share_token"),
        ]
        for unsafe, expected in cases:
            with self.subTest(unsafe=unsafe):
                candidate = deepcopy(self.publication)
                candidate["intro"] = unsafe
                self.rehash(candidate)
                with self.assertRaisesRegex(PublicationValidationError, expected):
                    validate_publication(candidate)

        candidate = deepcopy(self.publication)
        candidate["faq"][0]["source_message_ids"] = [1]
        self.rehash(candidate)
        with self.assertRaisesRegex(PublicationValidationError, "forbidden keys"):
            validate_publication(candidate)

        allowed = deepcopy(self.publication)
        allowed["intro"] = "1000쌤이 공유한 노션 활용 팁입니다."
        self.rehash(allowed)
        validate_publication(allowed)

    def test_schema_top_level_contract_matches_runtime_validator(self):
        schema = json.loads((ROOT / "schemas" / "publication.schema.json").read_text("utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), TOP_LEVEL_KEYS)
        self.assertEqual(set(schema["properties"]), TOP_LEVEL_KEYS)

    def test_content_pr_changed_file_allowlist(self):
        allowed = "publications/insight-20260819-20260826/publication.json"
        with mock.patch.object(validate_publication_script, "load_publication", return_value=self.publication):
            path = validate_publication_script.validate_changed_paths([allowed])
        self.assertEqual(path, ROOT / allowed)
        with self.assertRaisesRegex(PublicationValidationError, "exactly one publication"):
            validate_publication_script.validate_changed_paths([allowed, "README.md"])
        with self.assertRaisesRegex(PublicationValidationError, "only allowed"):
            validate_publication_script.validate_changed_paths(
                ["publications/insight-20260819-20260826/source_message_ids.json"]
            )
        for change in (("D", allowed, None), ("T", allowed, None), ("R100", allowed, allowed + ".old")):
            with self.subTest(change=change), self.assertRaisesRegex(
                PublicationValidationError, "deletion, rename, copy, or type change"
            ):
                validate_publication_script.validate_changed_paths([change])

    def test_repository_denylist_and_duplicate_slugs(self):
        for path in ("output/insights/period.final.json", "notes.draft.json", "run.log"):
            with self.subTest(path=path), self.assertRaisesRegex(
                PublicationValidationError, "forbidden generated/raw artifacts"
            ):
                validate_publication_script.validate_repository_paths([path])
        for path in (
            "publications/README.md",
            "publications/insight-20260819-20260826/extra.json",
            "publications//insight-20260819-20260826/publication.json",
        ):
            with self.subTest(path=path), self.assertRaisesRegex(
                PublicationValidationError, "unexpected path|malformed path"
            ):
                validate_publication_script.validate_repository_paths([path])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = "publications/insight-20260819-20260826/publication.json"
            second = "publications/insight-20260820-20260827/publication.json"
            one = deepcopy(self.publication)
            two = deepcopy(self.publication)
            two["content_id"] = "insight-20260820-20260827"
            two["period"] = {"start": "2026-08-20", "end": "2026-08-27", "label": "2026.08.20 - 08.27"}
            two["content_sha256"] = content_sha256(two)
            for relative, payload in ((first, one), (second, two)):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(pretty_json(payload), encoding="utf-8")
            with self.assertRaisesRegex(PublicationValidationError, "duplicate publication slug"):
                validate_publication_script.validate_all(root, [first, second])

    def test_git_ls_files_uses_nul_delimiters_and_rejects_control_paths(self):
        completed = mock.Mock(stdout=b"README.md\0publications/bad\nname.json\0", stderr=b"")
        with mock.patch.object(validate_publication_script.subprocess, "run", return_value=completed) as run:
            with self.assertRaisesRegex(PublicationValidationError, "control characters"):
                validate_publication_script.repository_paths_from_git(Path("/tmp/candidate"))
        self.assertIn("-z", run.call_args.args[0])

    def test_candidate_root_success_message_is_relative_to_candidate(self):
        allowed = "publications/insight-20260819-20260826/publication.json"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / allowed
            target.parent.mkdir(parents=True)
            target.write_text(pretty_json(self.publication), encoding="utf-8")
            argv = [
                "validate_publication.py", "--repository-root", str(root),
                "--changed-files", allowed,
            ]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                validate_publication_script, "validate_all", return_value=[]
            ), mock.patch("builtins.print") as printer:
                validate_publication_script.main()
        printer.assert_any_call(f"[validate-publication] valid content PR: {allowed}")

    def test_generated_at_rejects_non_rfc3339_iso_variant(self):
        candidate = deepcopy(self.publication)
        candidate["generated_at"] = "2026-09-03 12:00:00+09:00"
        self.rehash(candidate)
        with self.assertRaisesRegex(PublicationValidationError, "RFC 3339"):
            validate_publication(candidate)

    def test_payload_and_cardinality_limits(self):
        candidate = deepcopy(self.publication)
        candidate["title"] = "가" * 121
        candidate["content_sha256"] = content_sha256(candidate)
        with self.assertRaisesRegex(PublicationValidationError, "maxLength"):
            validate_publication(candidate)

        candidate = deepcopy(self.publication)
        candidate["topics"] = candidate["topics"] * 6
        candidate["content_sha256"] = content_sha256(candidate)
        with self.assertRaisesRegex(PublicationValidationError, "maxItems|10-item"):
            validate_publication(candidate)


class PullRequestAutomationTest(unittest.TestCase):
    def setUp(self):
        self.publication = project(fixture("a6_final_full.json"))

    def test_dry_run_is_offline_and_same_input_is_noop(self):
        plan = open_publication_pr.dry_run_plan(self.publication, deepcopy(self.publication))
        self.assertEqual(plan["status"], "noop")
        self.assertEqual(plan["network_requests"], 0)
        self.assertEqual(plan["local_git_mutations"], 0)
        self.assertEqual(plan["repository"], "scieum/notiontalk_insight")

    def test_missing_operator_token_stops_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_root = open_publication_pr.ROOT
            try:
                open_publication_pr.ROOT = Path(tmp)
                target = Path(tmp) / "publications" / self.publication["content_id"] / "publication.json"
                target.parent.mkdir(parents=True)
                target.write_text(pretty_json(self.publication), encoding="utf-8")
                argv = ["open_publication_pr.py", str(target), "--execute"]
                with mock.patch.object(sys, "argv", argv), mock.patch.dict(
                    os.environ, {open_publication_pr.TOKEN_ENV: ""}, clear=False
                ), mock.patch("urllib.request.urlopen") as urlopen:
                    with self.assertRaisesRegex(SystemExit, "stopped before any GitHub request"):
                        open_publication_pr.main()
                    urlopen.assert_not_called()
            finally:
                open_publication_pr.ROOT = old_root

    def test_pr_body_contains_period_counts_and_privacy_results(self):
        body = open_publication_pr.render_pr_body(self.publication)
        for expected in (
            "2026.08.19 - 08.26",
            "| 2 | 2 | 1 | 2 | 2 |",
            "PII 재검사 통과: `true`",
            "내부 source ID 제거: `true`",
            self.publication["content_sha256"],
        ):
            self.assertIn(expected, body)

    def test_execute_same_open_pr_hash_performs_no_mutation(self):
        encoded = open_publication_pr.base64.b64encode(
            pretty_json(self.publication).encode("utf-8")
        ).decode("ascii")

        class FakeClient:
            calls = []

            def __init__(self, _token):
                pass

            def request(self, method, path, payload=None):
                self.calls.append((method, path, payload))
                if path.startswith("/contents/") and "ref=main" in path:
                    raise open_publication_pr.GitHubAPIError(404, "not found")
                if path.startswith("/git/ref/heads%2F"):
                    return {"object": {"sha": "a" * 40}}
                if path.startswith("/compare/"):
                    return {"files": [{"filename": "publications/insight-20260819-20260826/publication.json", "status": "modified"}]}
                if path.startswith("/contents/") and "ref=publication" in path:
                    return {"encoding": "base64", "content": encoded, "sha": "b" * 40}
                if path.startswith("/pulls?"):
                    return [{"number": 7, "html_url": "https://github.com/scieum/notiontalk_insight/pull/7"}]
                raise AssertionError((method, path, payload))

        with mock.patch.object(open_publication_pr, "GitHubClient", FakeClient):
            result = open_publication_pr.execute(self.publication, "synthetic-token")
        self.assertEqual(result["status"], "noop")
        self.assertFalse(any(method in {"PUT", "POST", "PATCH"} for method, *_ in FakeClient.calls))

    def test_same_hash_remote_is_recomputed_before_noop(self):
        remote = deepcopy(self.publication)
        remote["intro"] += " tampered"
        encoded = open_publication_pr.base64.b64encode(pretty_json(remote).encode()).decode()

        class FakeClient:
            def __init__(self, _token): pass
            def request(self, method, path, payload=None):
                if path.startswith("/contents/") and "ref=main" in path:
                    raise open_publication_pr.GitHubAPIError(404, "not found")
                if path.startswith("/git/ref/heads%2F"): return {"object": {"sha": "a" * 40}}
                if path.startswith("/compare/"): return {"files": [{"filename": "publications/insight-20260819-20260826/publication.json", "status": "modified"}]}
                if path.startswith("/contents/") and "ref=publication" in path:
                    return {"encoding": "base64", "content": encoded, "sha": "b" * 40}
                raise AssertionError((method, path, payload))

        with mock.patch.object(open_publication_pr, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(open_publication_pr.GitHubAPIError, "refusing same-hash no-op"):
                open_publication_pr.execute(self.publication, "token")

    def test_oversized_whitespace_same_hash_remote_is_rejected_before_noop(self):
        oversized = pretty_json(self.publication).encode("utf-8") + b" " * MAX_PUBLICATION_BYTES
        encoded = open_publication_pr.base64.b64encode(oversized).decode("ascii")

        class FakeClient:
            def __init__(self, _token): pass
            def request(self, method, path, payload=None):
                if path.startswith("/contents/") and "ref=main" in path:
                    raise open_publication_pr.GitHubAPIError(404, "not found")
                if path.startswith("/git/ref/heads%2F"):
                    return {"object": {"sha": "a" * 40}}
                if path.startswith("/compare/"):
                    return {"files": [{
                        "filename": "publications/insight-20260819-20260826/publication.json",
                        "status": "modified",
                    }]}
                if path.startswith("/contents/") and "ref=publication" in path:
                    return {"encoding": "base64", "content": encoded, "sha": "b" * 40}
                raise AssertionError((method, path, payload))

        with mock.patch.object(open_publication_pr, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(open_publication_pr.GitHubAPIError, "byte publication limit"):
                open_publication_pr.execute(self.publication, "token")

    def test_json_requests_set_content_type(self):
        response = mock.MagicMock()
        response.read.return_value = b'{}'
        response.__enter__.return_value = response
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            open_publication_pr.GitHubClient("token").request("POST", "/pulls", {"title": "x"})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Content-type"), "application/json; charset=utf-8")

    def test_network_failures_are_wrapped(self):
        with mock.patch("urllib.request.urlopen", side_effect=open_publication_pr.urllib.error.URLError("offline")):
            with self.assertRaisesRegex(open_publication_pr.GitHubAPIError, "network.*offline"):
                open_publication_pr.GitHubClient("token").request("GET", "/pulls")

    def test_compare_requires_one_added_or_modified_non_rename(self):
        path = "publications/insight-20260819-20260826/publication.json"
        invalid = [
            {"files": []},
            {"files": [{"filename": path, "status": "renamed", "previous_filename": path + ".old"}]},
            {"files": [{"filename": path, "status": "modified", "previous_filename": path + ".old"}]},
            {"files": [{"filename": path, "status": "removed"}]},
            {"files": [{"filename": path, "status": "added"}, {"filename": "README.md", "status": "modified"}]},
        ]
        for comparison in invalid:
            with self.subTest(comparison=comparison), self.assertRaises(open_publication_pr.GitHubAPIError):
                open_publication_pr._validate_branch_comparison(comparison, path)

    def test_network_failure_after_branch_creation_reports_partial_mutation(self):
        class FakeClient:
            def __init__(self, _token): pass
            def request(self, method, path, payload=None):
                if path.startswith("/contents/") and "ref=main" in path:
                    raise open_publication_pr.GitHubAPIError(404, "not found")
                if path.startswith("/git/ref/heads%2F"):
                    raise open_publication_pr.GitHubAPIError(404, "not found")
                if path == "/git/ref/heads/main":
                    return {"object": {"sha": "a" * 40}}
                if method == "POST" and path == "/git/refs":
                    return {"ref": "created"}
                raise open_publication_pr.GitHubAPIError(None, "timed out")
        with mock.patch.object(open_publication_pr, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(open_publication_pr.GitHubAPIError, "partial mutations already completed.*created branch"):
                open_publication_pr.execute(self.publication, "token")

    def test_execute_rejects_contaminated_publication_branch_before_mutation(self):
        class FakeClient:
            calls = []

            def __init__(self, _token):
                pass

            def request(self, method, path, payload=None):
                self.calls.append((method, path, payload))
                if path.startswith("/contents/") and "ref=main" in path:
                    raise open_publication_pr.GitHubAPIError(404, "not found")
                if path.startswith("/git/ref/heads%2F"):
                    return {"object": {"sha": "a" * 40}}
                if path.startswith("/compare/"):
                    return {"files": [{"filename": "README.md", "status": "modified"}]}
                raise AssertionError((method, path, payload))

        with mock.patch.object(open_publication_pr, "GitHubClient", FakeClient):
            with self.assertRaisesRegex(open_publication_pr.GitHubAPIError, "non-allowlisted"):
                open_publication_pr.execute(self.publication, "synthetic-token")
        self.assertFalse(any(method in {"PUT", "POST", "PATCH"} for method, *_ in FakeClient.calls))

    def test_execute_updates_same_open_pr_branch(self):
        old = deepcopy(self.publication)
        old["intro"] = "이전 공개 projection입니다."
        old["content_sha256"] = content_sha256(old)
        encoded_old = open_publication_pr.base64.b64encode(
            pretty_json(old).encode("utf-8")
        ).decode("ascii")

        class FakeClient:
            calls = []

            def __init__(self, _token):
                pass

            def request(self, method, path, payload=None):
                self.calls.append((method, path, payload))
                if path.startswith("/contents/") and "ref=main" in path:
                    raise open_publication_pr.GitHubAPIError(404, "not found")
                if path.startswith("/git/ref/heads%2F"):
                    return {"object": {"sha": "a" * 40}}
                if path.startswith("/compare/"):
                    return {"files": [{"filename": "publications/insight-20260819-20260826/publication.json", "status": "modified"}]}
                if path.startswith("/contents/") and "ref=publication" in path:
                    return {"encoding": "base64", "content": encoded_old, "sha": "b" * 40}
                if method == "PUT" and path.startswith("/contents/"):
                    return {"commit": {"sha": "c" * 40}}
                if path.startswith("/pulls?"):
                    return [{"number": 7, "title": "old", "body": "old", "html_url": "https://example.test/pr/7"}]
                if method == "PATCH" and path == "/pulls/7":
                    return {"number": 7, "html_url": "https://example.test/pr/7"}
                raise AssertionError((method, path, payload))

        with mock.patch.object(open_publication_pr, "GitHubClient", FakeClient):
            result = open_publication_pr.execute(self.publication, "synthetic-token")
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["pull_request"], "https://example.test/pr/7")
        self.assertEqual(len(result["mutations"]), 2)
        methods = [method for method, *_ in FakeClient.calls]
        self.assertEqual(methods.count("PUT"), 1)
        self.assertEqual(methods.count("PATCH"), 1)


if __name__ == "__main__":
    unittest.main()
