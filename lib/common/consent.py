"""docs/consent.yaml에서 동의 모드를 읽는 최소 파서.

consent.yaml은 평평한 key: value 구조만 쓰므로(설계서 §1.4, §1.7) 의존성 없이
직접 파싱한다. 구조가 더 복잡해지면(리스트/중첩) 그때 PyYAML 도입을 검토한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from .paths import DOCS_DIR

ConsentMode = Literal["named", "anon"]

CONSENT_PATH = DOCS_DIR / "consent.yaml"


def get_consent_mode(path: Path = CONSENT_PATH) -> ConsentMode:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} 가 없습니다. G-C 게이트를 거치기 전까지는 이 파일이 anon 모드로 존재해야 합니다."
        )
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped.startswith("mode:"):
            value = stripped.split(":", 1)[1].strip().strip("'\"")
            if value not in ("named", "anon"):
                raise ValueError(f"{path}의 mode 값이 올바르지 않습니다: {value!r}")
            return value  # type: ignore[return-value]
    raise ValueError(f"{path}에서 mode 필드를 찾지 못했습니다")
