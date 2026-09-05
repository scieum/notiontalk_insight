"""노션 API 공용 클라이언트 (A7, B3, B7이 공유).

표준 라이브러리만 쓴다 — 노션 API는 단순한 REST라 SDK가 필요 없고, launchd에서
도는 스크립트에 의존성을 늘리지 않는 편이 낫다.

재시도는 gemini-client의 `retry.py`를 그대로 쓴다. 그 모듈은 처음부터 프레임워크
독립적으로 짰고, 429/5xx만 재시도하고 `Retry-After` 힌트를 존중하는 규칙이
노션에도 그대로 맞는다(CLAUDE.md §5 A7 행).

토큰은 macOS Keychain 또는 `.env`에서만 읽는다. 로그·산출물에 절대 남기지 않는다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[4]))
sys.path.insert(0, str(_HERE.parents[4] / ".claude/skills/gemini-client/scripts"))

from retry import is_transient_api_error, with_retry

from lib.common.paths import ROOT

API_BASE = "https://api.notion.com/v1"
API_VERSION = "2022-06-28"  # 노션 API 버전 고정. 올릴 때는 변경점을 확인하고 올린다.
ENV_VAR_NAME = "NOTION_TOKEN"
KEYCHAIN_SERVICE_NAME = "talkinsight-notion"

REQUEST_TIMEOUT = 60


class NotionError(RuntimeError):
    """노션 API 오류. status로 재시도 여부를 판단할 수 있게 코드를 보존한다."""

    def __init__(self, status: int, body: str):
        super().__init__(f"{status} {body[:400]}")
        self.status = status
        self.code = status  # retry.http_status_of()가 읽는 속성
        self.body = body


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


def _read_keychain(service: str) -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def get_token() -> str:
    token = os.environ.get(ENV_VAR_NAME)
    if token:
        return token
    token = _read_dotenv(ROOT / ".env", ENV_VAR_NAME)
    if token:
        return token
    token = _read_keychain(KEYCHAIN_SERVICE_NAME)
    if token:
        return token
    raise RuntimeError(
        f"{ENV_VAR_NAME}을 찾을 수 없습니다. 환경변수, 프로젝트 루트 .env, 또는 "
        f"Keychain 서비스 '{KEYCHAIN_SERVICE_NAME}'에 등록하십시오.\n"
        f"  security add-generic-password -a \"$USER\" -s {KEYCHAIN_SERVICE_NAME} -w"
    )


def _once(method: str, path: str, token: str, payload: dict | None):
    url = f"{API_BASE}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Notion-Version", API_VERSION)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise NotionError(e.code, body) from None
    except urllib.error.URLError as e:
        # 네트워크 오류는 상태 코드가 없어 재시도 대상으로 분류된다.
        raise RuntimeError(f"노션 API 연결 실패: {e.reason}") from None


def request(method: str, path: str, payload: dict | None = None, max_retries: int = 3):
    """노션 API 호출. 429/5xx만 재시도하고 4xx는 즉시 올린다."""
    token = get_token()
    return with_retry(
        lambda: _once(method, path, token, payload),
        max_retries=max_retries,
        is_retryable=is_transient_api_error,
    )


def normalize_id(raw: str) -> str:
    """노션 URL이나 32자 hex를 8-4-4-4-12 UUID로 정규화한다."""
    import re

    hexes = re.findall(r"[0-9a-fA-F]{32}", raw.replace("-", ""))
    if not hexes:
        raise ValueError(f"노션 ID를 찾을 수 없습니다: {raw[:80]}")
    h = hexes[0].lower()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
