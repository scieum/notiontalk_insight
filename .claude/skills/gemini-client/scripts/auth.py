"""Gemini API 키 조회 (설계서 C4: "macOS Keychain 또는 .env(git 제외)").

우선순위: 1) 환경변수 2) 프로젝트 루트 .env 3) (macOS에서만) Keychain.
어디서 찾았든 값 자체를 로그에 남기지 않는다(R4와 동일한 원칙 — 여기선
API 키지만 취급 방침은 동일하게 적용).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from lib.common.paths import ROOT

ENV_VAR_NAME = "GEMINI_API_KEY"
KEYCHAIN_SERVICE_NAME = "talkinsight-gemini"  # docs/db_access_reference.md 등과 이름 체계 통일


def _read_dotenv(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            return value.strip().strip("'\"")
    return None


def _read_macos_keychain(service: str) -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def get_api_key() -> str:
    key = os.environ.get(ENV_VAR_NAME)
    if key:
        return key

    key = _read_dotenv(ROOT / ".env", ENV_VAR_NAME)
    if key:
        return key

    key = _read_macos_keychain(KEYCHAIN_SERVICE_NAME)
    if key:
        return key

    raise RuntimeError(
        f"{ENV_VAR_NAME}를 찾을 수 없습니다. 환경변수, 프로젝트 루트 .env, "
        f"또는 (Mac에서) Keychain 서비스 '{KEYCHAIN_SERVICE_NAME}'에 등록하세요."
    )
