"""A0 (katok 경로): 대상 오픈채팅방의 신규 메시지를 katok CLI로 추출한다.

설계서 §2.4 A0을 **직접 복호화가 아니라 katok CLI 위임**으로 구현한 것이다.
직접 복호화 계획(논문·gist 참조)은 폐기했다 — 근거는 docs/db_access_reference.md.

우리가 부르는 katok 명령은 셋뿐이다:

    katok doctor        --json                    준비 상태·최신성 확인
    katok source chats  --source macos --json     방 목록에서 대상 방 찾기
    katok sync          --source macos --json     증분 수집
    katok transcript    --chat <id> --since <ts>  대상 방만, 기간만 추출

**`katok send`는 절대 호출하지 않는다.** 카카오톡으로 메시지를 보내는 명령이고,
제약 C1이 "오픈채팅방 안에서 발언하는 비공식 클라이언트 사용 금지"로 금지한다.
아래 `_run()`이 허용 명령 화이트리스트로 강제한다 — 실수로도 나가지 않게.

실행 전 게이트 (CLAUDE.md §2, §8):
  docs/risk_acceptance.md 의 `상태`가 `승인`이어야 한다. 아니면 실행하지 않는다.
  이 스크립트는 그 값을 읽기만 하고 바꾸지 않는다.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from lib.common.escalate import escalate
from lib.common.log import log_event
from lib.common.paths import DOCS_DIR, INBOX_DB_EXTRACT_DIR

# 이 목록 밖의 katok 하위 명령은 실행하지 않는다. 특히 send.
ALLOWED_COMMANDS = {("doctor",), ("source", "chats"), ("sync",), ("transcript",)}
FORBIDDEN = {"send"}


class Blocked(RuntimeError):
    pass


def _run(args: list[str], timeout: int = 900) -> tuple[int, str, str]:
    """katok을 호출한다. 허용 명령이 아니면 실행 자체를 거부한다."""
    head = tuple(a for a in args if not a.startswith("-"))[:2]
    if any(a in FORBIDDEN for a in args):
        raise Blocked(f"금지된 katok 명령입니다: {args[0] if args else '?'} (C1: 발신 금지)")
    if not any(head[: len(allowed)] == allowed for allowed in ALLOWED_COMMANDS):
        raise Blocked(f"허용되지 않은 katok 명령입니다: {' '.join(args[:2])}")

    exe = shutil.which("katok")
    if not exe:
        raise Blocked("katok을 찾을 수 없습니다. `brew install katok` 후 다시 실행하십시오.")

    proc = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def _json_or_raw(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def check_risk_approval() -> None:
    """CLAUDE.md §8 게이트. `승인`이 아니면 여기서 멈춘다."""
    path = DOCS_DIR / "risk_acceptance.md"
    if not path.exists():
        raise Blocked(f"{path} 가 없습니다. A0을 실행할 수 없습니다.")
    m = re.search(r"^\*\*상태\*\*:\s*(\S+)", path.read_text(encoding="utf-8"), re.M)
    status = m.group(1) if m else "(읽을 수 없음)"
    if status != "승인":
        raise Blocked(
            f"docs/risk_acceptance.md 의 상태가 '{status}' 입니다. "
            f"운영자가 R1~R5를 읽고 직접 '승인'으로 바꾼 뒤에만 A0을 실행합니다."
        )


def room_name() -> str:
    path = DOCS_DIR / "room_profile.md"
    m = re.search(r"^- 방 이름:\s*(.+)$", path.read_text(encoding="utf-8"), re.M)
    if not m:
        raise Blocked("room_profile.md에서 방 이름을 찾지 못했습니다.")
    return m.group(1).strip().strip("*").strip()


def find_chat(chats, target: str):
    """방 목록에서 대상 방을 찾는다. **정확히 일치**만 인정한다.

    부분 일치로 찾으면 다른 방을 열 위험이 있다. 방 이름 끝의 이모지도 이름의
    일부다(R5 — 다른 방 데이터는 어떤 파일에도 남기지 않는다).
    """
    if isinstance(chats, dict):
        chats = chats.get("chats") or chats.get("items") or chats.get("data") or []
    exact = []
    for c in chats:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or c.get("title") or c.get("chat_name") or ""
        if name == target:
            exact.append(c)
    if len(exact) > 1:
        raise Blocked(f"'{target}' 이름의 방이 {len(exact)}개입니다. 사람이 구분해야 합니다.")
    return exact[0] if exact else None


def _chat_id(chat: dict):
    for key in ("id", "chat_id", "chatId", "chat_log_id"):
        if key in chat:
            return chat[key]
    raise Blocked(f"방 레코드에서 id를 찾지 못했습니다: {sorted(chat)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="A0: katok으로 대상 방 메시지 추출")
    ap.add_argument("--since", required=True, help="ISO8601 (예: 2026-08-26T00:00:00+09:00)")
    ap.add_argument("--no-sync", action="store_true", help="sync를 건너뛰고 기존 아카이브만 읽는다")
    ap.add_argument("--probe", action="store_true",
                    help="katok 출력 구조를 그대로 보여준다(첫 실행 시 형식 확인용)")
    args = ap.parse_args()

    try:
        check_risk_approval()

        print("[1/4] katok doctor")
        rc, out, err = _run(["doctor", "--json"])
        doctor = _json_or_raw(out)
        if args.probe:
            print(out[:2000] or err[:2000])
        if rc != 0:
            raise Blocked(f"doctor 실패(rc={rc}). 전체 디스크 접근 권한을 확인하십시오.\n{err[:500]}")
        if isinstance(doctor, dict):
            fresh = doctor.get("freshness", {})
            last = (fresh.get("last_sync") or {}).get("completed_at")
            print(f"      마지막 sync: {last or '없음'}")

        target = room_name()
        print(f"[2/4] 방 목록에서 '{target}' 찾기")
        rc, out, err = _run(["source", "chats", "--source", "macos", "--json"])
        if args.probe:
            print(out[:2000] or err[:2000])
        if rc != 0:
            raise Blocked(f"source chats 실패(rc={rc})\n{err[:500]}")
        chats = _json_or_raw(out)
        chat = find_chat(chats, target)
        if chat is None:
            raise Blocked(
                f"'{target}' 방을 찾지 못했습니다. 카카오톡에서 그 방을 한 번 열어 "
                f"동기화한 뒤 다시 시도하거나, --probe로 목록을 확인하십시오."
            )
        cid = _chat_id(chat)
        print(f"      찾음: chat id={cid}")

        if not args.no_sync:
            print("[3/4] katok sync (증분)")
            rc, out, err = _run(["sync", "--source", "macos", "--json"])
            if rc != 0:
                raise Blocked(f"sync 실패(rc={rc})\n{err[:500]}")
            info = _json_or_raw(out) or {}
            if args.probe:
                print(out[:2000])
            print(f"      {json.dumps(info, ensure_ascii=False)[:300]}")
        else:
            print("[3/4] sync 건너뜀")

        print(f"[4/4] transcript --chat {cid} --since {args.since}")
        rc, out, err = _run(["transcript", "--chat", str(cid), "--since", args.since, "--json"])
        if args.probe:
            print(out[:3000] or err[:3000])
        if rc != 0:
            raise Blocked(f"transcript 실패(rc={rc})\n{err[:500]}")
        result = _json_or_raw(out)
        print(f"      {json.dumps(result, ensure_ascii=False)[:600] if result else out[:600]}")

        INBOX_DB_EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
        meta = INBOX_DB_EXTRACT_DIR / f"katok_{args.since[:10].replace('-', '')}.meta.json"
        meta.write_text(json.dumps(
            {"since": args.since, "chat_id": cid, "transcript": result}, ensure_ascii=False, indent=2
        ), encoding="utf-8")
        print(f"\n메타 기록: {meta}")
        log_event("A0", "success", detail=f"katok transcript 추출 (since={args.since})",
                  extra={"chat_id": str(cid)})

    except Blocked as e:
        print(f"\n[중단] {e}")
        log_event("A0", "failure", detail=str(e)[:300])
        raise SystemExit(1)
    except subprocess.TimeoutExpired:
        msg = "katok 호출이 시간 안에 끝나지 않았습니다."
        print(f"\n[중단] {msg}")
        log_event("A0", "failure", detail=msg)
        escalate("A0", msg)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
