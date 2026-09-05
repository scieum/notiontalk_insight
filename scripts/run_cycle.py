"""파이프라인 오케스트레이션 진입점 — launchd가 부르는 실제 실행 파일.

collect_publish (파이프라인 A):
    A0  katok 추출        — 실기기 검증 전(README 구현 상태 표). 지금은 skip+log.
    A0' 내보내기 파일     — inbox/raw/ 에 새로 들어온 csv/txt를 찾는다. 없으면 알림 후 종료.
    A1  정규화            — parse_csv.py / parse_txt.py
    A2  병합              — merge.py  (신규 0건이면 여기서 종료, CLAUDE.md §5)
    A3  스레드 분할       — split_threads.py
    A4  인사이트 추출     — insight-extractor/extract.py  (--since = 지난 리포트 끝, C3)
    A5  검증              — insight-evaluator/evaluate.py + apply_verdicts.py
    A6  PII 마스킹        — pii-guard/apply.py
    렌더                  — render_report.py (검토용 HTML, output/reports/)
    A7  노션 발행         — notion-publish/publish_report.py. 토큰이 없으면 skip+log.
                            리포트는 항상 '초안'으로만 올라간다(C5).
    A8  KB 갱신           — kb-builder 미구현. skip+log.

각 단계는 기존 스킬 스크립트를 **서브프로세스로** 부른다 — 스크립트들은 이미
자기 로그·에스컬레이션을 남기므로 여기서는 순서·입출력 경로·중단 조건만 맡는다.
생성(A4)과 평가(A5)는 별도 프로세스·별도 프롬프트로 분리돼 있다(C9).

상태 파일 output/state.json:
    last_report_until   지난 리포트가 다룬 마지막 메시지 시각. 다음 A4의 --since.
                        Mac이 꺼져 사이클을 건너뛰어도 다음 실행이 여기서 이어받는다(C3).
    last_issue          마지막 호수. 다음 호 = +1.
    processed_inputs    이미 A1~A2를 거친 inbox/raw 파일명. 같은 파일을 두 번 넣지 않는다.
    rejections          G-R 반려 사유(최근 N건). A4 프롬프트에 주입한다(CLAUDE.md §6).

실행:
    .venv/bin/python scripts/run_cycle.py --pipeline collect_publish
    # 이미 병합된 구간을 다시 A4부터 돌릴 때:
    .venv/bin/python scripts/run_cycle.py --pipeline collect_publish \\
        --period-id 2025-01-26_A --since 2026-08-31
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".claude/agents/insight-extractor/scripts"))

from lib.common.escalate import escalate
from lib.common.log import log_event
from lib.common.paths import (
    INBOX_RAW_DIR, INSIGHTS_DIR, MESSAGES_PARSED_DIR, OUTPUT_DIR, ensure_output_dirs,
)

STATE_PATH = OUTPUT_DIR / "state.json"
FONT_PATH = ROOT / "assets/fonts/PretendardVariable.woff2"
MAX_REJECTIONS = 3  # 설계서 O7 미해결 — CLAUDE.md §6 임시값

SKILLS = ROOT / ".claude/skills"
AGENTS = ROOT / ".claude/agents"
SCRIPT = {
    "parse_csv": SKILLS / "kakao-parser/scripts/parse_csv.py",
    "parse_txt": SKILLS / "kakao-parser/scripts/parse_txt.py",
    "verify_export": SKILLS / "kakao-export/scripts/verify_export.py",
    "merge": SKILLS / "kakao-parser/scripts/merge.py",
    "split_threads": SKILLS / "threading/scripts/split_threads.py",
    "extract": AGENTS / "insight-extractor/scripts/extract.py",
    "evaluate": AGENTS / "insight-evaluator/scripts/evaluate.py",
    "apply_verdicts": AGENTS / "insight-evaluator/scripts/apply_verdicts.py",
    "pii_apply": SKILLS / "pii-guard/scripts/apply.py",
    "render": ROOT / "scripts/render_report.py",
    "publish": SKILLS / "notion-publish/scripts/publish_report.py",
}

PIPELINE_STAGES = {
    "collect_publish": ["A0", "A0'", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"],
    "kb_refresh": ["B1", "B2"],
    "poll_questions": ["B3", "B4", "B5", "B6", "B7"],
}


class StageFailed(RuntimeError):
    pass


# ── 공용 ────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"last_report_until": None, "last_issue": 0, "processed_inputs": [], "rejections": []}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def run(stage: str, script: Path, *args: str, timeout: int = 3600) -> str:
    """스킬 스크립트를 같은 인터프리터(.venv)로 부른다. 실패하면 StageFailed."""
    cmd = [sys.executable, str(script), *args]
    print(f"[{stage}] {script.name} {' '.join(args)}")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    tail = "\n".join(line for line in proc.stdout.splitlines()
                     if "Warning" not in line and "warnings.warn" not in line)[-1500:]
    if tail:
        print("    " + tail.replace("\n", "\n    "))
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        raise StageFailed(f"{script.name} rc={proc.returncode}: {err[-1] if err else '(stderr 없음)'}")
    return proc.stdout


def notify(title: str, body: str) -> None:
    """macOS 알림. 실패해도 파이프라인을 막지 않는다(G-E 보조 수단일 뿐)."""
    if not shutil.which("osascript"):
        return
    safe = lambda s: s.replace('"', "'")
    subprocess.run(
        ["osascript", "-e", f'display notification "{safe(body)}" with title "{safe(title)}"'],
        capture_output=True, timeout=10,
    )


def notion_token_present() -> bool:
    if (ROOT / ".env").exists() and "NOTION" in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore"):
        return True
    proc = subprocess.run(["security", "find-generic-password", "-s", "talkinsight-notion"],
                          capture_output=True, timeout=10)
    return proc.returncode == 0


# ── A0' 입력 찾기 ─────────────────────────────────────────────────────────

def _nfc(name: str) -> str:
    """macOS는 파일명의 한글을 NFD(자모 분리)로 돌려준다. 비교·저장은 항상 NFC로."""
    return unicodedata.normalize("NFC", name)


def mark_processed(state: dict, name: str) -> None:
    done = state.setdefault("processed_inputs", [])
    if _nfc(name) not in {_nfc(n) for n in done}:
        done.append(_nfc(name))


def pending_inputs(state: dict) -> list[Path]:
    done = {_nfc(n) for n in state.get("processed_inputs") or []}
    files = [p for p in INBOX_RAW_DIR.iterdir()
             if p.is_file() and p.suffix.lower() in (".csv", ".txt") and _nfc(p.name) not in done]
    return sorted(files, key=lambda p: p.stat().st_mtime)


# ── 파이프라인 A ─────────────────────────────────────────────────────────

def collect_publish(args) -> int:
    state = load_state()
    since = args.since or state.get("last_report_until") \
        or (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    period_id = args.period_id

    if period_id:
        # 재실행 경로: 이미 병합·분할된 구간을 A4부터 다시 돈다.
        log_event("A0", "skip", detail="--period-id 지정: 수집 단계 생략")
        log_event("A0'", "skip", detail="--period-id 지정: 수집 단계 생략")
    else:
        # A0 — katok 경로는 실기기 검증 전(README 구현 상태 표). 검증되면 여기서
        # katok_extract.py를 부르고, 실패 시 A0'로 넘어가는 구조로 바꾼다(CLAUDE.md §2').
        log_event("A0", "skip", detail="katok 경로 실기기 미검증 — A0'(내보내기 파일)로 진행")

        # A0' — 운영자가 넣어 둔 내보내기 파일
        inputs = pending_inputs(state)
        if not inputs:
            msg = "inbox/raw/ 에 새 내보내기 파일이 없습니다. 카카오톡 → 채팅방 설정 → 대화 내용 관리 → '텍스트 파일로 저장' 후 inbox/raw/ 에 넣어주세요."
            log_event("A0'", "skip", detail=msg)
            notify("TalkInsight — 입력 대기", msg)
            print(msg)
            return 0
        src = inputs[-1]  # 가장 최근 파일 하나. 내보내기는 누적이라 최신 파일이 이전 것을 포함한다.
        out = run("A0'", SCRIPT["verify_export"], str(src), "--since", since, "--json")
        verdict = (json.loads(out) if out.strip().startswith("{") else {}).get("verdict")
        if verdict in ("empty", "stale"):
            log_event("A0'", "skip", detail=f"{src.name}: {verdict} — 신규 구간 없음")
            mark_processed(state, src.name)
            save_state(state)
            print(f"{src.name}: {verdict}. 발행 사이클을 실행하지 않습니다.")
            return 0
        if verdict == "suspect":
            escalate("A0'", f"내보내기 파일 형식이 인식되지 않습니다: {src.name}", files=[str(src)])
            notify("TalkInsight — 에스컬레이션", f"내보내기 파일 형식 미인식: {src.name}")
            return 1

        # A1
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        parsed = MESSAGES_PARSED_DIR / f"{ts}.jsonl"
        parser = SCRIPT["parse_csv"] if src.suffix.lower() == ".csv" else SCRIPT["parse_txt"]
        run("A1", parser, str(src), str(parsed))

        # A2
        out = run("A2", SCRIPT["merge"], str(parsed))
        mark_processed(state, src.name)
        save_state(state)
        m = re.search(r"\[merge\] period (\S+)", out)
        if not m:
            log_event("A2", "skip", detail="신규 메시지 0건 — 발행 사이클 미실행")
            print("신규 메시지가 없습니다. 발행 사이클을 실행하지 않습니다.")
            return 0
        period_id = m.group(1)

        # A3
        run("A3", SCRIPT["split_threads"], period_id)

    # A4 — 지난 리포트 끝(since)부터. 반려 사유(G-R)가 있으면 최근 N건 주입.
    from select_slice import slice_id  # extract.py와 같은 규칙으로 산출물 이름을 만든다
    sid = slice_id(period_id, since, args.until)
    draft = INSIGHTS_DIR / f"period_{sid}.draft.json"
    evalj = INSIGHTS_DIR / f"period_{sid}.eval.json"
    passed = INSIGHTS_DIR / f"period_{sid}.passed.json"
    final = INSIGHTS_DIR / f"period_{sid}.final.json"

    extract_args = ["--period-id", period_id, "--since", since]
    if args.until:
        extract_args += ["--until", args.until]
    for r in (state.get("rejections") or [])[-MAX_REJECTIONS:]:
        extract_args += ["--rejection", r]
    run("A4", SCRIPT["extract"], *extract_args)
    if not draft.exists():
        log_event("A4", "skip", detail=f"draft 없음 — 구간 {sid}에 스레드가 없었을 수 있음")
        print(f"이번 구간({since} ~)에 새 스레드가 없어 리포트를 만들지 않았습니다.")
        return 0

    # A5 — 생성과 분리된 평가 (C9)
    run("A5", SCRIPT["evaluate"], "--draft", str(draft))
    run("A5", SCRIPT["apply_verdicts"], "--draft", str(draft), "--eval", str(evalj))

    # A6
    run("A6", SCRIPT["pii_apply"], str(passed))

    # 검토용 HTML
    issue = int(state.get("last_issue") or 0) + 1
    render_args = ["--final", str(final), "--draft", str(draft), "--eval", str(evalj),
                   "--issue", str(issue)]
    if FONT_PATH.exists():
        render_args += ["--font", str(FONT_PATH)]
    out = run("render", SCRIPT["render"], *render_args)
    report_path = next((line.strip()[3:].strip() for line in out.splitlines()
                        if line.strip().startswith("->")), "output/reports/")

    # A7 — 토큰이 있을 때만. 항상 '초안'(C5).
    published = False
    if args.no_publish:
        log_event("A7", "skip", detail="--no-publish")
    elif not notion_token_present():
        log_event("A7", "skip", detail="노션 토큰 없음(talkinsight-notion) — HTML 검토본만 생성")
    else:
        try:
            run("A7", SCRIPT["publish"], "--final", str(final), "--draft", str(draft))
            published = True
        except StageFailed as e:
            # publish_report.py가 이미 로그·에스컬레이션을 남겼다. 다음 사이클에 재시도.
            print(f"[A7] 실패 — 다음 사이클에 재시도: {e}")

    log_event("A8", "skip", detail="kb-builder 미구현")

    # 상태 갱신 — 다음 사이클은 이번 리포트의 마지막 메시지 이후부터 본다(C3).
    state["last_issue"] = issue
    state["last_report_until"] = _slice_end(draft) or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    save_state(state)

    where = "노션 초안 + " if published else ""
    notify("TalkInsight — 리포트 준비됨",
           f"{issue}호 {where}HTML 검토본이 준비됐습니다. 검토 후 발행해 주세요.")
    print(f"\n완료: {issue}호 → {report_path}")
    return 0


def _slice_end(draft: Path) -> str | None:
    """이번 호가 실제로 다룬 마지막 메시지 시각(+1초)을 messages.sqlite에서 읽는다."""
    import sqlite3
    from lib.common.paths import MESSAGES_DB_PATH
    try:
        d = json.loads(draft.read_text(encoding="utf-8"))
        conn = sqlite3.connect(f"file:{MESSAGES_DB_PATH}?mode=ro", uri=True)
        sql = "SELECT MAX(ts) FROM messages WHERE period_id = ? AND ts >= ?"
        params = [d.get("source_period_id") or d["period_id"], d.get("since") or ""]
        if d.get("until"):
            sql += " AND ts < ?"
            params.append(d["until"])
        (hi,) = conn.execute(sql, params).fetchone()
        conn.close()
        if hi:
            return (datetime.fromisoformat(hi) + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None
    return None


# ── 파이프라인 B (미구현) ─────────────────────────────────────────────────

def run_placeholder(name: str) -> int:
    stages = PIPELINE_STAGES[name]
    print(f"[run_cycle] {name} 시작 — 단계: {', '.join(stages)}")
    for stage in stages:
        log_event(stage, "skip", detail="스킬 미구현 (V0 스캐폴딩 단계)")
        print(f"  - {stage}: skip (미구현)")
    print(f"[run_cycle] {name} 종료")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="TalkInsight 파이프라인 실행 진입점")
    ap.add_argument("--pipeline", required=True, choices=sorted(PIPELINE_STAGES.keys()))
    ap.add_argument("--period-id", help="이미 병합된 구간을 A4부터 다시 돌린다")
    ap.add_argument("--since", help="A4 슬라이스 시작. 생략하면 state.json의 last_report_until")
    ap.add_argument("--until", help="A4 슬라이스 끝(미포함). 보통 생략")
    ap.add_argument("--no-publish", action="store_true", help="A7(노션)을 건너뛰고 HTML만 만든다")
    args = ap.parse_args()

    ensure_output_dirs()
    if args.pipeline != "collect_publish":
        raise SystemExit(run_placeholder(args.pipeline))

    print(f"[run_cycle] collect_publish 시작 {datetime.now():%Y-%m-%d %H:%M}")
    try:
        rc = collect_publish(args)
    except StageFailed as e:
        stage = str(e).split(" ")[0]
        log_event("run_cycle", "failure", detail=str(e)[:300])
        escalate("run_cycle", f"파이프라인 중단: {e}")
        notify("TalkInsight — 실패", f"{stage} 단계에서 멈췄습니다. output/escalations/ 확인")
        print(f"\n[중단] {e}")
        rc = 1
    print(f"[run_cycle] collect_publish 종료 rc={rc}")
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
