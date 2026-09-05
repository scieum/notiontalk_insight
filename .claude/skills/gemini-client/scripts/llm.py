"""LLM 생성 호출 래퍼. 재시도·예산·로깅을 통일한다.

백엔드는 둘이다 (2026-09-05 운영자 결정, CLAUDE.md C4 개정):

- **claude** (기본) — 이 Mac에 로그인된 Claude Code CLI를 `claude -p`로 헤드리스
  호출한다. API 키가 필요 없고(구독 로그인 사용), 무료 등급 할당량 문제가 없다.
  2026-09-05에 Gemini 무료 등급이 하루 중 바닥나 A5 재생성이 전부 실패한 것이
  전환 계기다. 프로젝트 밖의 빈 디렉터리를 cwd로 써서 이 저장소의 CLAUDE.md·
  스킬이 프롬프트에 섞이지 않게 하고, `--tools ""`로 도구 사용을 막아 순수
  텍스트 생성만 하게 한다.
- **gemini** — 이전 경로. `TALKINSIGHT_LLM_PROVIDER=gemini`로 켠다. Keychain의
  `talkinsight-gemini` 키가 필요하다.

호출하는 서브에이전트(insight-extractor 등)가 생성과 평가를 분리 호출하는
책임을 진다(C9) — 이 래퍼는 그 분리를 강제하지 않고 메커니즘만 제공한다.
평가는 생성과 **다른 모델**을 쓴다(같은 모델이 만들고 채점하면 같은 맹점을 공유한다).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from budget import TokenBudget
from retry import is_transient_api_error, with_retry

from lib.common.log import log_event

PROVIDER = os.environ.get("TALKINSIGHT_LLM_PROVIDER", "claude").strip().lower()

# ── Claude CLI ──────────────────────────────────────────────────────────
CLAUDE_GENERATION_MODEL = "claude-sonnet-5"
CLAUDE_EVALUATION_MODEL = "claude-opus-5"
# `claude -p`를 돌릴 빈 작업 디렉터리. 프로젝트 안에서 부르면 CLAUDE.md·.claude/skills가
# 시스템 프롬프트로 딸려 들어가 토큰을 먹고 동작이 바뀐다.
CLAUDE_CWD = Path.home() / "Library/Application Support/talkinsight/llm-cwd"

# ── Gemini ──────────────────────────────────────────────────────────────
# gemini-2.0-flash(설계서 D8)는 단종. 3.6-flash는 무료 등급 하루 20요청이라
# 2.5-flash로 내렸다(2026-08-26). 평가는 할당량이 따로 계산되는 다른 모델.
GEMINI_GENERATION_MODEL = "gemini-2.5-flash"
GEMINI_EVALUATION_MODEL = "gemini-3.5-flash"

if PROVIDER == "gemini":
    GENERATION_MODEL = GEMINI_GENERATION_MODEL
    EVALUATION_MODEL = GEMINI_EVALUATION_MODEL
else:
    GENERATION_MODEL = CLAUDE_GENERATION_MODEL
    EVALUATION_MODEL = CLAUDE_EVALUATION_MODEL

# 요청 하나가 이 시간을 넘기면 끊는다. 타임아웃이 없으면 소켓 read에서 무한정
# 멈춘다 — 2026-08-26에 실제로 A4가 한 호출에서 10분 넘게 블록됐고, launchd
# 무인 실행이었다면 사이클 전체가 영원히 멈춰 있었을 것이다.
REQUEST_TIMEOUT_MS = 300_000  # 5분


class ClaudeCLIError(RuntimeError):
    """claude -p 가 비정상 종료했거나 오류 결과를 돌려줬다. 재시도 대상."""


def _claude_exe() -> str:
    exe = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    if not Path(exe).exists():
        raise RuntimeError("claude CLI를 찾을 수 없습니다. Claude Code가 설치·로그인돼 있어야 합니다.")
    return exe


def _call_claude(prompt: str, timeout_ms: int, model: str) -> tuple[str, int]:
    """claude -p 한 번. (본문, 소비 토큰 수)를 돌려준다."""
    CLAUDE_CWD.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)  # Claude Code 세션 안에서 불려도 중첩 실행을 허용
    cmd = [_claude_exe(), "-p", "--output-format", "json", "--model", model, "--tools", ""]
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            cwd=CLAUDE_CWD, env=env, timeout=timeout_ms / 1000,
        )
    except subprocess.TimeoutExpired as e:
        raise ClaudeCLIError(f"claude -p 타임아웃 ({timeout_ms // 1000}s)") from e
    if proc.returncode != 0:
        raise ClaudeCLIError(f"claude -p rc={proc.returncode}: {(proc.stderr or proc.stdout)[-300:]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ClaudeCLIError(f"claude -p 출력이 JSON이 아닙니다: {proc.stdout[:200]!r}") from e
    if data.get("is_error") or data.get("subtype") != "success":
        raise ClaudeCLIError(f"claude -p 오류 결과: {json.dumps(data, ensure_ascii=False)[:300]}")
    usage = data.get("usage") or {}
    tokens = sum(int(usage.get(k) or 0) for k in
                 ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return str(data.get("result") or ""), tokens


def _call_gemini(prompt: str, api_key: str, timeout_ms: int = REQUEST_TIMEOUT_MS,
                 model: str = GEMINI_GENERATION_MODEL):
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
    """프롬프트를 LLM에 보내고 생성된 텍스트를 반환한다."""
    if PROVIDER == "gemini":
        from auth import get_api_key  # 키가 필요한 경로라 gemini일 때만 import
        api_key = get_api_key()
        response = with_retry(
            lambda: _call_gemini(prompt, api_key, timeout_ms, model),
            max_retries=max_retries, is_retryable=is_transient_api_error, sleep_fn=sleep_fn,
        )
        usage = getattr(response, "usage_metadata", None)
        if budget is not None and usage is not None:
            budget.charge(getattr(usage, "total_token_count", 0))
        text = response.text
    else:
        text, tokens = with_retry(
            lambda: _call_claude(prompt, timeout_ms, model),
            max_retries=max_retries,
            is_retryable=lambda e: isinstance(e, ClaudeCLIError),
            sleep_fn=sleep_fn,
        )
        if budget is not None:
            budget.charge(tokens)

    log_event(
        "llm-client", "success",
        detail=f"generate 완료 (프롬프트 {len(prompt)}자, 모델 {model}, provider {PROVIDER})",
    )
    return text


def evaluate(prompt: str, **kwargs) -> str:
    """평가용 호출. generate()와 같은 경로지만 EVALUATION_MODEL을 쓴다."""
    return generate(prompt, model=EVALUATION_MODEL, **kwargs)
