"""Gemini 생성 호출 래퍼 (설계서 D8: gemini-2.0-flash). 재시도·예산·로깅을 통일한다.

**실 API 호출 경로는 미검증** — API 키가 없는 환경(현재 세션)에서는 테스트할
수 없었다. retry.py/budget.py/auth.py(재시도, 예산, 키 조회)는 각각 단위
테스트로 검증됨. 이 파일의 _call_gemini()만 실 키로 검증이 필요하다.

호출하는 서브에이전트(insight-extractor 등)가 생성과 평가를 분리 호출하는
책임을 진다(C9) — 이 래퍼는 그 분리를 강제하지 않고 메커니즘만 제공한다.
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

GENERATION_MODEL = "gemini-2.5-flash"  # 설계서 D8(개정). 바꾸려면 여기만 고치면 됨.
# 2026-08-26: D8이 정한 gemini-2.0-flash는 단종됐다(404 "no longer available",
# 서버가 3.6-flash로 갈아타라고 안내). gemini-3.7-flash는 같은 시점에 503
# UNAVAILABLE("experiencing high load")이라 3.6-flash로 고정했다.
# `gemini-flash-latest` 같은 별칭은 쓰지 않는다 — 모델이 조용히 바뀌면
# 프롬프트 동작이 함께 바뀌는데 그 변화를 아무도 눈치채지 못한다.
#
# **왜 3.6-flash가 아니라 2.5-flash인가**: 3.6-flash는 무료 등급에서
# 하루 20요청이 상한이다(429 RESOURCE_EXHAUSTED, quotaValue 20). A4 한 사이클이
# 배치 8개 + 리포트 1개 = 9요청이므로 하루 두 번 돌리면 바닥난다. 주 2회 발행에
# A5 채점·재생성까지 얹으면 절대 안 맞는다. 2.5-flash는 같은 무료 등급에서
# 할당량이 훨씬 크다. 유료 결제를 붙이면 그때 최신 모델로 다시 올린다.


# 요청 하나가 이 시간을 넘기면 끊는다. 타임아웃이 없으면 소켓 read에서 무한정
# 멈춘다 — 2026-08-26에 실제로 A4가 한 호출에서 10분 넘게 블록됐고, launchd
# 무인 실행이었다면 사이클 전체가 영원히 멈춰 있었을 것이다.
# 끊긴 요청은 상태 코드가 없어 is_transient_api_error가 재시도 대상으로 본다.
# 평가(A5/B6)는 생성과 **다른 모델**을 쓴다. 이유가 둘이다:
# 1) C9 — 같은 모델이 생성하고 채점하면 같은 맹점을 공유한다.
# 2) 무료 등급 할당량이 모델별로 따로 계산된다(모델당 하루 20요청). 생성과 평가가
#    같은 모델을 쓰면 A4가 할당량을 다 먹고 A5가 못 돈다 — 2026-08-26에 실제로 겪었다.
EVALUATION_MODEL = "gemini-3.5-flash"

REQUEST_TIMEOUT_MS = 300_000  # 5분


def _call_gemini(prompt: str, api_key: str, timeout_ms: int = REQUEST_TIMEOUT_MS,
                 model: str = GENERATION_MODEL):
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise RuntimeError(
            "google-genai 패키지가 설치되어 있지 않습니다. `.venv/bin/pip install google-genai`로 설치하세요."
        ) from e

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))
    return client.models.generate_content(model=model, contents=prompt)


def generate(
    prompt: str,
    *,
    max_retries: int = 3,
    budget: TokenBudget | None = None,
    timeout_ms: int = REQUEST_TIMEOUT_MS,
    model: str = GENERATION_MODEL,
    sleep_fn=time.sleep,
) -> str:
    """프롬프트를 Gemini에 보내고 생성된 텍스트를 반환한다."""
    api_key = get_api_key()

    response = with_retry(
        lambda: _call_gemini(prompt, api_key, timeout_ms, model),
        max_retries=max_retries,
        is_retryable=is_transient_api_error,
        sleep_fn=sleep_fn,
    )

    usage = getattr(response, "usage_metadata", None)
    if budget is not None and usage is not None:
        budget.charge(getattr(usage, "total_token_count", 0))

    log_event(
        "gemini-client", "success",
        detail=f"generate 완료 (프롬프트 {len(prompt)}자, 모델 {model})",
    )
    return response.text


def evaluate(prompt: str, **kwargs) -> str:
    """평가용 호출. generate()와 같은 경로지만 EVALUATION_MODEL을 쓴다."""
    return generate(prompt, model=EVALUATION_MODEL, **kwargs)
