"""A7: 주간 리포트를 노션 데이터베이스에 뉴스레터 페이지 한 장으로 발행한다.

설계서 §2.4 A7의 단순화 버전이다. 설계서는 노션 DB 5개(리포트/FAQ/팁/결정액션/
질문함)를 전제했지만, 운영자 결정(2026-08-27)에 따라 **DB 하나에 주 1회 페이지
한 장**을 넣는다. FAQ·팁·액션은 그 페이지 본문 안에 섹션으로 들어간다.

지켜야 하는 것:
- **리포트를 자동으로 `발행` 상태로 만들지 않는다**(C5). 항상 `초안`으로 만들고,
  사람이 검토(G-R)한 뒤 직접 바꾼다. 설정에서 status 값을 '발행'류로 두면 거부한다.
- 속성 이름을 코드에 박지 않는다. `docs/notion_publish.yaml`에서 읽고, 발행 전
  **실제 스키마와 대조**한다. 어긋나면 아무것도 만들지 않고 멈춘다.
- DB에 없는 속성을 새로 만들지 않는다. 남의 워크스페이스 스키마를 바꾸지 않는다.
- 같은 호를 두 번 만들지 않는다(매니페스트 + 제목 조회).

실행:
    python3 .claude/skills/notion-publish/scripts/publish_report.py \\
        --final output/insights/period_<id>.final.json \\
        --draft output/insights/period_<id>.draft.json \\
        --dry-run          # 노션에 쓰지 않고 무엇을 보낼지만 보여준다
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[4]
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import blocks as blk
import notion_client as nc

from lib.common.escalate import escalate
from lib.common.log import log_event
from lib.common.paths import DOCS_DIR, PUBLISHED_DIR
from lib.common.simple_yaml import load as load_yaml

CONFIG_PATH = DOCS_DIR / "notion_publish.yaml"

# 이 값들이 status로 설정되면 거부한다. 리포트 자동 발행은 금지다(C5).
FORBIDDEN_STATUS = {"발행", "published", "publish", "공개", "live", "완료"}


class ConfigError(RuntimeError):
    pass


def _title_opt(cfg: dict, key: str):
    """제목 형식 설정은 최상위에 둔다. properties 아래에 적어도 받아준다 —
    이 둘은 속성 이름이 아니라 형식 선택이라 헷갈리기 쉬운 자리다."""
    if key in cfg and cfg[key] is not None:
        return cfg[key]
    return (cfg.get("properties") or {}).get(key)


def _title_text(report: dict, cfg: dict, issue: int, week_label: str) -> str:
    source = _title_opt(cfg, "title_source") or "hook"
    if source == "hook":
        text = report.get("hook_title") or report.get("title") or "주간 리포트"
    elif source == "formal":
        text = report.get("title") or report.get("hook_title") or "주간 리포트"
    elif source == "issue":
        return f"{issue}호 · {week_label}"
    else:
        raise ConfigError(f"title_source 값이 올바르지 않습니다: {source!r} (hook/formal/issue)")
    if _title_opt(cfg, "title_prefix_issue"):
        return f"{issue}호 · {text}"
    return text


def validate_config(cfg: dict, schema: dict) -> dict:
    """설정의 속성 이름·타입을 실제 스키마와 대조한다. 어긋나면 발행하지 않는다."""
    props_cfg = cfg.get("properties") or {}
    if not isinstance(props_cfg, dict):
        raise ConfigError("notion_publish.yaml의 properties가 비어 있거나 형식이 다릅니다.")

    title_name = props_cfg.get("title")
    if not title_name:
        raise ConfigError("properties.title에 제목 속성 이름을 적어야 합니다. probe_db.py로 확인하십시오.")

    problems: list[str] = []
    expected_types = {
        "title": "title", "status": ("select", "status"), "issue_number": "number",
        "period_start": "date", "period_end": "date", "period_range": "date",
        "tags": "multi_select", "url": "url",
    }
    for key, name in props_cfg.items():
        if key in ("title_source", "title_prefix_issue") or not name:
            continue
        if name not in schema:
            problems.append(f"  properties.{key}: DB에 '{name}' 속성이 없습니다")
            continue
        want = expected_types.get(key)
        got = schema[name].get("type")
        if want and (got not in (want if isinstance(want, tuple) else (want,))):
            problems.append(f"  properties.{key}: '{name}'의 타입이 {got}입니다 (기대: {want})")

    status_value = cfg.get("status_draft_value")
    if props_cfg.get("status") and str(status_value).strip().lower() in FORBIDDEN_STATUS:
        problems.append(
            f"  status_draft_value가 '{status_value}'입니다. 리포트를 자동으로 발행 상태로 "
            f"만들 수 없습니다(C5). '초안' 같은 검토 대기 값을 쓰십시오."
        )

    if problems:
        raise ConfigError("설정이 실제 DB 스키마와 맞지 않습니다:\n" + "\n".join(problems))
    return props_cfg


def build_properties(cfg: dict, props_cfg: dict, schema: dict, *,
                     title: str, issue: int, start_iso: str, end_iso: str) -> dict:
    out: dict = {props_cfg["title"]: {"title": blk.rich(title)}}

    name = props_cfg.get("status")
    if name:
        kind = schema[name]["type"]
        out[name] = {kind: {"name": cfg.get("status_draft_value") or "초안"}}

    name = props_cfg.get("issue_number")
    if name:
        out[name] = {"number": issue}

    name = props_cfg.get("period_range")
    if name:
        out[name] = {"date": {"start": start_iso, "end": end_iso}}
    else:
        if props_cfg.get("period_start"):
            out[props_cfg["period_start"]] = {"date": {"start": start_iso}}
        if props_cfg.get("period_end"):
            out[props_cfg["period_end"]] = {"date": {"start": end_iso}}

    name = props_cfg.get("tags")
    if name:
        tags = cfg.get("auto_tags") or ["자동생성"]
        out[name] = {"multi_select": [{"name": t} for t in tags]}

    return out


def manifest_path(period_id: str) -> Path:
    return PUBLISHED_DIR / f"period_{period_id}.manifest.json"


def already_published(period_id: str, title: str, db_id: str, mode: str) -> str | None:
    """이미 만든 페이지가 있으면 그 id를 돌려준다. 없으면 None."""
    if mode in ("manifest", "both"):
        p = manifest_path(period_id)
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
            page_id = (data.get("report") or {}).get("page_id")
            if page_id:
                return page_id

    if mode in ("title", "both"):
        try:
            res = nc.request("POST", f"/databases/{db_id}/query", {"page_size": 100})
        except nc.NotionError:
            return None
        for row in res.get("results", []):
            tprop = next((v for v in row.get("properties", {}).values() if v.get("type") == "title"), {})
            existing = "".join(x.get("plain_text", "") for x in tprop.get("title", []))
            if existing.strip() == title.strip():
                return row.get("id")
    return None


def publish(final_path: Path, draft_path: Path | None, *, dry_run: bool = False,
            config_path: Path = CONFIG_PATH) -> dict:
    import render_report as rr  # 기간·호수 계산을 렌더러와 공유한다

    data = json.loads(final_path.read_text(encoding="utf-8"))
    report = data.get("report") or {}
    if not report:
        raise RuntimeError("final.json에 report가 없습니다. A5에서 리포트 생성이 실패했는지 확인하십시오.")

    since = until = None
    source_period_id = None
    if draft_path and draft_path.exists():
        d = json.loads(draft_path.read_text(encoding="utf-8"))
        since, until = d.get("since"), d.get("until")
        source_period_id = d.get("source_period_id")

    period_id = data["period_id"]
    start_iso, end_iso = rr.data_range(source_period_id or period_id, since, until)
    week_label = f"{rr._display(start_iso)} ~ {rr._display(end_iso)}"
    issue = rr.issue_number(period_id)

    cfg = load_yaml(config_path)
    db_id = nc.normalize_id(str(cfg.get("database") or ""))

    title = _title_text(report, cfg, issue, week_label)
    body = blk.build(data, week_label, issue)

    if dry_run:
        print(f"[dry-run] {issue}호 · {week_label}")
        print(f"  DB      {db_id}")
        print(f"  제목    {title}")
        print(f"  블록    {len(body)}개 ({len(list(blk.batched(body)))}회 요청)")
        print(f"  설정    {config_path}")
        print("\n  ※ 노션에 아무것도 쓰지 않았습니다. 실제 발행하려면 --dry-run을 빼십시오.")
        return {"dry_run": True, "title": title, "blocks": len(body), "database": db_id}

    schema = nc.request("GET", f"/databases/{db_id}").get("properties", {})
    props_cfg = validate_config(cfg, schema)

    existing = already_published(period_id, title, db_id, cfg.get("duplicate_check") or "both")
    if existing:
        log_event("A7", "skip", detail=f"{issue}호는 이미 발행돼 있습니다", period_id=period_id,
                  extra={"page_id": existing})
        print(f"이미 발행된 호입니다 (page {existing}). 아무것도 만들지 않았습니다.")
        return {"skipped": True, "page_id": existing}

    payload = {
        "parent": {"database_id": db_id},
        "properties": build_properties(cfg, props_cfg, schema, title=title, issue=issue,
                                       start_iso=start_iso, end_iso=end_iso),
        "children": next(blk.batched(body)),
    }
    page = nc.request("POST", "/pages", payload)
    page_id = page["id"]

    rest = list(blk.batched(body))[1:]
    for i, chunk in enumerate(rest, 2):
        nc.request("PATCH", f"/blocks/{page_id}/children", {"children": chunk})

    manifest = {
        "period_id": period_id,
        "issue": issue,
        "week": {"start": start_iso, "end": end_iso},
        "report": {"page_id": page_id, "url": page.get("url"), "title": title,
                   "status": cfg.get("status_draft_value")},
        "blocks": len(body),
        "database_id": db_id,
    }
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path(period_id).write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                        encoding="utf-8")

    log_event("A7", "success", detail=f"{issue}호 발행(초안) — 블록 {len(body)}개",
              period_id=period_id, extra={"page_id": page_id})
    print(f"발행 완료(초안): {page.get('url')}")
    print(f"매니페스트: {manifest_path(period_id)}")
    print("\n※ 상태는 '초안'입니다. 검토 후 사람이 직접 발행 상태로 바꿔야 합니다(C5).")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="A7: 주간 리포트를 노션에 뉴스레터 페이지로 발행")
    ap.add_argument("--final", required=True)
    ap.add_argument("--draft", help="기간·호수 계산에 쓴다(권장)")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--dry-run", action="store_true", help="노션에 쓰지 않고 보낼 내용만 확인")
    args = ap.parse_args()

    try:
        publish(Path(args.final), Path(args.draft) if args.draft else None,
                dry_run=args.dry_run, config_path=Path(args.config))
    except ConfigError as e:
        print(f"\n[설정 오류] {e}\n")
        print("probe_db.py로 실제 속성 이름을 확인하고 docs/notion_publish.yaml을 고치십시오.")
        log_event("A7", "failure", detail=f"설정 검증 실패: {str(e)[:200]}")
        raise SystemExit(1)
    except nc.NotionError as e:
        hint = {
            401: "토큰이 유효하지 않습니다. Keychain의 talkinsight-notion을 확인하십시오.",
            404: "통합이 이 DB에 연결돼 있지 않습니다. 노션에서 ··· → 연결 → 통합 추가.",
        }.get(e.status, "")
        print(f"\n[노션 {e.status}] {hint or e.body[:300]}")
        log_event("A7", "failure", detail=f"노션 {e.status}")
        if e.status not in (401, 404):
            escalate("A7", f"노션 발행 실패 {e.status}: {e.body[:300]}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
