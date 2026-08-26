"""A5 검증 오케스트레이터: draft.json을 원문과 대조해 채점하고 eval.json을 낸다.

설계서 §2.4 A5를 그대로 구현한다. 4축 중

- 충실성 / 근거 일치 / 공개 적절성 → LLM 평가 (스레드 단위로 묶어 1회 호출)
- 중복 → 임베딩 코사인 유사도 (스크립트 계산, LLM에게 묻지 않는다)

실패 처리:
- 충실성·근거 실패 → 그 항목이 나온 스레드만 **재생성 1회**(A4 프롬프트, 별도 호출)
  후 재평가. 그래도 실패하면 폐기 + 로그.
- 공개 적절성 실패 → 재생성하지 않는다. **에스컬레이션**(운영자 판단, 자동 발행 금지).
- 기존 항목과 유사도 >= 0.9 → 신규 발행 대신 기존 항목에 병합(`merge_into`).

C9 준수: 생성(A4)과 평가(A5)는 프롬프트 파일도 호출도 분리돼 있다. 재생성조차
insight-extractor의 프롬프트로 별도 호출한다 — 평가자가 자기 지적을 반영해 직접
고쳐 쓰면 그건 자기검증이다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[4]
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / ".claude/skills/gemini-client/scripts"))
sys.path.insert(0, str(_ROOT / ".claude/skills/kb-search/scripts"))
sys.path.insert(0, str(_ROOT / ".claude/agents/insight-extractor/scripts"))

import eval_prompt  # 파일명이 prompt.py면 insight-extractor의 동명 모듈과 충돌한다

from lib.common.consent import get_consent_mode
from lib.common.escalate import escalate
from lib.common.kb_schema import decode_embedding, ensure_kb_schema
from lib.common.log import log_event
from lib.common.paths import INSIGHTS_DIR, KB_DB_PATH

MAX_SCHEMA_RETRIES = 2
DUPLICATE_THRESHOLD = 0.9  # 설계서 A5: 유사도 >= 0.9면 신규 발행 대신 병합

KIND_OF = {"faq": "FAQ", "tips": "팁", "actions": "결정·액션"}


def _item_text(item: dict, kind: str) -> str:
    """중복 판정용 임베딩 대상 텍스트. 발행되는 내용만 넣는다."""
    if kind == "faq":
        return f"{item.get('question','')}\n{item.get('answer','')}"
    if kind == "tips":
        return f"{item.get('title','')}\n{item.get('body','')}"
    return item.get("text", "")


def load_kb_chat_chunks() -> list[dict]:
    """중복 비교 대상: 이미 발행된 chat 원천 청크. KB가 없으면 빈 목록."""
    if not KB_DB_PATH.exists():
        return []
    conn = sqlite3.connect(f"file:{KB_DB_PATH}?mode=ro", uri=True)
    try:
        ensure_kb_schema(conn)
        rows = conn.execute(
            "SELECT id, text, embedding FROM chunks WHERE source = 'chat' AND superseded = 0"
        ).fetchall()
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()
    return [{"id": r[0], "text": r[1], "embedding": decode_embedding(r[2])} for r in rows]


def check_duplicates(items: list[tuple[dict, str]], embed_fn, kb_chunks: list[dict]) -> dict:
    """항목별 최고 유사도와 병합 대상을 계산한다.

    KB가 비어 있으면(kb-builder 미구현·첫 사이클) 중복 축은 '미실시'다.
    통과로 위장하지 않는다 — 검사하지 않은 것과 통과한 것은 다르다.
    """
    if not kb_chunks:
        return {}

    from search import cosine_similarity

    out = {}
    for item, kind in items:
        vec = embed_fn(_item_text(item, kind))
        best_id, best_sim = None, 0.0
        for chunk in kb_chunks:
            if len(chunk["embedding"]) != len(vec):
                continue  # 임베딩 모델이 바뀐 흔적. 비교 자체가 무의미하므로 건너뛴다.
            sim = cosine_similarity(vec, chunk["embedding"])
            if sim > best_sim:
                best_id, best_sim = chunk["id"], sim
        out[item["id"]] = {"max_similarity": round(best_sim, 4), "nearest_chunk_id": best_id}
    return out


def _validate_eval(obj, expected_ids: set[str]) -> list[str]:
    errors = []
    if not isinstance(obj, dict) or not isinstance(obj.get("items"), list):
        return ['최상위가 {"items": [...]} 형태의 객체가 아닙니다.']
    seen = set()
    for i, it in enumerate(obj["items"]):
        if not isinstance(it, dict):
            errors.append(f"items[{i}]가 객체가 아닙니다.")
            continue
        iid = it.get("id")
        if iid not in expected_ids:
            errors.append(f"items[{i}].id '{iid}'는 채점 대상에 없는 항목입니다.")
            continue
        seen.add(iid)
        for axis in ("faithful", "evidence_supported", "publish_safe"):
            if not isinstance(it.get(axis), bool):
                errors.append(f"{iid}.{axis}는 true/false여야 합니다.")
        reasons = it.get("reasons")
        if not isinstance(reasons, dict) or not all(
            reasons.get(k) for k in ("faithful", "evidence", "publish")
        ):
            errors.append(f"{iid}.reasons에 faithful/evidence/publish 세 사유가 모두 필요합니다.")
    missing = expected_ids - seen
    if missing:
        errors.append(f"채점이 빠진 항목이 있습니다: {sorted(missing)}")
    return errors


MAX_THREADS_PER_EVAL = 5   # 채점 호출 하나에 담는 스레드 수
MAX_ITEMS_PER_EVAL = 12    # 응답 길이는 항목 수에 비례한다


def make_eval_batches(groups: list[tuple], max_threads: int = MAX_THREADS_PER_EVAL,
                      max_items: int = MAX_ITEMS_PER_EVAL) -> list[list[tuple]]:
    """(thread_id, items, messages) 그룹을 채점 호출 단위로 묶는다.

    스레드 하나는 쪼개지 않는다 — 쪼개면 그 스레드 원문 일부만 보고 채점하게 된다.
    """
    batches, current, n_items = [], [], 0
    for g in groups:
        k = len(g[1])
        if current and (len(current) >= max_threads or n_items + k > max_items):
            batches.append(current)
            current, n_items = [], 0
        current.append(g)
        n_items += k
    if current:
        batches.append(current)
    return batches


def _call_eval(groups, consent_mode, generate_fn):
    from extract import parse_json_response

    expected = {it["id"] for _tid, items, _msgs in groups for it, _k in items}
    base = eval_prompt.build_eval_prompt(groups, consent_mode)
    schema_error = None
    for attempt in range(MAX_SCHEMA_RETRIES + 1):
        p = base if schema_error is None else (
            base + f"\n\n## 직전 응답이 거부된 이유\n\n{schema_error}\n\n"
            "이번에는 위 문제를 고쳐서 JSON만 다시 출력하십시오.\n"
        )
        try:
            obj = parse_json_response(generate_fn(p))
        except json.JSONDecodeError as e:
            schema_error = f"응답이 올바른 JSON이 아니었습니다: {e}"
            continue
        except Exception as e:
            # API 오류는 재시도해도 프롬프트를 고칠 문제가 아니다. 이 배치는 포기하고
            # 다음 배치로 넘어간다 — 한 배치 때문에 구간 전체를 잃지 않는다.
            return None, attempt + 1, f"채점 호출 실패: {type(e).__name__}: {str(e)[:200]}"
        errors = _validate_eval(obj, expected)
        if not errors:
            return {it["id"]: it for it in obj["items"]}, attempt + 1, None
        schema_error = "\n".join(f"- {e}" for e in errors)
    return None, MAX_SCHEMA_RETRIES + 1, schema_error


def _regenerate_thread(thread: dict, failed_reasons: list[str], generate_fn):
    """충실성·근거로 걸린 스레드를 A4 프롬프트로 **다시 생성**한다(설계서 A5 실패 처리).

    핵심은 이 호출이 insight-extractor의 프롬프트를 쓴다는 것이다. 평가자가
    자기 지적을 반영해 직접 고쳐 쓰면 그것은 생성과 평가의 분리가 아니다(C9).
    평가 사유는 A4의 '반려 사유' 자리로 주입한다 — 그 자리가 원래 그런 용도다.

    반환: {"faq": [...], "tips": [...], "actions": [...]} 또는 실패 시 None.
    """
    import extract_prompt
    import validate_draft
    from extract import parse_json_response

    feature_tags = validate_draft.load_feature_tags()
    rejections = ["직전 생성에서 아래 지적을 받았습니다. 같은 문제를 반복하지 마십시오."] + failed_reasons

    prompt_text = extract_prompt.build_thread_batch_prompt([thread], rejections, None)
    try:
        obj = parse_json_response(generate_fn(prompt_text))
    except json.JSONDecodeError:
        return None
    except Exception as e:
        # API 오류(429 할당량 소진, 타임아웃 등)로 재생성을 못 한 경우다.
        # 여기서 예외를 올리면 이미 끝낸 채점 결과까지 통째로 날아간다 —
        # 설계서 A5는 "재실패 시 폐기+로그"이지 중단이 아니다.
        log_event("A5", "failure", detail=f"재생성 호출 실패: {type(e).__name__}")
        return None
    if validate_draft.validate_thread_batch(obj, [thread], feature_tags):
        return None
    th = obj["threads"][0]
    return {k: th.get(k) or [] for k in ("faq", "tips", "actions")}


def run(
    draft_path: Path,
    generate_fn,
    embed_fn=None,
    out_path: Path | None = None,
    regenerate_fn=None,
) -> dict:
    """generate_fn: 채점 호출(평가 모델). regenerate_fn: 재생성 호출(생성 모델).

    재생성은 이름 그대로 **생성**이므로 평가 모델로 부르면 안 된다 — 평가 모델이
    자기가 지적한 것을 자기가 다시 쓰면 C9가 무너진다. 생략하면 generate_fn을
    쓰지만, 그건 테스트용 편의일 뿐 운영 경로가 아니다.
    """
    regenerate_fn = regenerate_fn or generate_fn
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    sid = draft["period_id"]

    import select_slice

    sl = select_slice.select(draft["source_period_id"], draft.get("since"), draft.get("until"))
    msgs_by_thread = {t["thread_id"]: t["messages"] for t in sl["threads"]}

    # 항목을 스레드별로 묶는다 — 평가는 원문 대조라 같은 스레드 것끼리 한 번에 본다.
    by_thread: dict[str, list[tuple[dict, str]]] = {}
    all_items: list[tuple[dict, str]] = []
    for key in ("faq", "tips", "actions"):
        for item in draft.get(key) or []:
            by_thread.setdefault(item["thread_id"], []).append((item, key))
            all_items.append((item, key))

    if not all_items:
        log_event("A5", "skip", detail="채점할 항목이 0건입니다", period_id=sid)
        return {"period_id": sid, "items": [], "skipped": True}

    consent_mode = get_consent_mode()
    verdicts: dict[str, dict] = {}
    unscored: list[str] = []

    groups = []
    for tid, group in by_thread.items():
        messages = msgs_by_thread.get(tid)
        if messages is None:
            # 원문을 못 찾으면 대조가 불가능하다. 통과시키지 않는다.
            for item, _ in group:
                unscored.append(item["id"])
            log_event("A5", "failure", detail=f"스레드 {tid} 원문을 찾지 못해 채점 불가", period_id=sid)
            continue
        groups.append((tid, [(it, KIND_OF[k]) for it, k in group], messages))

    eval_batches = make_eval_batches(groups)
    for i, batch in enumerate(eval_batches, 1):
        n_items = sum(len(g[1]) for g in batch)
        scored, attempts, err = _call_eval(batch, consent_mode, generate_fn)
        if scored is None:
            for _tid, items, _msgs in batch:
                for it, _k in items:
                    unscored.append(it["id"])
            log_event(
                "A5", "failure",
                detail=f"채점 배치 {i}/{len(eval_batches)} {attempts}회 스키마 실패 — 항목 {n_items}건 미채점",
                period_id=sid, extra={"last_error": err},
            )
            print(f"  [채점 {i}/{len(eval_batches)}] 실패 — 항목 {n_items}건 미채점")
            continue
        verdicts.update(scored)
        print(f"  [채점 {i}/{len(eval_batches)}] 완료 (스레드 {len(batch)}개, 항목 {n_items}건, 시도 {attempts}회)")

    # --- 재생성 1회 (충실성·근거 실패 항목만) ---
    # 설계서 A5: 공개 부적절은 재생성 대상이 아니다(사람 판단). 충실성·근거만 다시 만든다.
    regen_targets: dict[str, list[str]] = {}
    for item, _kind in all_items:
        v = verdicts.get(item["id"])
        if v is None or not v["publish_safe"]:
            continue
        if not v["faithful"] or not v["evidence_supported"]:
            reasons = []
            if not v["faithful"]:
                reasons.append(f"- 충실성: {v['reasons'].get('faithful','')}")
            if not v["evidence_supported"]:
                reasons.append(f"- 근거 일치: {v['reasons'].get('evidence','')}")
            regen_targets.setdefault(item["thread_id"], []).extend(reasons)

    regenerated: dict[str, list[dict]] = {}
    for tid, reasons in regen_targets.items():
        # 어느 실패 경로로 빠지든 regenerated[tid]를 비워서 남긴다.
        # 그래야 원본이 '폐기'로 처리된다 — 설계서 A5: 재실패 시 폐기+로그.
        thread = next((t for t in sl["threads"] if t["thread_id"] == tid), None)
        if thread is None:
            log_event("A5", "failure", detail=f"스레드 {tid} 원문이 없어 재생성 불가 — 해당 항목 폐기", period_id=sid)
            regenerated[tid] = []
            continue
        fresh = _regenerate_thread(thread, reasons, regenerate_fn)
        if fresh is None:
            log_event("A5", "failure", detail=f"스레드 {tid} 재생성 실패 — 해당 항목 폐기", period_id=sid)
            regenerated[tid] = []
            continue

        # 새로 만든 항목에 id를 붙이고(원본과 구분되게 -r), 다시 채점한다.
        new_items: list[tuple[dict, str]] = []
        for key in ("faq", "tips", "actions"):
            for n, it in enumerate(fresh[key], 1):
                it["id"] = f"{key[0].upper()}{n}-r-{tid}"
                it["thread_id"] = tid
                new_items.append((it, key))
        if not new_items:
            log_event("A5", "skip", detail=f"스레드 {tid} 재생성 결과가 0건 — 원본 항목 폐기", period_id=sid)
            regenerated[tid] = []
            continue

        rescored, _attempts, _err = _call_eval(
            [(tid, [(it, KIND_OF[k]) for it, k in new_items], msgs_by_thread[tid])],
            consent_mode, generate_fn,
        )
        if rescored is None:
            log_event("A5", "failure", detail=f"스레드 {tid} 재채점 실패 — 해당 항목 폐기", period_id=sid)
            regenerated[tid] = []
            continue

        kept = []
        for it, kind in new_items:
            v = rescored.get(it["id"])
            if v and v["faithful"] and v["evidence_supported"] and v["publish_safe"]:
                verdicts[it["id"]] = v
                kept.append((it, kind))
        regenerated[tid] = kept
        print(f"  [{tid}] 재생성 {len(new_items)}건 -> 재채점 통과 {len(kept)}건")

    # 재생성이 일어난 스레드는 원본 항목을 목록에서 빼고 새 항목으로 갈아끼운다.
    # 빠진 원본은 조용히 사라지지 않고 `폐기`로 결과에 남는다 — 폐기와 미생성은 다르다.
    discarded_items: list[tuple[dict, str]] = []
    if regenerated:
        replaced: list[tuple[dict, str]] = []
        for item, kind in all_items:
            if item["thread_id"] in regenerated:
                discarded_items.append((item, kind))
                continue
            replaced.append((item, kind))
        for tid, kept in regenerated.items():
            replaced.extend(kept)
        log_event(
            "A5", "success",
            detail=(
                f"재생성 {len(regen_targets)}개 스레드, 원본 폐기 {len(discarded_items)}건, "
                f"대체 {sum(len(k) for k in regenerated.values())}건"
            ),
            period_id=sid,
        )
        all_items = replaced

    # --- 중복 축 (스크립트 계산) ---
    kb_chunks = load_kb_chat_chunks()
    dup_checked = bool(kb_chunks) and embed_fn is not None
    dup = check_duplicates(all_items, embed_fn, kb_chunks) if dup_checked else {}
    if not dup_checked:
        log_event(
            "A5", "skip",
            detail="중복 축 미실시 — KB에 chat 원천 청크가 없습니다(kb-builder 미구현/첫 사이클)",
            period_id=sid,
        )

    # --- 판정 종합 ---
    results = []
    counts = {"pass": 0, "escalate": 0, "merge": 0, "unscored": 0, "discard": 0}
    escalated = []

    for item, kind in all_items:
        iid = item["id"]
        v = verdicts.get(iid)
        d = dup.get(iid, {})
        sim = d.get("max_similarity")

        if v is None:
            decision = "미채점"
            counts["unscored"] += 1
        elif not v["publish_safe"]:
            # 공개 부적절은 재생성 대상이 아니다 — 사람이 판단한다.
            decision = "에스컬레이션"
            counts["escalate"] += 1
            escalated.append((iid, kind, v["reasons"].get("publish", "")))
        elif not v["faithful"] or not v["evidence_supported"]:
            # 재생성 단계를 거치고도 여기 남았다면 재생성본마저 실패한 것이다.
            decision = "폐기"
            counts["discard"] += 1
        elif sim is not None and sim >= DUPLICATE_THRESHOLD:
            decision = "병합"
            counts["merge"] += 1
        else:
            decision = "통과"
            counts["pass"] += 1

        results.append({
            "id": iid,
            "kind": kind,
            "thread_id": item["thread_id"],
            "decision": decision,
            "faithful": v["faithful"] if v else None,
            "evidence_supported": v["evidence_supported"] if v else None,
            "publish_safe": v["publish_safe"] if v else None,
            "reasons": v["reasons"] if v else None,
            "duplicate_checked": dup_checked,
            "max_similarity": sim,
            "merge_into": d.get("nearest_chunk_id") if (sim is not None and sim >= DUPLICATE_THRESHOLD) else None,
        })

    for item, kind in discarded_items:
        v = verdicts.get(item["id"])
        counts["discard"] += 1
        results.append({
            "id": item["id"],
            "kind": kind,
            "thread_id": item["thread_id"],
            "decision": "폐기",
            "faithful": v["faithful"] if v else None,
            "evidence_supported": v["evidence_supported"] if v else None,
            "publish_safe": v["publish_safe"] if v else None,
            "reasons": v["reasons"] if v else None,
            "duplicate_checked": dup_checked,
            "max_similarity": None,
            "merge_into": None,
            "note": "충실성·근거 실패로 재생성됐다. 이 항목은 발행하지 않는다.",
        })

    if escalated:
        lines = "\n".join(f"  - {i} ({k}): {r}" for i, k, r in escalated)
        esc = escalate(
            "A5",
            f"공개 적절성 판정에서 걸린 항목이 {len(escalated)}건입니다. 자동 발행하지 않습니다. "
            f"운영자가 직접 판단해야 합니다.\n{lines}",
            files=[str(draft_path)],
            period_id=sid,
        )
        print(f"  공개 부적절 {len(escalated)}건 → 에스컬레이션 {esc}")

    out = out_path or (INSIGHTS_DIR / f"period_{sid}.eval.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "period_id": sid,
        "draft_path": str(draft_path),
        "consent_mode": consent_mode,
        "duplicate_checked": dup_checked,
        "duplicate_threshold": DUPLICATE_THRESHOLD,
        "counts": counts,
        "items": results,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    log_event(
        "A5", "success" if not counts["unscored"] else "failure",
        detail=(
            f"통과 {counts['pass']} · 폐기 {counts['discard']} · 병합 {counts['merge']} "
            f"· 에스컬레이션 {counts['escalate']} · 미채점 {counts['unscored']}"
        ),
        period_id=sid, extra={"output": str(out)},
    )
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description="A5 인사이트 검증")
    ap.add_argument("--draft", required=True, help="A4가 만든 draft.json 경로")
    ap.add_argument("--out")
    args = ap.parse_args()

    from embed_client import embed
    from llm import evaluate as llm_evaluate
    from llm import generate as llm_generate

    # 채점은 평가 모델(EVALUATION_MODEL), 재생성은 생성 모델(GENERATION_MODEL).
    payload = run(Path(args.draft), generate_fn=llm_evaluate, embed_fn=embed,
                  out_path=Path(args.out) if args.out else None,
                  regenerate_fn=llm_generate)
    if payload.get("skipped"):
        print("채점할 항목이 없습니다.")
        return
    c = payload["counts"]
    print(f"\n통과 {c['pass']} · 폐기 {c['discard']} · 병합 {c['merge']} "
          f"· 에스컬레이션 {c['escalate']} · 미채점 {c['unscored']}")
    if not payload["duplicate_checked"]:
        print("(중복 축은 KB가 비어 있어 미실시)")


if __name__ == "__main__":
    main()
