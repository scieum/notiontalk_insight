"""Gemini 임베딩 호출 래퍼 (설계서 D8: text-embedding-004). kb-builder(A8/B2)와
kb-search(질의 임베딩)가 사용한다.

**실 API 호출 경로는 미검증** — llm.py와 동일한 사유. retry.py/budget.py/auth.py는
단위 테스트로 검증됨.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from auth import get_api_key
from budget import TokenBudget
from retry import is_transient_api_error, with_retry

from lib.common.log import log_event

EMBEDDING_MODEL = "gemini-embedding-001"  # 설계서 D8(개정).
# 2026-08-26: text-embedding-004는 embedContent를 더 지원하지 않는다(404).
# 실측 차원 3072. gemini-embedding-2도 동일 차원이나, 안정 버전인 001로 고정한다.
# 주의: 모델을 바꾸면 기존 kb.sqlite의 벡터와 비교가 무의미해진다 — 전량 재임베딩이 필요하다.
REQUEST_TIMEOUT_MS = 120_000  # 임베딩은 생성보다 짧게 잡는다
EMBEDDING_DIM = 3072  # 실측값. kb-builder/kb-search가 차원 일치 검사에 쓴다.


def _call_gemini_embed(text: str, api_key: str):
    """미검증: 실제 google-genai SDK 임베딩 호출부. 키 확보 후 V4에서 검증 필요."""
    try:
        from google import genai
    except ImportError as e:
        raise RuntimeError(
            "google-genai 패키지가 설치되어 있지 않습니다. `pip install google-genai`로 설치하세요."
        ) from e

    from google.genai import types

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
    return client.models.embed_content(model=EMBEDDING_MODEL, contents=text)


def embed(
    text: str,
    *,
    max_retries: int = 3,
    budget: TokenBudget | None = None,
    sleep_fn=time.sleep,
) -> list[float]:
    """텍스트 1건을 임베딩 벡터로 변환한다."""
    api_key = get_api_key()

    response = with_retry(
        lambda: _call_gemini_embed(text, api_key),
        max_retries=max_retries,
        is_retryable=is_transient_api_error,
        sleep_fn=sleep_fn,
    )

    usage = getattr(response, "usage_metadata", None)
    if budget is not None and usage is not None:
        budget.charge(getattr(usage, "total_token_count", 0))

    log_event(
        "gemini-client", "success",
        detail=f"embed 완료 (텍스트 {len(text)}자, 모델 {EMBEDDING_MODEL})",
    )
    return response.embeddings[0].values
