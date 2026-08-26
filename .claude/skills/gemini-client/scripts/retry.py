"""범용 재시도(지수 백오프) 헬퍼. gemini-client 뿐 아니라 API 호출이 필요한
다른 스킬(notion-publish 등)에서도 재사용할 수 있도록 프레임워크 독립적으로 짠다.

sleep_fn을 주입할 수 있게 해서, 테스트에서는 실제로 기다리지 않고도 백오프
횟수·시간 계산 로직을 검증할 수 있다.

재시도 대상 판정은 `is_retryable` 술어로 주입한다. CLAUDE.md §5(A7 행)가 정한
"429/5xx만 재시도"를 그대로 구현한 것이 `is_transient_api_error`다 — 400/401/403
같은 영구 에러를 재시도하면 백오프 시간만 버리고 결과는 같다.
"""

from __future__ import annotations

import re
import time
from typing import Callable, TypeVar

T = TypeVar("T")

_LEADING_STATUS_RE = re.compile(r"^\s*(\d{3})\b")
# 429 응답의 RetryInfo.retryDelay("33s") 또는 "Please retry in 33.3s" 힌트.
_RETRY_DELAY_RE = re.compile(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'|retry in (\d+(?:\.\d+)?)s")


class RetryExhausted(Exception):
    def __init__(self, attempts: int, last_error: Exception):
        super().__init__(f"{attempts}번 재시도 후 실패: {last_error!r}")
        self.attempts = attempts
        self.last_error = last_error


def http_status_of(exc: Exception) -> int | None:
    """예외에서 HTTP 상태 코드를 추출한다. 못 찾으면 None.

    SDK마다 상태 코드를 담는 속성이 달라(google-genai는 `.code`, httpx/requests
    계열은 `.response.status_code`) 덕 타이핑으로 훑는다.
    """
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value

    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value

    match = _LEADING_STATUS_RE.match(str(exc))
    if match:
        value = int(match.group(1))
        if 100 <= value <= 599:
            return value
    return None


def retry_after_seconds(exc: Exception) -> float | None:
    """서버가 알려준 재시도 대기 시간. 없으면 None.

    지수 백오프(2/4/8초)는 서버가 "33초 뒤에 다시 오라"고 할 때 턱없이 짧다.
    힌트가 있으면 그쪽을 따른다.
    """
    # 한 응답에 힌트가 여러 번 나온다("Please retry in 33.3s" + "'retryDelay': '33s'").
    # 값이 미묘하게 다르므로 가장 큰 값을 쓴다 — 짧게 잡아 또 429를 맞는 것보다 낫다.
    hints = [float(a or b) for a, b in _RETRY_DELAY_RE.findall(str(exc))]
    return max(hints) if hints else None


def is_transient_api_error(exc: Exception) -> bool:
    """429·5xx는 재시도, 그 외 4xx는 즉시 실패(CLAUDE.md §5).

    상태 코드를 못 찾으면(네트워크 끊김·타임아웃 등) 재시도 대상으로 본다.
    """
    status = http_status_of(exc)
    if status is None:
        return True
    return status == 429 or status >= 500


def with_retry(
    fn: Callable[[], T],
    max_retries: int = 3,
    backoff_base_seconds: float = 2.0,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    is_retryable: Callable[[Exception], bool] | None = None,
    max_wait_seconds: float = 90.0,
    on_retry: Callable[[int, float, Exception], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    """fn()을 실행하고, 재시도 대상 예외가 나면 지수 백오프 후 재시도한다.

    재시도 대상 판정은 두 단계다: 먼저 `retryable_exceptions` 타입에 속해야 하고,
    `is_retryable`이 주어졌으면 그것이 True를 반환해야 한다. `is_retryable`이
    False를 반환하면 재시도하지 않고 **원래 예외를 그대로** 올린다(호출자가
    RetryExhausted로 감싸인 영구 에러를 다시 풀어볼 필요가 없게 한다).

    max_retries번 재시도까지 모두 실패하면 RetryExhausted를 던진다(원래 예외를
    last_error로 보존). 총 시도 횟수는 max_retries + 1(최초 시도 포함)이다.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except retryable_exceptions as e:
            if is_retryable is not None and not is_retryable(e):
                raise
            attempt += 1
            if attempt > max_retries:
                raise RetryExhausted(attempt - 1, e) from e
            wait = backoff_base_seconds * (2 ** (attempt - 1))
            hinted = retry_after_seconds(e)
            if hinted is not None:
                # 서버가 준 대기 시간을 존중하되, 무한정 기다리지는 않는다.
                wait = min(max(wait, hinted + 1.0), max_wait_seconds)
            if on_retry:
                on_retry(attempt, wait, e)
            sleep_fn(wait)
