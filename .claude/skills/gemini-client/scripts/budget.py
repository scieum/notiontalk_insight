"""구간당·질문당 토큰 예산 상한 강제 (설계서 C4, §6 O6).

O6가 아직 미해결이라(정확한 상한값 미정) 기본값은 무제한(None)이다. 상한이
정해지면 이 값을 넘기는 쪽(insight-extractor/answer-writer 호출부)에서
TokenBudget(max_tokens=...)로 명시한다.
"""

from __future__ import annotations


class BudgetExceededError(Exception):
    def __init__(self, used: int, max_tokens: int):
        super().__init__(f"토큰 예산 초과: {used}/{max_tokens}")
        self.used = used
        self.max_tokens = max_tokens


class TokenBudget:
    def __init__(self, max_tokens: int | None = None):
        self.max_tokens = max_tokens
        self.used = 0

    def charge(self, n: int) -> None:
        self.used += n
        if self.max_tokens is not None and self.used > self.max_tokens:
            raise BudgetExceededError(self.used, self.max_tokens)

    def remaining(self) -> int | None:
        if self.max_tokens is None:
            return None
        return max(0, self.max_tokens - self.used)
