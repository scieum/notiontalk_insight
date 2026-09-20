#!/usr/bin/env python3
"""Build the public allowlist projection from an A6 ``final.json``.

This script never copies the input object wholesale.  Every public field is
selected explicitly so evidence IDs and future internal fields fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.common.consent import get_consent_mode
from lib.publication import (
    PublicationValidationError,
    content_sha256,
    pretty_json,
    parse_rfc3339,
    parse_full_date,
    publication_path,
    validate_publication,
    strict_json_loads,
)


def _object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PublicationValidationError(f"{where} must be an object")
    return value


def _array(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise PublicationValidationError(f"{where} must be an array")
    return value


def _required_text(obj: dict[str, Any], key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PublicationValidationError(f"{where}.{key} must be a non-empty string")
    return value


def _optional_text(obj: dict[str, Any], key: str, where: str) -> str | None:
    value = obj.get(key)
    if value is not None and not isinstance(value, str):
        raise PublicationValidationError(f"{where}.{key} must be a string or null")
    return value


def _string_list(obj: dict[str, Any], key: str, where: str) -> list[str]:
    value = obj.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise PublicationValidationError(f"{where}.{key} must be a list of non-empty strings")
    return list(value)


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_a6_privacy_evidence(final: dict[str, Any], expected_mode: str) -> str:
    evidence = _object(final.get("privacy_evidence"), "final.privacy_evidence")
    required = {
        "evidence_version",
        "stage",
        "consent_mode",
        "pii_scan_passed",
        "nickname_policy_passed",
        "source_sha256",
        "period_start",
        "period_end",
        "payload_sha256",
    }
    if set(evidence) != required:
        raise PublicationValidationError(
            "final.privacy_evidence must contain only the immutable A6 evidence fields"
        )
    if evidence.get("evidence_version") != 1 or evidence.get("stage") != "A6":
        raise PublicationValidationError("final.privacy_evidence has an unsupported provenance")
    applied_mode = evidence.get("consent_mode")
    if applied_mode not in {"anon", "named"}:
        raise PublicationValidationError("final.privacy_evidence.consent_mode is invalid")
    if applied_mode != expected_mode:
        raise PublicationValidationError(
            f"consent mode drift: A6 applied {applied_mode!r}, current config is {expected_mode!r}; rerun A6"
        )
    if evidence.get("pii_scan_passed") is not True:
        raise PublicationValidationError("A6 PII scan did not pass")
    if evidence.get("nickname_policy_passed") is not True:
        raise PublicationValidationError("A6 nickname policy did not pass")
    for key in ("source_sha256", "payload_sha256"):
        value = evidence.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise PublicationValidationError(f"final.privacy_evidence.{key} is not a SHA-256 digest")

    for key in ("period_start", "period_end"):
        value = evidence.get(key)
        try:
            parsed = parse_full_date(value) if isinstance(value, str) else None
        except ValueError as exc:
            raise PublicationValidationError(
                f"final.privacy_evidence.{key} must be YYYY-MM-DD"
            ) from exc
        if parsed is None or parsed.isoformat() != value or final.get(key) != value:
            raise PublicationValidationError(
                f"final.privacy_evidence.{key} must match the digest-bound A6 payload"
            )

    payload = dict(final)
    payload.pop("privacy_evidence", None)
    actual = _canonical_sha256(payload)
    if not hmac.compare_digest(evidence["payload_sha256"], actual):
        raise PublicationValidationError(
            "final.privacy_evidence.payload_sha256 does not match the A6 payload; rerun A6"
        )
    return applied_mode


def normalize_generated_at(value: str) -> str:
    try:
        parsed = parse_rfc3339(value)
    except ValueError as exc:
        raise PublicationValidationError("generated_at must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise PublicationValidationError("generated_at must include a timezone")
    utc_value = parsed.astimezone(timezone.utc).replace(microsecond=0)
    return utc_value.isoformat().replace("+00:00", "Z")


def period_label(start: date, end: date) -> str:
    if start.year == end.year:
        return f"{start:%Y.%m.%d} - {end:%m.%d}"
    return f"{start:%Y.%m.%d} - {end:%Y.%m.%d}"


def default_content_id(start: date, end: date) -> str:
    return f"insight-{start:%Y%m%d}-{end:%Y%m%d}"


def default_slug(issue: int) -> str:
    return f"community-insight-{issue:03d}"


def project_publication(
    final: dict[str, Any],
    *,
    start: date,
    end: date,
    issue: int,
    generated_at: str,
    consent_mode: str,
    content_id: str | None = None,
    slug: str | None = None,
) -> dict[str, Any]:
    if start > end:
        raise PublicationValidationError("period start must not be after period end")
    if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
        raise PublicationValidationError("issue must be an integer >= 1")

    final = _object(final, "final")
    applied_consent_mode = verify_a6_privacy_evidence(final, consent_mode)
    evidence = _object(final["privacy_evidence"], "final.privacy_evidence")
    if start.isoformat() != evidence["period_start"] or end.isoformat() != evidence["period_end"]:
        raise PublicationValidationError(
            "requested publication period does not match immutable A6 period provenance"
        )
    report = _object(final.get("report"), "final.report")
    sections = _object(report.get("sections"), "final.report.sections")
    topics_source = _array(sections.get("topics", []), "final.report.sections.topics")
    unresolved_source = _array(
        sections.get("unresolved", []), "final.report.sections.unresolved"
    )
    faq_source = _array(final.get("faq", []), "final.faq")
    tips_source = _array(final.get("tips", []), "final.tips")
    actions_source = _array(final.get("actions", []), "final.actions")

    raw_hook = report.get("hook_title")
    if raw_hook is not None and (not isinstance(raw_hook, str) or not raw_hook.strip()):
        raise PublicationValidationError("final.report.hook_title must be a non-empty string or null")
    hook_title = raw_hook.strip() if isinstance(raw_hook, str) else None
    title = f"노션하는 교사톡 주간 인사이트 {issue}호"

    topics = []
    for index, raw in enumerate(topics_source):
        item = _object(raw, f"final.report.sections.topics[{index}]")
        topics.append(
            {
                "topic": _required_text(item, "topic", f"topics[{index}]"),
                "why": _required_text(item, "why", f"topics[{index}]"),
            }
        )

    faq = []
    for index, raw in enumerate(faq_source):
        item = _object(raw, f"final.faq[{index}]")
        faq.append(
            {
                "question": _required_text(item, "question", f"faq[{index}]"),
                "answer": _required_text(item, "answer", f"faq[{index}]"),
                "tags": _string_list(item, "tags", f"faq[{index}]"),
                "practical_tip": _optional_text(item, "practical_tip", f"faq[{index}]"),
            }
        )

    unresolved = []
    for index, raw in enumerate(unresolved_source):
        item = _object(raw, f"final.report.sections.unresolved[{index}]")
        unresolved.append(
            {"question": _required_text(item, "question", f"unresolved[{index}]")}
        )

    tips = []
    for index, raw in enumerate(tips_source):
        item = _object(raw, f"final.tips[{index}]")
        tips.append(
            {
                "title": _required_text(item, "title", f"tips[{index}]"),
                "body": _required_text(item, "body", f"tips[{index}]"),
                "feature_tags": _string_list(item, "feature_tags", f"tips[{index}]"),
                "practical_tip": _optional_text(item, "practical_tip", f"tips[{index}]"),
            }
        )

    actions = []
    for index, raw in enumerate(actions_source):
        item = _object(raw, f"final.actions[{index}]")
        actions.append(
            {
                "type": _required_text(item, "type", f"actions[{index}]"),
                "text": _required_text(item, "text", f"actions[{index}]"),
                "owner": _optional_text(item, "owner_nickname", f"actions[{index}]"),
                "due": _optional_text(item, "due", f"actions[{index}]"),
            }
        )

    stats = _object(sections.get("stats", {}), "final.report.sections.stats")
    count_contract = {
        "질문 수": len(faq_source) + len(unresolved_source),
        "그중 해결된 수": len(faq_source),
    }
    for label, expected in count_contract.items():
        if label in stats and stats[label] != expected:
            raise PublicationValidationError(
                f"final.report.sections.stats[{label!r}]={stats[label]!r} "
                f"does not match approved item count {expected}"
            )
    publication: dict[str, Any] = {
        "schema_version": 1,
        "kind": "community_insight",
        "content_id": content_id or default_content_id(start, end),
        "slug": slug or default_slug(issue),
        "issue": issue,
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": period_label(start, end),
        },
        "title": title,
        "hook_title": hook_title,
        "intro": _required_text(report, "intro", "final.report"),
        "stats": dict(stats),
        "topics": topics,
        "faq": faq,
        "unresolved": unresolved,
        "tips": tips,
        "actions": actions,
        "privacy": {
            "consent_mode": applied_consent_mode,
            "pii_scan_passed": True,
            "source_ids_stripped": True,
        },
        "generated_at": normalize_generated_at(generated_at),
    }
    publication["content_sha256"] = content_sha256(publication)

    # Full-count preservation is checked before the independent validator.  A
    # future projection edit may rename fields, but it must never silently drop
    # an item that A6 approved.
    expected_counts = {
        "topics": len(topics_source),
        "faq": len(faq_source),
        "unresolved": len(unresolved_source),
        "tips": len(tips_source),
        "actions": len(actions_source),
    }
    actual_counts = {key: len(publication[key]) for key in expected_counts}
    if actual_counts != expected_counts:
        raise PublicationValidationError(
            f"projection item counts changed: expected={expected_counts}, actual={actual_counts}"
        )

    validate_publication(publication)
    return publication


def write_if_changed(path: Path, publication: dict[str, Any]) -> str:
    rendered = pretty_json(publication)
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return "unchanged"
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return "updated" if existed else "created"


def parse_date(value: str) -> date:
    try:
        return parse_full_date(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="A6 final.json -> safe public publication.json")
    parser.add_argument("--final", required=True, type=Path, help="A6 final.json path")
    parser.add_argument("--period-start", required=True, type=parse_date)
    parser.add_argument("--period-end", required=True, type=parse_date)
    parser.add_argument("--issue", required=True, type=int)
    parser.add_argument("--generated-at", required=True, help="RFC 3339 timestamp with timezone")
    parser.add_argument("--content-id", help="optional revision content ID")
    parser.add_argument("--slug", help="optional revision slug")
    parser.add_argument("--out", type=Path, help="write to this local path instead of publications/<content-id>/publication.json")
    parser.add_argument("--dry-run", action="store_true", help="validate and print without writing")
    args = parser.parse_args()

    try:
        final = strict_json_loads(args.final.read_text(encoding="utf-8"))
        publication = project_publication(
            final,
            start=args.period_start,
            end=args.period_end,
            issue=args.issue,
            generated_at=args.generated_at,
            consent_mode=get_consent_mode(),
            content_id=args.content_id,
            slug=args.slug,
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        PublicationValidationError,
    ) as exc:
        raise SystemExit(f"[build-publication] blocked: {exc}") from exc

    relative_target = publication_path(publication["content_id"])
    target = args.out if args.out is not None else ROOT / relative_target
    display_target = target.relative_to(ROOT) if target.is_relative_to(ROOT) else target
    if args.dry_run:
        print(pretty_json(publication), end="")
        print(f"[build-publication] dry-run: would write {display_target}", file=sys.stderr)
        return

    existed = target.exists()
    rendered = pretty_json(publication)
    if existed and target.read_text(encoding="utf-8") == rendered:
        status = "unchanged"
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
        status = "updated" if existed else "created"
    print(f"[build-publication] {status}: {display_target}")


if __name__ == "__main__":
    main()
