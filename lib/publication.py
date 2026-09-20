"""Public TalkInsight publication contract, validation, and hashing.

Only data that passes this module may leave the local A6 output directory.  The
projection itself lives in ``scripts/build_publication.py``; this module is the
independent, secrets-free verifier used locally and in pull-request CI.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
PUBLICATION_KIND = "community_insight"
MAX_PUBLICATION_BYTES = 256 * 1024
MAX_JSON_DEPTH = 12
MAX_JSON_NODES = 5_000
MAX_TOTAL_ITEMS = 250
MAX_STATS_VALUE = 10_000_000
ALLOWED_STATS = frozenset(
    {"메시지 수", "함께한 선생님", "스레드 수", "질문 수", "그중 해결된 수"}
)
COLLECTION_LIMITS = {
    "topics": 10,
    "faq": 100,
    "unresolved": 100,
    "tips": 100,
    "actions": 100,
}
CONTENT_ID_RE = re.compile(r"^insight-\d{8}-\d{8}(?:-r[1-9]\d*)?$")
SLUG_RE = re.compile(r"^community-insight-\d{3,}(?:-r[1-9]\d*)?$")
ANON_OWNER_RE = re.compile(r"^(?:1000쌤|함께한 선생님[A-Z]+)$")
PUBLICATION_PATH_RE = re.compile(
    r"^publications/(?P<content_id>insight-\d{8}-\d{8}(?:-r[1-9]\d*)?)/publication\.json$"
)

# Exact key names only.  In particular, ``source_ids_stripped`` is allowed as
# the positive privacy assertion while all evidence/source identifiers remain
# forbidden.
FORBIDDEN_KEYS = frozenset(
    {
        "id",
        "period_id",
        "source_period_id",
        "source_message_id",
        "source_message_ids",
        "source_thread_id",
        "source_thread_ids",
        "message_ids",
        "thread_id",
        "merge_into",
        "eval",
        "evaluation",
        "verdict",
        "generation",
        "local_path",
        "output_path",
        "log",
        "logs",
        "api_response",
        "raw",
        "raw_messages",
    }
)

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "phone",
        re.compile(
            r"(?<!\d)(?:(?:\+?82[- .]?(?:0[- .]?)?)|0|\(0)(?:1[016789]|2|[3-6][1-5]|70|50\d)\)?"
            r"[- .]?\d{3,4}[- .]?\d{4}(?!\d)"
        ),
    ),
    (
        "international_phone",
        re.compile(
            r"(?<![\w])\+\d{1,3}[- .]?(?:\(\d{1,4}\)[- .]?|\d{1,4}[- .]?)"
            r"\d{2,4}[- .]?\d{3,4}(?!\w)"
        ),
    ),
    ("service_phone", re.compile(r"(?<!\d)1\d{3}[- .]?\d{4}(?!\d)")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("rrn", re.compile(r"(?<!\d)\d{6}-?[1-8]\d{6}(?!\d)")),
    ("url", re.compile(r"(?:https?://|www\.)\S+", re.I)),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~-]{16,}\b", re.I)),
    ("notion_token", re.compile(r"\b(?:ntn_[A-Za-z0-9_-]{20,}|secret_[A-Za-z0-9_-]{20,})\b")),
    ("oauth_token", re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    (
        "assigned_secret",
        re.compile(
            r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password)"
            r"\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{12,}",
            re.I,
        ),
    ),
    ("bare_notion_share", re.compile(r"(?<![\w.-])(?:www\.)?notion\.(?:so|site)/[^\s<>'\"]+", re.I)),
    ("share_token", re.compile(r"\b(?:share|shared|invite)[/_?=&:-][A-Za-z0-9_-]{16,}\b", re.I)),
    ("uuid", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I)),
)
_LOCAL_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mac_local_path", re.compile(r"(?:^|[\s('\"])/Users/[^\s)'\"]+")),
    ("linux_local_path", re.compile(r"(?:^|[\s('\"])/home/[^\s)'\"]+")),
    ("windows_local_path", re.compile(r"\b[A-Za-z]:\\[^\s\"']+")),
    ("windows_slash_path", re.compile(r"\b[A-Za-z]:/[^\s\"']+")),
    ("windows_unc_path", re.compile(r"(?:^|[\s('\"])(?:\\\\|//)[^\s)\"']+")),
    ("tilde_local_path", re.compile(r"(?:^|[\s('\"])~[/\\][^\s)\"']+")),
    ("relative_local_path", re.compile(r"(?:^|[\s('\"])(?:\.\.?/)[^\s)\"']+")),
    (
        "sensitive_posix_path",
        re.compile(r"(?:^|[\s('\"])/(?:private|tmp|var|etc|mnt|Volumes)/[^\s)\"']+"),
    ),
    ("private_output_path", re.compile(r"(?:^|[\s('\"])(?:output|inbox)/[^\s)'\"]+")),
)

_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d"
    r"(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)
_FULL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "content_id",
        "slug",
        "issue",
        "period",
        "title",
        "hook_title",
        "intro",
        "stats",
        "topics",
        "faq",
        "unresolved",
        "tips",
        "actions",
        "privacy",
        "generated_at",
        "content_sha256",
    }
)


class PublicationValidationError(ValueError):
    """Raised when a public projection is unsafe or outside the contract."""


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PublicationValidationError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def strict_json_loads(value: str) -> Any:
    """Parse JSON while rejecting duplicate keys at every object nesting level."""
    return json.loads(value, object_pairs_hook=_reject_duplicate_object_keys)


def strict_json_load_bytes(raw: bytes, *, source: str = "JSON") -> Any:
    """Apply the publication byte ceiling before UTF-8 and duplicate-safe parsing."""
    if not isinstance(raw, bytes):
        raise PublicationValidationError(f"{source} input must be bytes")
    if len(raw) > MAX_PUBLICATION_BYTES:
        raise PublicationValidationError(
            f"{source} exceeds the {MAX_PUBLICATION_BYTES}-byte publication limit"
        )
    try:
        return strict_json_loads(raw.decode("utf-8"))
    except UnicodeError as exc:
        raise PublicationValidationError(f"{source} is not valid UTF-8") from exc


def _same_json_value(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's ``True == 1`` coercion."""
    return type(left) is type(right) and left == right


