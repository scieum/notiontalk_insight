"""A5 판정을 draft.json에 적용해 '발행 후보'만 남긴 JSON을 만든다 (A5 → A6 사이).

pii-guard(A6)는 "A5를 통과한 항목만 담긴" 입력을 가정한다. 어떤 항목이 통과했는지
고르는 것은 평가자·오케스트레이터의 책임이라 그 일을 여기서 한다. LLM을 부르지
않는다 — 순수 규칙이다.

**리포트 본문도 같이 걸러야 한다.** 리포트는 A4에서 *모든* 항목을 보고 쓰였으므로,
폐기·에스컬레이션된 항목을 그대로 언급하고 있을 수 있다. 항목만 빼고 리포트를
그대로 두면 "본문에는 있는데 카드가 없는" 상태로 발행된다 — 근거 없는 서술이다.
그래서 제거된 항목의 근거 메시지만 가리키는 리포트 항목도 함께 뺀다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from lib.common.log import log_event
from lib.common.paths import INSIGHTS_DIR

PUBLISHABLE = {"통과", "병합"}


def _filter_report_section(entries, removed_ids: set[int]) -> tuple[list, list]:
    """근거가 전부 제거된 항목만 가리키는 리포트 항목을 걸러낸다."""
    kept, dropped = [], []
    for e in entries or []:
        ids = set(e.get("source_message_ids") or [])
        if ids and ids <= removed_ids:
            dropped.append(e)
        else:
            kept.append(e)
    return kept, dropped


def run(draft_path: Path, eval_path: Path, out_path: Path | None = None) -> dict:
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    ev = json.loads(eval_path.read_text(encoding="utf-8"))
    sid = draft["period_id"]

    decision_of = {i["id"]: i["decision"] for i in ev["items"]}
    merge_of = {i["id"]: i.get("merge_into") for i in ev["items"]}

    # 재생성본은 draft에 없고 eval.json에만 있다 — 원본을 대체한 항목이므로 함께 실어야 한다.
    draft_ids = {i["id"] for k in ("faq", "tips", "actions") for i in draft.get(k) or []}
    regenerated_ids = [i for i in decision_of if i not in draft_ids and decision_of[i] in PUBLISHABLE]

    kept = {"faq": [], "tips": [], "actions": []}
    removed_ids: set[int] = set()
    removed_count = {"폐기": 0, "에스컬레이션": 0, "미채점": 0}

    for key in ("faq", "tips", "actions"):
        for item in draft.get(key) or []:
            d = decision_of.get(item["id"], "미채점")
            if d in PUBLISHABLE:
                if d == "병합":
                    item = {**item, "merge_into": merge_of.get(item["id"])}
                kept[key].append(item)
            else:
                removed_count[d] = removed_count.get(d, 0) + 1
                removed_ids.update(item.get("source_message_ids") or [])

    # 재생성본(eval.json의 regenerated_items)은 원본을 대체한 항목이므로 함께 싣는다.
    # 구버전 eval.json에는 이 키가 없어 내용을 복구할 수 없다 — 그 경우만 경고로 남긴다.
    regen_items = ev.get("regenerated_items") or {}
    for key in ("faq", "tips", "actions"):
        for item in regen_items.get(key) or []:
            if decision_of.get(item["id"]) in PUBLISHABLE:
                kept[key].append(item)
    unrecoverable_ids = [
        i for i in regenerated_ids
        if not any(it["id"] == i for key in regen_items for it in regen_items.get(key) or [])
    ]

    # 살아남은 항목이 같은 근거를 쓰고 있으면 그 근거는 '제거됨'이 아니다.
    surviving_ids = {
        mid for key in kept for item in kept[key] for mid in (item.get("source_message_ids") or [])
    }
    removed_ids -= surviving_ids

    report = draft.get("report")
    report_dropped = {}
    if report:
        sections = dict(report["sections"])
        for name in ("resolved", "unresolved", "decisions"):
            k, dropped = _filter_report_section(sections.get(name), removed_ids)
            sections[name] = k
            if dropped:
                report_dropped[name] = len(dropped)
        report = {**report, "sections": sections}

    out = {
        "period_id": sid,
        "source_period_id": draft.get("source_period_id"),
        "since": draft.get("since"),
        "until": draft.get("until"),
        "report": report,
        "faq": kept["faq"],
        "tips": kept["tips"],
        "actions": kept["actions"],
        "unresolved": draft.get("unresolved") or [],
        "applied": {
            "removed": removed_count,
            "regenerated_included": len(regenerated_ids) - len(unrecoverable_ids),
            "regenerated_not_in_draft": unrecoverable_ids,
            "report_entries_dropped": report_dropped,
        },
    }

    path = out_path or (INSIGHTS_DIR / f"period_{sid}.passed.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    total_kept = sum(len(v) for v in kept.values())
    log_event(
        "A5", "success",
        detail=(
            f"판정 적용: 발행 후보 {total_kept}건 "
            f"(폐기 {removed_count.get('폐기',0)} · 에스컬레이션 {removed_count.get('에스컬레이션',0)} "
            f"· 미채점 {removed_count.get('미채점',0)} 제외), 리포트 항목 제거 {sum(report_dropped.values()) or 0}건"
        ),
        period_id=sid, extra={"output": str(path)},
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="A5 판정을 draft에 적용해 발행 후보만 남긴다 (LLM 미호출)")
    ap.add_argument("--draft", required=True)
    ap.add_argument("--eval", required=True, dest="eval_path")
    ap.add_argument("--out")
    args = ap.parse_args()

    out = run(Path(args.draft), Path(args.eval_path), Path(args.out) if args.out else None)
    a = out["applied"]
    print(f"발행 후보  FAQ {len(out['faq'])} · 팁 {len(out['tips'])} · 액션 {len(out['actions'])}")
    print(f"제외      {a['removed']}")
    if a["report_entries_dropped"]:
        print(f"리포트에서 뺀 항목  {a['report_entries_dropped']}")
    if a["regenerated_included"]:
        print(f"재생성본 {a['regenerated_included']}건을 원본 대신 실었습니다")
    if a["regenerated_not_in_draft"]:
        print(f"주의: 재생성본 {len(a['regenerated_not_in_draft'])}건은 eval.json에 내용이 없어(구버전) 실리지 않았습니다")


if __name__ == "__main__":
    main()
