"""A4 산출물 규칙 검증. LLM을 호출하지 않는다 — 순수 규칙이라 키 없이 검증된다.

설계서 §2.4 A4 성공 기준을 그대로 옮긴 것이다:
- 스키마 준수
- 모든 항목에 source_message_ids >= 1
- 잡담 유형 스레드에서 항목 생성 0건
- 리포트 필수 섹션 존재

여기에 환각 방어 두 가지를 더 얹었다. LLM이 (a) 대화에 없는 메시지 번호를
지어내거나 (b) 분류표에 없는 태그를 만들어내는 것이 실제로 가장 흔한 실패라서,
둘 다 오류로 잡아 재시도 프롬프트에 주입한다.

검증 실패는 예외가 아니라 **오류 문자열 목록**으로 돌려준다 — 그대로 재시도
프롬프트에 넣어야 하기 때문이다(설계서 A4 실패 처리).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from lib.common.paths import DOCS_DIR

VALID_TYPES = {"질문응답", "팁공유", "논의결정", "잡담"}
VALID_ACTION_TYPES = {"decision", "action"}
REQUIRED_REPORT_SECTIONS = ("topics", "resolved", "unresolved", "decisions")


def load_feature_tags() -> set[str]:
    """notion_feature_taxonomy.md의 표에서 태그 이름만 뽑는다."""
    path = DOCS_DIR / "notion_feature_taxonomy.md"
    if not path.exists():
        return set()
    tags = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*([^|]+?)\s*\|\s*[^|]+\|\s*$", line)
        if not m:
            continue
        tag = m.group(1).strip()
        if tag in ("태그", "") or set(tag) <= set("-: "):
            continue
        tags.add(tag)
    return tags


def _check_items(
    items,
    label: str,
    allowed_ids: set[int],
    errors: list[str],
    required_fields: tuple[str, ...],
) -> None:
    if not isinstance(items, list):
        errors.append(f"{label}이 배열이 아닙니다.")
        return
    for i, item in enumerate(items):
        where = f"{label}[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{where}가 객체가 아닙니다.")
            continue
        for f in required_fields:
            if not item.get(f):
                errors.append(f"{where}.{f}가 비어 있습니다.")

        ids = item.get("source_message_ids")
        if not isinstance(ids, list) or not ids:
            errors.append(f"{where}.source_message_ids가 비어 있습니다. 근거 없는 항목은 만들 수 없습니다.")
            continue
        bad = [x for x in ids if not isinstance(x, int) or x not in allowed_ids]
        if bad:
            errors.append(
                f"{where}.source_message_ids에 이 대화에 없는 번호가 있습니다: {bad}. "
                f"제시된 [숫자] 메시지 번호만 쓸 수 있습니다."
            )


def validate_thread_batch(obj, batch: list[dict], feature_tags: set[str] | None = None) -> list[str]:
    """배치 응답 하나를 검증한다. 반환값이 빈 리스트면 통과."""
    errors: list[str] = []
    if feature_tags is None:
        feature_tags = load_feature_tags()

    if not isinstance(obj, dict) or not isinstance(obj.get("threads"), list):
        return ["최상위가 {\"threads\": [...]} 형태의 객체가 아닙니다."]

    expected = {t["thread_id"]: t for t in batch}
    allowed_by_thread = {
        t["thread_id"]: {m["id"] for m in t["messages"]} for t in batch
    }

    seen = set()
    for i, th in enumerate(obj["threads"]):
        if not isinstance(th, dict):
            errors.append(f"threads[{i}]가 객체가 아닙니다.")
            continue
        tid = th.get("thread_id")
        if tid not in expected:
            errors.append(f"threads[{i}].thread_id '{tid}'는 이 배치에 없는 스레드입니다.")
            continue
        seen.add(tid)

        types = th.get("types")
        if not isinstance(types, list) or not types:
            errors.append(f"{tid}.types가 비어 있습니다.")
            types = []
        bad_types = [t for t in types if t not in VALID_TYPES]
        if bad_types:
            errors.append(f"{tid}.types에 허용되지 않은 값이 있습니다: {bad_types}. 허용: {sorted(VALID_TYPES)}")

        allowed_ids = allowed_by_thread[tid]
        counts = {}
        for key, fields in (
            ("faq", ("question", "answer")),
            ("tips", ("title", "body")),
            ("actions", ("text",)),
            ("unresolved", ("question",)),
        ):
            items = th.get(key, [])
            counts[key] = len(items) if isinstance(items, list) else 0
            _check_items(items, f"{tid}.{key}", allowed_ids, errors, fields)

        # 잡담 전용 스레드는 항목 0건이어야 한다 (설계서 A4 성공 기준)
        if types and set(types) == {"잡담"} and sum(counts.values()) > 0:
            errors.append(
                f"{tid}는 유형이 '잡담'뿐인데 항목이 {sum(counts.values())}건 있습니다. "
                f"잡담 스레드에서는 항목을 만들지 않습니다."
            )

        for a in th.get("actions", []) or []:
            if isinstance(a, dict) and a.get("type") not in VALID_ACTION_TYPES:
                errors.append(f"{tid}.actions의 type은 {sorted(VALID_ACTION_TYPES)} 중 하나여야 합니다.")

        for t_item in th.get("tips", []) or []:
            if not isinstance(t_item, dict):
                continue
            tags = t_item.get("feature_tags") or []
            bad_tags = [x for x in tags if x not in feature_tags]
            if bad_tags:
                errors.append(
                    f"{tid}.tips의 feature_tags에 분류표에 없는 태그가 있습니다: {bad_tags}. "
                    f"허용: {sorted(feature_tags)}"
                )

    missing = set(expected) - seen
    if missing:
        errors.append(f"응답에서 빠진 스레드가 있습니다: {sorted(missing)}. 배치의 모든 스레드를 넣어야 합니다.")

    return errors


def validate_report(obj, allowed_ids: set[int], allowed_thread_ids: set[str]) -> list[str]:
    """리포트 응답을 검증한다. 반환값이 빈 리스트면 통과."""
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["최상위가 객체가 아닙니다."]

    for f in ("hook_title", "intro", "title"):
        if not obj.get(f):
            errors.append(f"{f}가 비어 있습니다.")
    if isinstance(obj.get("hook_title"), str) and len(obj["hook_title"]) > 40:
        errors.append(f"hook_title이 {len(obj['hook_title'])}자입니다. 40자 이내로 줄이십시오.")

    sections = obj.get("sections")
    if not isinstance(sections, dict):
        return errors + ["sections가 객체가 아닙니다."]
    for name in REQUIRED_REPORT_SECTIONS:
        if name not in sections:
            errors.append(f"sections.{name}이 없습니다. 항목이 없더라도 빈 배열로 넣어야 합니다.")
        elif not isinstance(sections[name], list):
            errors.append(f"sections.{name}이 배열이 아닙니다.")

    for i, t in enumerate(sections.get("topics") or []):
        if not isinstance(t, dict):
            errors.append(f"sections.topics[{i}]가 객체가 아닙니다.")
            continue
        for f in ("topic", "why"):
            if not t.get(f):
                errors.append(f"sections.topics[{i}].{f}가 비어 있습니다.")
        bad = [x for x in (t.get("source_thread_ids") or []) if x not in allowed_thread_ids]
        if bad:
            errors.append(f"sections.topics[{i}].source_thread_ids에 없는 스레드가 있습니다: {bad}")

    _check_items(sections.get("resolved") or [], "sections.resolved", allowed_ids, errors, ("question", "answer"))
    _check_items(sections.get("unresolved") or [], "sections.unresolved", allowed_ids, errors, ("question",))
    _check_items(sections.get("decisions") or [], "sections.decisions", allowed_ids, errors, ("text",))

    bad = [x for x in (obj.get("source_thread_ids") or []) if x not in allowed_thread_ids]
    if bad:
        errors.append(f"source_thread_ids에 없는 스레드가 있습니다: {bad}")

    return errors