@lru_cache(maxsize=1)
def _publication_schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "schemas" / "publication.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_type_matches(instance: Any, expected: str) -> bool:
    return {
        "object": isinstance(instance, dict),
        "array": isinstance(instance, list),
        "string": isinstance(instance, str),
        "integer": isinstance(instance, int) and not isinstance(instance, bool),
        "null": instance is None,
    }.get(expected, False)


def _resolve_local_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise PublicationValidationError(f"unsupported non-local schema ref: {ref}")
    current: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[part]
    if not isinstance(current, dict):
        raise PublicationValidationError(f"schema ref is not an object: {ref}")
    return current


def _schema_errors(
    instance: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    where: str = "$",
) -> list[str]:
    if "$ref" in schema:
        return _schema_errors(instance, _resolve_local_ref(root_schema, schema["$ref"]), root_schema, where)

    errors: list[str] = []
    if "oneOf" in schema:
        matches = [
            option
            for option in schema["oneOf"]
            if not _schema_errors(instance, option, root_schema, where)
        ]
        if len(matches) != 1:
            return [f"{where} must match exactly one schema option"]
        return []

    if "const" in schema and not _same_json_value(instance, schema["const"]):
        errors.append(f"{where} must equal {schema['const']!r}")
    if "enum" in schema and not any(
        _same_json_value(instance, option) for option in schema["enum"]
    ):
        errors.append(f"{where} must be one of {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(_schema_type_matches(instance, item) for item in expected_types):
            errors.append(f"{where} has the wrong JSON type")
            return errors

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            errors.append(f"{where} has more than maxProperties")
        for key in sorted(required - set(instance)):
            errors.append(f"{where} is missing required key {key!r}")
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child_where = f"{where}.{key}"
            if key in properties:
                errors.extend(_schema_errors(value, properties[key], root_schema, child_where))
            elif additional is False:
                errors.append(f"{where} contains unexpected key {key!r}")
            elif isinstance(additional, dict):
                errors.extend(_schema_errors(value, additional, root_schema, child_where))
            if "propertyNames" in schema:
                errors.extend(_schema_errors(key, schema["propertyNames"], root_schema, child_where))

    if isinstance(instance, list):
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{where} has more than maxItems")
        if schema.get("uniqueItems"):
            rendered_items = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in instance]
            if len(rendered_items) != len(set(rendered_items)):
                errors.append(f"{where} must contain unique items")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(instance):
                errors.extend(_schema_errors(item, schema["items"], root_schema, f"{where}[{index}]"))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{where} is shorter than minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{where} is longer than maxLength")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{where} does not match the required pattern")
        if schema.get("format") == "date":
            try:
                parse_full_date(instance)
            except ValueError:
                errors.append(f"{where} is not a YYYY-MM-DD full-date")
        if schema.get("format") == "date-time":
            try:
                parse_rfc3339(instance)
            except ValueError:
                errors.append(f"{where} is not an RFC 3339 timestamp")

    if (
        isinstance(instance, (int, float))
        and not isinstance(instance, bool)
        and "minimum" in schema
        and instance < schema["minimum"]
    ):
        errors.append(f"{where} is below minimum")
    if (
        isinstance(instance, (int, float))
        and not isinstance(instance, bool)
        and "maximum" in schema
        and instance > schema["maximum"]
    ):
        errors.append(f"{where} is above maximum")
    return errors


