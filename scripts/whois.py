"""가명 ↔ 실제 닉네임 조회 (운영자 전용, 로컬에서만).

리포트를 검토(G-R)하다 "함께한 선생님G가 누구지?"를 확인해야 할 때 쓴다.

**대응표를 파일로 저장하지 않는다.** 그 파일 자체가 재식별 열쇠라서, 만들어 두면
익명화의 의미가 줄어든다(C2, R4). 대신 A6와 똑같은 방식으로 그때그때 다시 계산한다 —
같은 구간·같은 범위면 항상 같은 결과가 나온다.

사용:
    python3 scripts/whois.py --draft output/insights/period_<id>.draft.json "함께한 선생님G"
    python3 scripts/whois.py --draft ... --reverse "1000쌤(정보부장)"
    python3 scripts/whois.py --draft ... --all
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
sys.path.insert(0, str(_HERE.parents[1] / ".claude/skills/pii-guard/scripts"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import anonymize
from apply import get_period_nicknames


def build_map(period_id: str, since: str | None, until: str | None) -> dict[str, str]:
    nicks = get_period_nicknames(period_id, since=since, until=until)
    return anonymize.build_nickname_map(nicks)


def main() -> None:
    ap = argparse.ArgumentParser(description="가명 ↔ 닉네임 조회 (저장하지 않는다)")
    ap.add_argument("name", nargs="?", help="찾을 가명 (예: 함께한 선생님G)")
    ap.add_argument("--draft", required=True,
                    help="그 호의 draft/passed/eval json (period_id·since·until을 읽는다)")
    ap.add_argument("--reverse", help="반대로: 실제 닉네임으로 가명 찾기")
    ap.add_argument("--all", action="store_true", help="전체 대응 출력 (화면에만)")
    args = ap.parse_args()

    meta = json.loads(Path(args.draft).read_text(encoding="utf-8"))
    period_id = meta.get("source_period_id") or meta["period_id"]
    mapping = build_map(period_id, meta.get("since"), meta.get("until"))

    if not mapping:
        print(f"구간 '{period_id}'에서 닉네임을 찾지 못했습니다. period_id를 확인하십시오.")
        raise SystemExit(1)

    reverse: dict[str, list[str]] = {}
    for real, pseudo in mapping.items():
        reverse.setdefault(pseudo, []).append(real)

    if args.all:
        print(f"구간 {period_id} · {meta.get('since') or '처음'} ~ {meta.get('until') or '오늘'}")
        print(f"가명 {len(reverse)}명\n")
        for pseudo in sorted(reverse, key=lambda p: (len(p), p)):
            print(f"  {pseudo:>8}  {' / '.join(reverse[pseudo])}")
        return

    if args.reverse:
        pseudo = mapping.get(args.reverse)
        print(f"{args.reverse} -> {pseudo}" if pseudo else
              f"'{args.reverse}'는 이 범위에 등장하지 않습니다.")
        return

    if not args.name:
        ap.error("가명을 지정하거나 --reverse / --all 을 쓰십시오.")

    names = reverse.get(args.name)
    if names:
        print(f"{args.name} = {' / '.join(names)}")
    else:
        print(f"'{args.name}'에 해당하는 사람이 없습니다. --all 로 전체를 확인하십시오.")


if __name__ == "__main__":
    main()
