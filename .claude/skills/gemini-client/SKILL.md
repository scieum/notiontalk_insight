---
name: gemini-client
description: Gemini LLM·임베딩 호출을 위한 공용 래퍼(재시도, 토큰 예산, 로깅). 모든 서브에이전트(insight-extractor, insight-evaluator, answer-writer, answer-evaluator)와 kb-builder/kb-search가 LLM 또는 임베딩을 호출할 때 이 스킬을 거친다.
---

# gemini-client (공용 LLM 호출 래퍼)

> 상태: **부분 구현**. 재시도(`retry.py`)·예산(`budget.py`)·키 조회(`auth.py`)는
> 단위 테스트로 검증 완료.
>
> **[2026-08-26 Mac 세션]**
> - `google-genai` 1.47.0을 프로젝트 `.venv`(python 3.9.6)에 설치했다. cp39 휠이
>   정상 제공되어 homebrew python 도입은 불필요했다. **이 스킬을 실행할 때는
>   시스템 `python3`가 아니라 `.venv/bin/python`을 써야 한다** — 다른 스킬들은
>   여전히 표준 라이브러리만 쓰므로 시스템 python으로 돈다.
> - `retry.py`에 재시도 대상 판정(`is_retryable` 술어, `is_transient_api_error`,
>   `http_status_of`)을 추가했다. 기존 기본값은 `Exception` 전부를 재시도해서
>   400/401/403 같은 **영구 에러도 3회 재시도**했다(CLAUDE.md §5의 "429/5xx만
>   재시도" 규칙 위반). 이제 4xx는 즉시 원래 예외를 올린다 — 실측으로 같은
>   실패가 28초에서 1.3초로 줄었다. 429/5xx 백오프(2/4/8초)와
>   `is_retryable` 미지정 시 하위 호환은 그대로다.
> - **실 API 호출부는 여전히 미검증**: 운영자가 제공한 키가 서버에서
>   `API_KEY_INVALID`(400)로 거부됐다. auth→SDK→요청 전송까지는 확인됐고
>   응답 파싱(`response.text`, `response.embeddings[0].values`)·예산 차감
>   경로만 유효한 키를 기다리고 있다.

## 목적

Gemini API 호출(생성 `gemini-2.0-flash`, 임베딩 `text-embedding-004`)을 한 곳에서
관리한다: 재시도, 토큰 예산 상한, 요청/응답 로깅(원문 그대로 로깅하지 않음 — 길이·상태만).

## 책임

- API 키는 macOS Keychain 또는 `.env`(git 제외)에서만 읽는다. 로그에 키를 남기지 않는다.
- 배치 처리 시 구간당·질문당 토큰 예산 상한을 강제한다(설계서 §6 O6 — 상한 값은 운영자 확정 필요).
- 재시도·백오프 정책을 통일한다.
- 생성 호출과 평가 호출은 항상 별도 호출로 이루어진다(C9) — 이 스킬은 그 분리를 강제하지 않고
  **호출하는 서브에이전트가** 분리 책임을 진다. 이 스킬은 호출 메커니즘만 제공한다.

## 스크립트

| 스크립트 | 역할 | 상태 |
|---------|------|------|
| `scripts/retry.py` | 범용 지수 백오프 재시도 헬퍼 (`with_retry`) | 구현·테스트됨 |
| `scripts/budget.py` | 토큰 예산 상한 강제 (`TokenBudget`) | 구현·테스트됨 |
| `scripts/auth.py` | API 키 조회 (env → .env → Mac Keychain) | 구현·테스트됨 |
| `scripts/llm.py` | 생성 호출 래퍼 (`generate()`) | 재시도/로깅 부분 구현, 실 API 호출부 미검증 |
| `scripts/embed_client.py` | 임베딩 호출 래퍼 (`embed()`) | 재시도/로깅 부분 구현, 실 API 호출부 미검증 |

API 키가 필요한 부분(`_call_gemini`, `_call_gemini_embed`)은 `google-genai` 패키지를
지연 임포트한다 — 패키지가 설치되어 있지 않아도 나머지 로직(재시도·예산·로깅)은
임포트·테스트 가능하다.

## 성공 기준 / 실패 처리

호출부(A4/A5/B5/B6/A8/B2)의 개별 실패 처리 규칙을 따른다(CLAUDE.md §5). 이 스킬
자체는 재시도 후에도 실패하면 예외를 그대로 호출부에 전파한다.
