"""B4: kb.sqlite에서 질의 임베딩과 유사한 청크를 찾아 원천별로 리랭킹한다.

설계서 §2.5 B4, D10(로컬 임베딩 검색 + 메타데이터 필터 하이브리드).
벡터 유사도 상위 20 -> 원천별 리랭킹(chat<=4, notion_help<=4,
notiontalk_site<=2, superseded 제외) -> 최종 K<=10.

임베딩 자체(embed_client.embed)는 API 키가 필요해 여기선 검증 못 했지만,
이 파일의 검색/리랭킹 로직은 임베딩이 이미 벡터로 주어졌다고 가정하므로
가짜 벡터로 완전히 테스트 가능하다.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from lib.common.kb_schema import decode_embedding, ensure_kb_schema
from lib.common.paths import KB_DB_PATH

DEFAULT_TOP_N = 20
DEFAULT_FINAL_K = 10
SOURCE_CAPS = {"chat": 4, "notion_help": 4, "notiontalk_site": 2}
MIN_TOP_SIMILARITY = 0.6  # 설계서 §2.5 B4 성공 기준


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"벡터 차원이 다릅니다: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def search(
    query_embedding: list[float],
    db_path: Path = KB_DB_PATH,
    top_n: int = DEFAULT_TOP_N,
) -> list[dict]:
    """superseded=0인 청크 중 유사도 상위 top_n을 반환한다 (내림차순 정렬)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_kb_schema(conn)
    rows = conn.execute("SELECT * FROM chunks WHERE superseded = 0").fetchall()
    conn.close()

    scored = []
    for r in rows:
        emb = decode_embedding(r["embedding"])
        sim = cosine_similarity(query_embedding, emb)
        item = dict(r)
        item["embedding"] = None  # 결과에는 원시 벡터를 담지 않는다 (불필요하게 큼)
        item["similarity"] = sim
        scored.append(item)

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_n]


def rerank_by_source(
    candidates: list[dict],
    source_caps: dict[str, int] = SOURCE_CAPS,
    final_k: int = DEFAULT_FINAL_K,
) -> list[dict]:
    """유사도 내림차순으로 정렬된 candidates에서 원천별 상한을 적용해 최종 K개를 뽑는다."""
    counts = {src: 0 for src in source_caps}
    result = []
    for c in candidates:
        cap = source_caps.get(c["source"])
        if cap is not None:
            if counts.get(c["source"], 0) >= cap:
                continue
            counts[c["source"]] = counts.get(c["source"], 0) + 1
        result.append(c)
        if len(result) >= final_k:
            break
    return result


def search_and_rerank(
    query_embedding: list[float],
    db_path: Path = KB_DB_PATH,
    top_n: int = DEFAULT_TOP_N,
    final_k: int = DEFAULT_FINAL_K,
) -> dict:
    """B4 전체 절차. 근거 부족 여부(성공 기준: 최상위 유사도 >= 0.6)도 함께 반환한다."""
    top = search(query_embedding, db_path, top_n)
    reranked = rerank_by_source(top, final_k=final_k)
    sufficient = bool(top) and top[0]["similarity"] >= MIN_TOP_SIMILARITY
    return {"evidence": reranked, "sufficient": sufficient}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="B4: 질의 임베딩(JSON 파일, 숫자 배열)으로 kb.sqlite 검색"
    )
    parser.add_argument("embedding_json", type=Path, help="질의 임베딩 벡터가 담긴 JSON 배열 파일")
    parser.add_argument("--top-k", type=int, default=DEFAULT_FINAL_K)
    args = parser.parse_args()

    query_embedding = json.loads(args.embedding_json.read_text(encoding="utf-8"))
    result = search_and_rerank(query_embedding, final_k=args.top_k)
    for item in result["evidence"]:
        print(f"{item['similarity']:.3f}  [{item['source']}]  {item['text'][:60]}")
    if not result["sufficient"]:
        print("경고: 최상위 유사도가 0.6 미만입니다 — 근거 부족", file=sys.stderr)


if __name__ == "__main__":
    main()