def canonical_json(publication: dict[str, Any], *, include_hash: bool = False) -> bytes:
    """Return the exact canonical JSON bytes used for SHA-256."""
    payload = dict(publication)
    if not include_hash:
        payload.pop("content_sha256", None)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def content_sha256(publication: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(publication)).hexdigest()


def pretty_json(publication: dict[str, Any]) -> str:
    return json.dumps(
        publication,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"


def publication_path(content_id: str) -> Path:
    if not CONTENT_ID_RE.fullmatch(content_id):
        raise PublicationValidationError(f"invalid content_id: {content_id!r}")
    return Path("publications") / content_id / "publication.json"


def _walk(value: Any, location: str = "$"):
    yield location, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{location}[{index}]")


def _complexity_errors(value: Any) -> list[str]:
    errors: list[str] = []
    stack = [(value, "$", 0)]
    nodes = 0
    while stack:
        current, location, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            errors.append(f"$ exceeds the {MAX_JSON_NODES} JSON-node limit")
            break
        if depth > MAX_JSON_DEPTH:
            errors.append(f"{location} exceeds the JSON depth limit {MAX_JSON_DEPTH}")
            continue
        if isinstance(current, dict):
            stack.extend((child, f"{location}.{key}", depth + 1) for key, child in current.items())
        elif isinstance(current, list):
            stack.extend((child, f"{location}[{index}]", depth + 1) for index, child in enumerate(current))
        elif isinstance(current, str) and len(current) > 8_000:
            errors.append(f"{location} exceeds the 8000-character string limit")
    return errors


def _privacy_pattern_errors(value: str, location: str, *, consent_mode: Any) -> list[str]:
    errors: list[str] = []
    for name, pattern in _PII_PATTERNS + _LOCAL_PATH_PATTERNS:
        if pattern.search(value):
            errors.append(f"{location} contains forbidden {name}")
    if consent_mode == "anon":
        scrubbed = re.sub(
            r"1000쌤|함께한 선생님[A-Z]+|선생님|관리자님|운영자님",
            "",
            value,
        )
        if re.search(r"[가-힣A-Za-z0-9_]{2,20}(?:님|쌤)", scrubbed):
            errors.append(f"{location} contains a person-like honorific in anon mode")
    return errors


def _require_keys(obj: Any, required: set[str], allowed: set[str], where: str, errors: list[str]) -> None:
    if not isinstance(obj, dict):
        errors.append(f"{where} must be an object")
        return
    missing = required - set(obj)
    extra = set(obj) - allowed
    if missing:
        errors.append(f"{where} missing keys: {sorted(missing)}")
    if extra:
        errors.append(f"{where} unexpected keys: {sorted(extra)}")


def _string(value: Any, where: str, errors: list[str], *, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        errors.append(f"{where} must be a {'possibly empty ' if allow_empty else 'non-empty '}string")


def _optional_string(value: Any, where: str, errors: list[str]) -> None:
    if value is not None and not isinstance(value, str):
        errors.append(f"{where} must be a string or null")


def _string_list(value: Any, where: str, errors: list[str]) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{where} must be a list of non-empty strings")


def _validate_list_objects(
    value: Any,
    where: str,
    required: set[str],
    optional: set[str],
    errors: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        errors.append(f"{where} must be an array")
        return []
    objects: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        item_where = f"{where}[{index}]"
        _require_keys(item, required, required | optional, item_where, errors)
        if isinstance(item, dict):
            objects.append(item)
    return objects


def _validate_iso_date(value: Any, where: str, errors: list[str]) -> date | None:
    if not isinstance(value, str):
        errors.append(f"{where} must be an ISO date")
        return None
    try:
        return parse_full_date(value)
    except ValueError:
        errors.append(f"{where} must be a YYYY-MM-DD full-date")
        return None


def parse_full_date(value: str) -> date:
    if not isinstance(value, str) or _FULL_DATE_RE.fullmatch(value) is None:
        raise ValueError("not a YYYY-MM-DD full-date")
    return date.fromisoformat(value)


def _validate_generated_at(value: Any, errors: list[str]) -> None:
    if not isinstance(value, str):
        errors.append("$.generated_at must be an RFC 3339 timestamp")
        return
    try:
        parse_rfc3339(value)
    except ValueError:
        errors.append("$.generated_at must be an RFC 3339 timestamp")


def parse_rfc3339(value: str) -> datetime:
    """Parse the RFC 3339 timestamp profile, rejecting broad ISO 8601 variants."""
    if not isinstance(value, str) or _RFC3339_RE.fullmatch(value) is None:
        raise ValueError("not strict RFC 3339")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized)


def validate_publication(publication: Any, *, verify_hash: bool = True) -> None:
    """Validate schema shape, privacy invariants, and the canonical hash."""
    errors = _complexity_errors(publication)
    try:
        encoded_size = len(canonical_json(publication, include_hash=True)) if isinstance(publication, dict) else 0
    except (TypeError, ValueError, RecursionError) as exc:
        errors.append(f"$ cannot be encoded as strict JSON: {exc}")
        encoded_size = 0
    if encoded_size > MAX_PUBLICATION_BYTES:
        errors.append(f"$ exceeds the {MAX_PUBLICATION_BYTES}-byte publication limit")
    schema = _publication_schema()
    errors.extend(f"schema: {error}" for error in _schema_errors(publication, schema, schema))
    _require_keys(publication, set(TOP_LEVEL_KEYS), set(TOP_LEVEL_KEYS), "$", errors)
    if not isinstance(publication, dict):
        raise PublicationValidationError("; ".join(errors))

    if publication.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"$.schema_version must equal {SCHEMA_VERSION}")
    if publication.get("kind") != PUBLICATION_KIND:
        errors.append(f"$.kind must equal {PUBLICATION_KIND!r}")

    content_id = publication.get("content_id")
    if not isinstance(content_id, str) or not CONTENT_ID_RE.fullmatch(content_id):
        errors.append("$.content_id has an invalid format")
    slug = publication.get("slug")
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        errors.append("$.slug has an invalid format")
    issue = publication.get("issue")
    if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
        errors.append("$.issue must be an integer >= 1")

    period = publication.get("period")
    _require_keys(period, {"start", "end", "label"}, {"start", "end", "label"}, "$.period", errors)
    if isinstance(period, dict):
        start = _validate_iso_date(period.get("start"), "$.period.start", errors)
        end = _validate_iso_date(period.get("end"), "$.period.end", errors)
        _string(period.get("label"), "$.period.label", errors)
        if start and end and start > end:
            errors.append("$.period.start must not be after $.period.end")
        if start and end:
            expected_label = (
                f"{start:%Y.%m.%d} - {end:%m.%d}"
                if start.year == end.year
                else f"{start:%Y.%m.%d} - {end:%Y.%m.%d}"
            )
            if period.get("label") != expected_label:
                errors.append(f"$.period.label must equal {expected_label!r}")
            expected_content_id = f"insight-{start:%Y%m%d}-{end:%Y%m%d}"
            if isinstance(content_id, str) and not (
                content_id == expected_content_id
                or re.fullmatch(re.escape(expected_content_id) + r"-r[1-9]\d*", content_id)
            ):
                errors.append("$.content_id dates must match $.period")

    if isinstance(slug, str) and isinstance(issue, int) and not isinstance(issue, bool):
        slug_match = re.fullmatch(r"community-insight-(\d+)(-r[1-9]\d*)?", slug)
        if slug_match and int(slug_match.group(1)) != issue:
            errors.append("$.slug issue number must match $.issue")
        if isinstance(content_id, str):
            content_revision = re.search(r"(-r[1-9]\d*)$", content_id)
            slug_revision = re.search(r"(-r[1-9]\d*)$", slug)
            if (content_revision.group(1) if content_revision else None) != (
                slug_revision.group(1) if slug_revision else None
            ):
                errors.append("content_id and slug revision suffixes must match")

    _string(publication.get("title"), "$.title", errors)
    if publication.get("hook_title") is not None:
        _string(publication.get("hook_title"), "$.hook_title", errors)
    _string(publication.get("intro"), "$.intro", errors)

    stats = publication.get("stats")
    if not isinstance(stats, dict):
        errors.append("$.stats must be an object")
    else:
        unexpected_stats = set(stats) - ALLOWED_STATS
        if unexpected_stats:
            errors.append(f"$.stats contains unsupported labels: {sorted(unexpected_stats)}")
        for key, value in stats.items():
            if not isinstance(key, str) or not key.strip():
                errors.append("$.stats keys must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                errors.append(f"$.stats[{key!r}] must be an integer >= 0")
            elif value > MAX_STATS_VALUE:
                errors.append(f"$.stats[{key!r}] exceeds {MAX_STATS_VALUE}")

        expected_counts = {
            "질문 수": len(publication.get("faq", [])) + len(publication.get("unresolved", []))
            if isinstance(publication.get("faq"), list) and isinstance(publication.get("unresolved"), list)
            else None,
            "그중 해결된 수": len(publication.get("faq", []))
            if isinstance(publication.get("faq"), list)
            else None,
        }
        for label, expected in expected_counts.items():
            if label in stats and expected is not None and stats[label] != expected:
                errors.append(f"$.stats[{label!r}] must equal {expected}")

    total_items = 0
    for key, limit in COLLECTION_LIMITS.items():
        value = publication.get(key)
        if isinstance(value, list):
            total_items += len(value)
            if len(value) > limit:
                errors.append(f"$.{key} exceeds the {limit}-item limit")
    if total_items > MAX_TOTAL_ITEMS:
        errors.append(f"publication exceeds the {MAX_TOTAL_ITEMS}-item total limit")

    for item in _validate_list_objects(publication.get("topics"), "$.topics", {"topic", "why"}, set(), errors):
        _string(item.get("topic"), "topic.topic", errors)
        _string(item.get("why"), "topic.why", errors)

    for item in _validate_list_objects(
        publication.get("faq"),
        "$.faq",
        {"question", "answer", "tags", "practical_tip"},
        set(),
        errors,
    ):
        _string(item.get("question"), "faq.question", errors)
        _string(item.get("answer"), "faq.answer", errors)
        _string_list(item.get("tags"), "faq.tags", errors)
        _optional_string(item.get("practical_tip"), "faq.practical_tip", errors)

    for item in _validate_list_objects(publication.get("unresolved"), "$.unresolved", {"question"}, set(), errors):
        _string(item.get("question"), "unresolved.question", errors)

    for item in _validate_list_objects(
        publication.get("tips"),
        "$.tips",
        {"title", "body", "feature_tags", "practical_tip"},
        set(),
        errors,
    ):
        _string(item.get("title"), "tips.title", errors)
        _string(item.get("body"), "tips.body", errors)
        _string_list(item.get("feature_tags"), "tips.feature_tags", errors)
        _optional_string(item.get("practical_tip"), "tips.practical_tip", errors)

    for item in _validate_list_objects(
        publication.get("actions"),
        "$.actions",
        {"type", "text", "owner", "due"},
        set(),
        errors,
    ):
        if item.get("type") not in {"decision", "action"}:
            errors.append("actions.type must be decision or action")
        _string(item.get("text"), "actions.text", errors)
        _optional_string(item.get("owner"), "actions.owner", errors)
        due = item.get("due")
        if due is not None:
            _validate_iso_date(due, "actions.due", errors)

    privacy = publication.get("privacy")
    _require_keys(
        privacy,
        {"consent_mode", "pii_scan_passed", "source_ids_stripped"},
        {"consent_mode", "pii_scan_passed", "source_ids_stripped"},
        "$.privacy",
        errors,
    )
    if isinstance(privacy, dict):
        if privacy.get("consent_mode") not in {"anon", "named"}:
            errors.append("$.privacy.consent_mode must be anon or named")
        if privacy.get("pii_scan_passed") is not True:
            errors.append("$.privacy.pii_scan_passed must be true")
        if privacy.get("source_ids_stripped") is not True:
            errors.append("$.privacy.source_ids_stripped must be true")

    if isinstance(publication.get("actions"), list) and isinstance(privacy, dict):
        for index, action in enumerate(publication["actions"]):
            if not isinstance(action, dict):
                continue
            owner = action.get("owner")
            if privacy.get("consent_mode") == "anon" and owner is not None:
                if not isinstance(owner, str) or not ANON_OWNER_RE.fullmatch(owner):
                    errors.append(
                        f"$.actions[{index}].owner must be null or an approved pseudonym in anon mode"
                    )

    _validate_generated_at(publication.get("generated_at"), errors)
    digest = publication.get("content_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        errors.append("$.content_sha256 must be a lowercase SHA-256 hex digest")
    elif verify_hash and digest != content_sha256(publication):
        errors.append("$.content_sha256 does not match canonical JSON")

    consent_mode = privacy.get("consent_mode") if isinstance(privacy, dict) else None
    for location, value in _walk(publication):
        if isinstance(value, dict):
            bad = FORBIDDEN_KEYS.intersection(value)
            if bad:
                errors.append(f"{location} contains forbidden keys: {sorted(bad)}")
            for key in value:
                if isinstance(key, str):
                    errors.extend(_privacy_pattern_errors(key, f"{location} key {key!r}", consent_mode=consent_mode))
        if isinstance(value, str):
            if location != "$.content_sha256":
                errors.extend(_privacy_pattern_errors(value, location, consent_mode=consent_mode))

    if errors:
        raise PublicationValidationError("\n".join(dict.fromkeys(errors)))


def load_publication(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        data = strict_json_load_bytes(raw, source=f"publication {path}")
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationValidationError(f"cannot read publication {path}: {exc}") from exc
    validate_publication(data)
    return data


def count_items(publication: dict[str, Any]) -> dict[str, int]:
    return {
        key: len(publication[key])
        for key in ("topics", "faq", "unresolved", "tips", "actions")
    }
