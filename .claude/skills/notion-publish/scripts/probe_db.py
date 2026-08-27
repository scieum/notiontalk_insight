"""노션 데이터베이스 스키마 확인 (A7 구현 전 1회, 그리고 속성이 바뀔 때마다).

발행 스크립트가 어떤 속성에 무엇을 넣을지 정하려면 먼저 그 DB가 어떤 속성을
가졌는지 알아야 한다. 노션 DB는 사람이 언제든 속성을 추가·삭제·개명할 수 있으므로
코드에 속성 이름을 박아두기 전에 실제 스키마를 확인한다.

읽기만 한다 — 이 스크립트는 아무것도 만들거나 바꾸지 않는다.

    python3 .claude/skills/notion-publish/scripts/probe_db.py <DB URL 또는 ID>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import notion_client as nc


def describe(prop: dict) -> str:
    kind = prop.get("type", "?")
    if kind == "select":
        opts = [o["name"] for o in prop.get("select", {}).get("options", [])]
        return f"select — 선택지: {opts}" if opts else "select — 선택지 없음"
    if kind == "multi_select":
        opts = [o["name"] for o in prop.get("multi_select", {}).get("options", [])]
        return f"multi_select — 선택지: {opts}" if opts else "multi_select — 선택지 없음"
    if kind == "status":
        groups = prop.get("status", {})
        opts = [o["name"] for o in groups.get("options", [])]
        return f"status — 선택지: {opts}"
    if kind == "relation":
        return f"relation → {prop.get('relation', {}).get('database_id', '?')}"
    if kind == "formula":
        return "formula (쓰기 불가)"
    if kind in ("created_time", "created_by", "last_edited_time", "last_edited_by", "rollup"):
        return f"{kind} (쓰기 불가)"
    return kind


def main() -> None:
    ap = argparse.ArgumentParser(description="노션 DB 스키마 확인 (읽기 전용)")
    ap.add_argument("target", help="데이터베이스 URL 또는 ID")
    ap.add_argument("--rows", type=int, default=0, help="기존 행을 N개까지 훑어본다(제목만 표시)")
    ap.add_argument("--raw", action="store_true", help="응답 원본 JSON 출력")
    args = ap.parse_args()

    db_id = nc.normalize_id(args.target)
    print(f"데이터베이스 {db_id}\n")

    try:
        db = nc.request("GET", f"/databases/{db_id}")
    except nc.NotionError as e:
        if e.status == 404:
            print("[404] 통합이 이 데이터베이스에 접근할 수 없습니다.\n"
                  "  노션에서 해당 DB 우상단 ··· → '연결' → 만든 통합을 추가하십시오.")
        elif e.status == 401:
            print("[401] 토큰이 유효하지 않습니다. Keychain의 talkinsight-notion 값을 확인하십시오.")
        else:
            print(f"[{e.status}] {e.body[:400]}")
        raise SystemExit(1)

    if args.raw:
        print(json.dumps(db, ensure_ascii=False, indent=2))
        return

    title = "".join(t.get("plain_text", "") for t in db.get("title", []))
    print(f"제목: {title or '(없음)'}")
    print(f"객체: {db.get('object')} · 생성 {db.get('created_time', '')[:10]}\n")

    props = db.get("properties", {})
    print(f"속성 {len(props)}개")
    for name, prop in sorted(props.items(), key=lambda kv: (kv[1].get("type") != "title", kv[0])):
        mark = "제목" if prop.get("type") == "title" else "    "
        print(f"  [{mark}] {name:<20} {describe(prop)}")

    if args.rows:
        res = nc.request("POST", f"/databases/{db_id}/query", {"page_size": args.rows})
        rows = res.get("results", [])
        print(f"\n기존 행 {len(rows)}개 (최대 {args.rows} 요청)")
        for r in rows:
            tprop = next((v for v in r.get("properties", {}).values() if v.get("type") == "title"), {})
            t = "".join(x.get("plain_text", "") for x in tprop.get("title", []))
            print(f"  - {t or '(제목 없음)'}")


if __name__ == "__main__":
    main()
