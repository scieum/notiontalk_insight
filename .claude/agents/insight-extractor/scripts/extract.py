"""A4 인사이트 추출 오케스트레이터.

  슬라이스 선택 → 배치별 분류·추출(LLM) → 항목 취합 → 리포트 생성(LLM) → draft.json

설계서 §2.4 A4의 실패 처리를 그대로 구현한다:
- 스키마 오류 → 오류 메시지를 주입해 최대 2회 재시도
- 지속 실패 → 배치를 이분해 좁힌 뒤 **문제 스레드만** 스킵 + 로그
- 리포트 생성 실패 → 에스컬레이션

이 스크립트는 생성만 한다. 채점은 A5(insight-evaluator)가 별도 호출로 한다(C9).

실행:
    .venv/bin/python .../extract.py --period-id 2025-01-26_A --since 2026-08-19
    # 키 없이 프롬프트만 확인:
    python3 .../extract.py --period-id ... --since ... --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parents[4]))
sys.path.insert(0, str(_HERE.parents[4] / ".claude/skills/gemini-client/scripts"))

import extract_prompt as prompt_mod
import select_slice
import validate_draft

from lib.common.escalate import escalate
from lib.common.log import log_event
from lib.common.paths import INSIGHTS_DIR

MAX_SCHEMA_RETRIES = 2  # 설계서 A4: 스키마 오류 시 최대 2회 재시도

# 배치 결과 캐시. 호출 하나가 수십 초~수 분 걸리므로, 도중에 끊긴 실행을 다시
# 돌릴 때 이미 성공한 배치까지 재과금하면 안 된다. 키는 프롬프트 내용 해시라
# 스레드 구성·프롬프트 문구가 바뀌면 자동으로 무효가 된다.
CACHE_DIR_NAME = ".cache"


def _cache_path(sid: str, prompt_text: str) -> Path:
    # 모델명을 키에 넣는다. 모델이 바뀌면 결과 성격도 바뀌므로, 한 draft 안에
    # 서로 다른 모델의 산출물이 섞이면 안 된다.
    try:
        from llm import GENERATION_MODEL as _model
    except Exception:
        _model = "unknown"
    digest = hashlib.sha256(f"{_model}\n{prompt_text}".encode("utf-8")).hexdigest()[:16]
    return INSIGHTS_DIR / CACHE_DIR_NAME / sid / f"{digest}.json"


def _cache_get(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cache_put(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def parse_json_response(text: str):
    """LLM 응답에서 JSON을 꺼낸다.

    'JSON만 출력하라'고 지시해도 코드 펜스나 인사말이 섞여 나오는 일이 잦다.
    펜스를 벗기고, 그래도 안 되면 첫 '{'부터 마지막 '}'까지를 잘라 본다.
    """
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        return json.loads(cleaned[start : end + 1])
    raise json.JSONDecodeError("JSON을 찾을 수 없습니다", cleaned, 0)


def _call_with_schema_retry(build_prompt, validate, generate_fn, label: str):
    """생성 → 검증 → (실패 시 오류 주입) 재시도. (결과, 시도수, 마지막오류) 반환."""
    schema_error = None
    for attempt in range(MAX_SCHEMA_RETRIES + 1):
        text = generate_fn(build_prompt(schema_error))
        try:
            obj = parse_json_response(text)
        except json.JSONDecodeError as e:
            schema_error = f"응답이 올바른 JSON이 아니었습니다: {e}"
            continue
        errors = validate(obj)
        if not errors:
            return obj, attempt + 1, None
        schema_error = "\n".join(f"- {e}" for e in errors)
    return None, MAX_SCHEMA_RETRIES + 1, schema_error


def _aggregate(thread_results: list[dict]) -> dict:
    """배치 결과들을 draft.json의 faq/tips/actions 배열로 펼치고 id를 부여한다."""
    faq, tips, actions, unresolved = [], [], [], []
    thread_types: dict[str, list[str]] = {}

    for th in thread_results:
        tid = th["thread_id"]
        thread_types[tid] = th.get("types", [])
        for item in th.get("faq") or []:
            faq.append(
                {
                    "id": f"F{len(faq) + 1}",
                    "question": item.get("question", ""),
                    "answer": item.get("answer", ""),
                    "tags": item.get("tags") or [],
                    "source_message_ids": item["source_message_ids"],
                    "practical_tip": item.get("practical_tip"),
                    "merge_into": None,
                    "thread_id": tid,
                }
            )
        for item in th.get("tips") or []:
            tips.append(
                {
                    "id": f"P{len(tips) + 1}",
                    "title": item.get("title", ""),
                    "body": item.get("body", ""),
                    "feature_tags": item.get("feature_tags") or [],
                    "source_message_ids": item["source_message_ids"],
                    "practical_tip": item.get("practical_tip"),
                    "thread_id": tid,
                }
            )
        for item in th.get("actions") or []:
            actions.append(
                {
                    "id": f"A{len(actions) + 1}",
                    "type": item.get("type", "action"),
                    "text": item.get("text", ""),
                    "owner_nickname": item.get("owner_nickname"),
                    "due": item.get("due"),
                    "source_message_ids": item["source_message_ids"],
                    "thread_id": tid,
                }
            )
        for item in th.get("unresolved") or []:
            unresolved.append(
                {
                    "question": item.get("question", ""),
                    "source_message_ids": item["source_message_ids"],
                    "thread_id": tid,
                }
            )

    return {
        "faq": faq,
        "tips": tips,
        "actions": actions,
        "unresolved": unresolved,
        "thread_types": thread_types,
    }


def run(
    period_id: str,
    since: str | None,
    until: str | None,
    generate_fn,
    rejections: list[str] | None = None,
    out_path: Path | None = None,
    use_cache: bool = True,
) -> dict:
    sl = select_slice.select(period_id, since, until)
    threads = sl["threads"]
    sid = select_slice.slice_id(period_id, since, until)

    if not threads:
        log_event("A4", "skip", detail="처리할 스레드가 0건입니다", period_id=sid)
        return {"skipped": True, "slice_id": sid}

    batches = select_slice.make_batches(threads)
    feature_tags = validate_draft.load_feature_tags()

    thread_results: list[dict] = []
    skipped_batches: list[dict] = []

    def process(batch: list[dict], label: str) -> None:
        """배치 하나를 처리한다. 스키마 재시도가 다 실패하면 반으로 쪼개 다시 시도한다.

        설계서 A4의 실패 처리는 "해당 **스레드**만 스킵"이다. 배치째 버리면 스레드
        50개가 한 번에 날아가므로, 이분해서 문제 스레드만 남을 때까지 좁힌다.
        LLM 호출이 늘지만 실패한 경로에서만 늘어난다.
        """
        first_prompt = prompt_mod.build_thread_batch_prompt(batch, rejections, None)
        cache_file = _cache_path(sid, first_prompt)
        if use_cache:
            cached = _cache_get(cache_file)
            if cached is not None and not validate_draft.validate_thread_batch(cached, batch, feature_tags):
                thread_results.extend(cached["threads"])
                print(f"  [{label}] 캐시 사용 (스레드 {len(cached['threads'])}개)")
                return

        obj, attempts, err = _call_with_schema_retry(
            build_prompt=lambda se, b=batch: prompt_mod.build_thread_batch_prompt(b, rejections, se),
            validate=lambda o, b=batch: validate_draft.validate_thread_batch(o, b, feature_tags),
            generate_fn=generate_fn,
            label=label,
        )
        if obj is not None and use_cache:
            _cache_put(cache_file, obj)
        if obj is not None:
            thread_results.extend(obj["threads"])
            print(f"  [{label}] OK (시도 {attempts}회, 스레드 {len(obj['threads'])}개)")
            return

        if len(batch) > 1:
            mid = len(batch) // 2
            print(f"  [{label}] 실패 — 스레드 {len(batch)}개를 {mid}/{len(batch) - mid}로 쪼개 재시도")
            process(batch[:mid], f"{label}a")
            process(batch[mid:], f"{label}b")
            return

        tid = batch[0]["thread_id"]
        skipped_batches.append({"batch": label, "thread_ids": [tid], "last_error": err})
        log_event(
            "A4", "failure",
            detail=f"스레드 {tid} 스키마 검증 {attempts}회 실패 — 스킵",
            period_id=sid,
            extra={"thread_ids": [tid], "last_error": err},
        )
        print(f"  [{label}] 실패 — 스레드 {tid} 스킵")

    for i, batch in enumerate(batches, 1):
        process(batch, f"배치 {i}/{len(batches)}")

    agg = _aggregate(thread_results)

    stats = {
        "메시지 수": sl["stats"]["message_count"],
        "함께한 선생님": sl["stats"]["participant_count"],
        "스레드 수": sl["stats"]["threads_selected"],
        "질문 수": len(agg["faq"]) + len(agg["unresolved"]),
        "그중 해결된 수": len(agg["faq"]),
    }

    allowed_ids = {m["id"] for t in threads for m in t["messages"]}
    allowed_tids = {t["thread_id"] for t in threads}
    period_label = f"{since or '처음'} ~ {until or '오늘'}"

    report_items = {
        "faq": [{k: v for k, v in f.items() if k != "merge_into"} for f in agg["faq"]],
        "tips": agg["tips"],
        "actions": agg["actions"],
        "unresolved": agg["unresolved"],
        "thread_types": agg["thread_types"],
    }

    report, attempts, err = _call_with_schema_retry(
        build_prompt=lambda se: prompt_mod.build_report_prompt(
            report_items, stats, period_label, rejections, se
        ),
        validate=lambda o: validate_draft.validate_report(o, allowed_ids, allowed_tids),
        generate_fn=generate_fn,
        label="report",
    )

    if report is None:
        # 설계서 A4: 리포트 생성 실패는 에스컬레이션 대상이다.
        esc = escalate(
            "A4",
            f"리포트 생성이 스키마 검증 {attempts}회 실패로 완료되지 못했습니다. 마지막 오류:\n{err}",
            period_id=sid,
        )
        log_event("A4", "failure", detail=f"리포트 생성 실패 ({attempts}회)", period_id=sid,
                  extra={"escalation": str(esc)})
        report = None

    draft = {
        "period_id": sid,
        "source_period_id": period_id,
        "since": since,
        "until": until,
        "report": (
            {**report, "sections": {**report["sections"], "stats": stats}} if report else None
        ),
        "faq": agg["faq"],
        "tips": agg["tips"],
        "actions": agg["actions"],
        "unresolved": agg["unresolved"],
        "generation": {
            "batches": len(batches),
            "threads_skipped": sum(len(b["thread_ids"]) for b in skipped_batches),
            "skipped_detail": skipped_batches,
            "threads_selected": sl["stats"]["threads_selected"],
            "threads_extracted": len(thread_results),
            "slice_stats": sl["stats"],
        },
    }

    out = out_path or (INSIGHTS_DIR / f"period_{sid}.draft.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")

    # 스레드 몇 개를 스킵해도 리포트가 나왔으면 이 구간은 쓸 수 있는 산출물이다.
    # 스킵 자체는 이미 스레드별 failure 로그로 남아 있으므로, 여기서 또 failure로
    # 적으면 운영자가 로그를 훑을 때 "구간이 통째로 실패했다"로 오해한다.
    status = "success" if report else "failure"
    log_event(
        "A4", status,
        detail=(
            f"FAQ {len(agg['faq'])}건, 팁 {len(agg['tips'])}건, 액션 {len(agg['actions'])}건, "
            f"미해결 {len(agg['unresolved'])}건 / 스레드 {sl['stats']['threads_selected']}개 중 "
            f"{sum(len(b['thread_ids']) for b in skipped_batches)}개 스킵"
        ),
        period_id=sid,
        extra={"output": str(out)},
    )
    return draft


def _stub_generate(text: str) -> str:
    """--dry-run용. 프롬프트를 파일로 떨구고 빈 응답을 돌려준다(LLM 미호출)."""
    _stub_generate.prompts.append(text)  # type: ignore[attr-defined]
    return "{}"


_stub_generate.prompts = []  # type: ignore[attr-defined]


def main() -> None:
    ap = argparse.ArgumentParser(description="A4 인사이트 추출")
    ap.add_argument("--period-id", required=True)
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--out")
    ap.add_argument("--rejection", action="append", default=[],
                    help="직전 리포트 반려 사유. 여러 번 지정 가능(최근 3건 권장, CLAUDE.md §6)")
    ap.add_argument("--no-cache", action="store_true",
                    help="배치 결과 캐시를 쓰지 않고 전부 다시 호출한다")
    ap.add_argument("--dry-run", action="store_true",
                    help="LLM을 호출하지 않고 첫 배치 프롬프트를 화면에 출력한다")
    ap.add_argument("--dump-prompt", help="--dry-run과 함께: 프롬프트를 이 경로에 저장")
    args = ap.parse_args()

    if args.dry_run:
        sl = select_slice.select(args.period_id, args.since, args.until)
        batches = select_slice.make_batches(sl["threads"])
        if not batches:
            print("처리할 스레드가 없습니다.")
            return
        p = prompt_mod.build_thread_batch_prompt(batches[0], args.rejection or None)
        print(f"[dry-run] 배치 {len(batches)}개 중 1번 프롬프트 — {len(p)}자, 스레드 {len(batches[0])}개\n")
        if args.dump_prompt:
            Path(args.dump_prompt).write_text(p, encoding="utf-8")
            print(f"-> {args.dump_prompt}")
        else:
            print(p)
        return

    from llm import generate  # 키가 필요한 경로라 dry-run에서는 import조차 하지 않는다

    draft = run(
        args.period_id, args.since, args.until,
        generate_fn=generate,
        rejections=args.rejection or None,
        out_path=Path(args.out) if args.out else None,
        use_cache=not args.no_cache,
    )
    if draft.get("skipped"):
        print("처리할 스레드가 0건이라 건너뛰었습니다.")
        return
    print(
        f"\nFAQ {len(draft['faq'])} · 팁 {len(draft['tips'])} · 액션 {len(draft['actions'])} "
        f"· 미해결 {len(draft['unresolved'])}"
    )
    if draft["report"]:
        print(f"리포트: {draft['report']['hook_title']}")
    else:
        print("리포트: 생성 실패(에스컬레이션 기록됨)")


if __name__ == "__main__":
    main()
