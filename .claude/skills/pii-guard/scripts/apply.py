"""A6 오케스트레이터: mask.py + anonymize.py를 인사이트 JSON 전체에 적용하고
재검사 후 period_<id>.final.json을 쓴다.

설계서 §2.4 A6, §3.2. 입력은 A5 검증을 통과한 항목만 담긴 draft/eval 형태의
JSON({period_id, report, faq[], tips[], actions[]})이라고 가정한다 — 어떤 항목이
통과했는지 고르는 것은 insight-evaluator/메인 오케스트레이터의 책임이고, 이
스크립트는 "발행 직전 마지막 방어선"만 담당한다.

처리 단위는 report 전체(1건) / faq[i] / tips[i] / actions[i] 각각이다. 재검사에서
PII나 (anon 모드) 원본 닉네임이 남아 있으면 그 항목만 최종 출력에서 빼고
에스컬레이션한다 — 나머지 항목은 정상 발행된다.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import anonymize
import mask

from lib.common.consent import get_consent_mode
from lib.common.escalate import escalate
from lib.common.log import log_event
from lib.common.paths import DOCS_DIR, INSIGHTS_DIR, MESSAGES_DB_PATH


_ADMIN_SECTION_RE = re.compile(r"^##\s*운영진 닉네임\s*$(.*?)(?=^##\s|\Z)", re.M | re.S)
_TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*$", re.M)


def load_exempt_nicknames(path: Path | None = None) -> set[str]:
    """익명화에서 제외할 닉네임(운영진). room_profile.md의 '운영진 닉네임' 표에서 읽는다.

    운영자·운영진은 이름이 드러나도 되는 위치라 가명으로 바꾸면 오히려 읽기 나쁘다.
    다만 **명단에 올리는 것 자체가 그 사람의 공개 동의를 전제**하므로(G-C), 코드에
    하드코딩하지 않고 운영자가 직접 관리하는 문서에서만 읽는다.
    """
    path = path or (DOCS_DIR / "room_profile.md")
    if not path.exists():
        return set()
    m = _ADMIN_SECTION_RE.search(path.read_text(encoding="utf-8"))
    if not m:
        return set()
    out = set()
    for name, _role in _TABLE_ROW_RE.findall(m.group(1)):
        if name in ("닉네임", "") or set(name) <= set("-: "):
            continue
        out.add(name)
    return out


def get_period_nicknames(
    period_id: str,
    db_path: Path = MESSAGES_DB_PATH,
    since: str | None = None,
    until: str | None = None,
) -> list[str]:
    """구간(또는 그 안의 슬라이스)에 등장한 닉네임을 첫 등장(ts) 순서대로 반환한다.

    since/until을 주면 그 범위로 좁힌다. **이게 중요하다**: 백필된 구간 하나에
    19개월치가 들어 있어 닉네임이 1,025개나 되는데, 그걸 전부 치환 대상으로 삼으면
    한 글자 닉네임이 일반 단어를 갈아엎는다("노션 AI 라이브러리" → "참여자MD AI
    참여자ADS브러리", 2026-08-26 실측). 발행하는 것은 슬라이스이므로 그 안에서
    실제로 말한 사람만 치환하면 된다.
    """
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(db_path)
    sql = "SELECT nickname FROM messages WHERE period_id = ? AND nickname IS NOT NULL"
    params: list = [period_id]
    if since:
        sql += " AND ts >= ?"
        params.append(since)
    if until:
        sql += " AND ts < ?"
        params.append(until)
    rows = conn.execute(sql + " ORDER BY ts", params).fetchall()
    conn.close()
    ordered: list[str] = []
    for (name,) in rows:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _transform_strings(obj, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_transform_strings(x, fn) for x in obj]
    if isinstance(obj, dict):
        return {k: _transform_strings(v, fn) for k, v in obj.items()}
    return obj


def process_item(item, nickname_map: dict[str, str], mode: str):
    def _pipeline(text: str) -> str:
        text = anonymize.remove_system_event_mentions(text)
        text, _counts = mask.mask_text(text)
        if mode == "anon":
            text = anonymize.anonymize_text(text, nickname_map)
        return text

    return _transform_strings(item, _pipeline)


def verify_item(item, nickname_map: dict[str, str], mode: str) -> list[str]:
    dumped = json.dumps(item, ensure_ascii=False)
    issues = []
    remaining = mask.rescan(dumped)
    if remaining:
        issues.append(f"PII 잔존: {remaining}")
    if mode == "anon":
        # 치환과 같은 낱말 경계 기준으로 검사한다. 기준이 다르면 치환은 안 하고
        # 검사만 걸리는 항목이 생긴다.
        leaked = anonymize.find_nicknames(dumped, nickname_map)
        if leaked:
            issues.append(f"원본 닉네임 잔존: {leaked}")
    return issues


def run(input_path: Path, output_path: Path | None = None) -> dict:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    period_id = data["period_id"]
    mode = get_consent_mode()

    # 닉네임은 **원본 구간 id**로 조회한다. A4가 날짜로 잘라낸 슬라이스 id
    # (`2025-01-26_A__20260819-끝`)는 messages.sqlite의 periods에 없으므로, 그대로
    # 조회하면 빈 목록이 돌아온다 — 그러면 치환할 대상이 없어 재검사도 통과해버려
    # **실명이 그대로 발행된다**(2026-08-26에 실제로 발생).
    lookup_period_id = data.get("source_period_id") or period_id
    exempt = load_exempt_nicknames()
    nicknames = [
        n for n in get_period_nicknames(
            lookup_period_id, since=data.get("since"), until=data.get("until")
        )
        if n not in exempt
    ]

    if mode == "anon" and not nicknames:
        # 익명화 모드인데 치환할 닉네임을 하나도 못 찾은 상태는 정상일 수 없다.
        # 조용히 통과시키면 C2(닉네임 익명화)가 무력화된다.
        reason = (
            f"익명화 모드인데 구간 '{lookup_period_id}'의 닉네임을 하나도 찾지 못했습니다. "
            f"치환 없이 발행하면 실명이 그대로 나갑니다. period_id 매핑을 확인하십시오."
        )
        escalate("A6", reason, files=[str(input_path)], period_id=period_id)
        log_event("A6", "failure", period_id=period_id, detail=reason)
        raise SystemExit(f"[apply] 중단 — {reason}")

    nickname_map = anonymize.build_nickname_map(nicknames) if mode == "anon" else {}
    # 별칭 생성 결과가 운영진 이름과 겹칠 수 있으니 한 번 더 걸러낸다.
    nickname_map = {k: v for k, v in nickname_map.items() if k not in exempt}

    result = {"period_id": period_id, "report": None, "faq": [], "tips": [], "actions": []}
    dropped: list[tuple[str, str, list[str]]] = []

    if data.get("report"):
        processed = process_item(data["report"], nickname_map, mode)
        issues = verify_item(processed, nickname_map, mode)
        if issues:
            dropped.append(("report", "report", issues))
        else:
            result["report"] = processed

    for kind in ("faq", "tips", "actions"):
        for item in data.get(kind, []):
            processed = process_item(item, nickname_map, mode)
            issues = verify_item(processed, nickname_map, mode)
            item_id = item.get("id") or "?"
            if issues:
                dropped.append((kind, item_id, issues))
            else:
                result[kind].append(processed)

    output_path = output_path or (INSIGHTS_DIR / f"period_{period_id}.final.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    for kind, item_id, issues in dropped:
        escalate(
            "A6",
            f"{kind}({item_id}) 발행 보류 — 재검사 실패: {'; '.join(issues)}",
            period_id=period_id,
        )

    passed_count = len(result["faq"]) + len(result["tips"]) + len(result["actions"]) + (
        1 if result["report"] else 0
    )
    detail = (
        f"통과 {passed_count}건, 보류(재검사 실패) {len(dropped)}건, 모드={mode}, "
        f"닉네임 사전 {len(nickname_map)}개(구간 {lookup_period_id}), 운영진 제외 {len(exempt)}명"
    )
    log_event("A6", "success", period_id=period_id, detail=detail)
    print(f"[apply] {detail} -> {output_path}")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="A6: PII 마스킹 + 닉네임 익명화 적용")
    parser.add_argument("input", type=Path, help="A5 검증 통과 항목 JSON (draft/eval 스키마)")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    run(args.input, args.output)


if __name__ == "__main__":
    main()
