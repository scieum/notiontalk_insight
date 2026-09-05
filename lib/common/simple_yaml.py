"""설정 파일용 YAML 최소 파서. 표준 라이브러리만 쓴다.

이 프로젝트의 설정 파일(`consent.yaml`, `notion_publish.yaml`)은 사람이 손으로
고치는 물건이라 JSON보다 YAML이 읽기 좋다. 그렇다고 PyYAML을 넣으면 시스템
python으로 도는 스크립트 전부가 의존성을 얻는다 — 지금은 `google-genai`만
`.venv`에 두고 나머지는 의존성 없이 도는 구조다.

그래서 **필요한 문법만** 지원한다:

    key: value            문자열/숫자/true/false/null
    key:                  한 단계 중첩 (2칸 들여쓰기)
      sub: value
    key:                  문자열 리스트
      - item
    # 주석                 줄 전체 또는 값 뒤

지원하지 않는 것: 여러 단계 중첩, 인라인 배열/객체(`[a, b]`, `{a: b}`),
여러 줄 문자열(`|`, `>`), 앵커·별칭. 설정이 그만큼 복잡해지면 그때 PyYAML을
`.venv`에 넣고 이 모듈을 버리는 게 맞다.
"""

from __future__ import annotations

from pathlib import Path


class YamlError(ValueError):
    pass


def _scalar(raw: str):
    text = raw.strip()
    if not text:
        return None
    # 따옴표 안에서는 #을 주석으로 보지 않는다
    if text[0] in "\"'":
        quote = text[0]
        end = text.find(quote, 1)
        if end == -1:
            raise YamlError(f"따옴표가 닫히지 않았습니다: {raw[:60]}")
        return text[1:end]
    text = text.split("#", 1)[0].strip()
    if text in ("null", "~", ""):
        return None
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def loads(text: str) -> dict:
    root: dict = {}
    current_key: str | None = None   # 중첩/리스트를 모으는 중인 최상위 키
    container = None                 # dict 또는 list

    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()

        if indent == 0:
            if ":" not in stripped:
                raise YamlError(f"{lineno}행: 'key: value' 형태가 아닙니다 — {stripped[:60]}")
            key, _, rest = stripped.partition(":")
            key = key.strip()
            if rest.strip() and not rest.strip().startswith("#"):
                root[key] = _scalar(rest)
                current_key, container = None, None
            else:
                # 값이 비었으면 다음 줄들이 중첩이거나 리스트다. 아무것도 안 오면 None.
                root[key] = None
                current_key, container = key, None
            continue

        if current_key is None:
            raise YamlError(f"{lineno}행: 들여쓴 줄인데 상위 키가 없습니다 — {stripped[:60]}")

        if stripped.startswith("- "):
            if container is None:
                container = []
                root[current_key] = container
            if not isinstance(container, list):
                raise YamlError(f"{lineno}행: 이미 중첩 객체인데 리스트 항목이 왔습니다")
            container.append(_scalar(stripped[2:]))
            continue

        if ":" not in stripped:
            raise YamlError(f"{lineno}행: 'key: value' 형태가 아닙니다 — {stripped[:60]}")
        if container is None:
            container = {}
            root[current_key] = container
        if not isinstance(container, dict):
            raise YamlError(f"{lineno}행: 이미 리스트인데 키가 왔습니다")
        sub_key, _, sub_rest = stripped.partition(":")
        container[sub_key.strip()] = _scalar(sub_rest)

    return root


def load(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"설정 파일이 없습니다: {p}")
    return loads(p.read_text(encoding="utf-8"))
