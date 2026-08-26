# Mac 이전 체크리스트 (mac_handoff_checklist.md)

> Windows에서 여기까지 만들고 검증한 뒤, Mac에서 이어받을 때 보는 문서.
> 새 Claude Code 세션(이 대화를 기억 못 하는)이 이 문서만 보고도 정확히 뭘
> 먼저 해야 하는지 알 수 있도록 썼다. Mac에서 세션을 열면 이 파일을 먼저
> 읽게 하거나, "docs/mac_handoff_checklist.md 보고 이어서 진행해줘"라고 말하면 된다.

## 0. 프로젝트를 Mac으로 가져오기

- 이 폴더 전체를 git으로 동기화하거나(권장 — 지금 `git init`만 된 상태, 원격
  저장소는 아직 없음) 직접 복사한다.
- `.env`, `output/`, `inbox/`의 실제 내용물은 `.gitignore`로 제외되어 있어
  git으로 옮기면 넘어오지 않는다 — 의도된 것이다(PII 우려, C2). `.gitkeep`만
  넘어오므로 폴더 구조는 유지된다.
- Mac에 `python3` 있는지 확인 (`python3 --version`). 없으면 `brew install python3`.
  지금까지 모든 스크립트는 **표준 라이브러리만** 써서 pip install 없이 그대로 돈다
  (**2026-08-26 갱신**: V3 진입으로 `google-genai` 1.47.0을 프로젝트 `.venv`에
  설치했다. gemini-client만 `.venv/bin/python`으로 실행하고 나머지 스킬은
  여전히 시스템 python3로 돈다. cp39 휠이 있어 homebrew python은 불필요.)

> **[2026-08-26 Mac 세션 실측] 이 항목은 이미 처리됐다.**
> 이 Mac의 `python3`는 시스템 기본 **3.9.6**(`/usr/bin/python3`, homebrew python 없음)인데,
> 코드는 Windows에서 **3.12** 기준으로 작성돼 있어 `X | None` 어노테이션(PEP 604)이
> 런타임에 `TypeError`를 내며 전 스크립트가 죽었다. 21개 모듈 전부에
> `from __future__ import annotations`를 넣어 해결했다(어노테이션이 지연 평가되어
> 3.9에서도 동작). `|` 사용처 14곳이 **전부 어노테이션 자리**임을 AST로 확인했으므로
> 이 한 줄로 완결이며, 별도 인터프리터 설치는 불필요하다.
> 앞으로 새 .py를 추가할 때도 **첫 import로 `from __future__ import annotations`를 넣을 것**.
> (참고: Python 3.9는 2025-10 EOL. V3에서 `google-genai` 설치 시 최소 버전 요구를
> 확인하고, 3.9를 못 쓰면 그때 homebrew python 도입을 결정한다.)

## 1. 지금까지 상태 요약

| 스킬 | 상태 |
|------|------|
| `kakao-parser` (A1 txt, A2) | 구현·테스트 완료. `normalize_db.py`(A1 DB 경로)만 스텁 |
| `threading` (A3 1차) | 구현·테스트 완료 |
| `pii-guard` (A6) | 구현·테스트 완료 |
| `gemini-client` | 재시도/예산/키조회 구현·테스트 완료. 실 API 호출부 미검증(키 없어서) |
| `kb-search` (B4) | 검색·리랭킹 로직 구현·테스트 완료(가짜 벡터로). 실제 임베딩 연동은 미검증 |
| `kakao-db-reader` (A0) | **미구현.** 이 문서의 목적 |
| `kakao-export` (A0') | **미구현** |
| `kb-builder` (A8/B1/B2) | 미구현 |
| `notion-publish` (A7/B3/B7) | 미구현 |
| 4개 서브에이전트 | AGENT.md 골격만, 실제 프롬프트/로직 없음 |

각 스킬의 정확한 상태는 `.claude/skills/<이름>/SKILL.md` 맨 위에 최신으로 적혀 있다 — 이
표보다 SKILL.md를 신뢰할 것(이 문서가 시간이 지나며 갱신을 놓칠 수 있음).

## 2. Mac에서 가장 먼저 할 일 순서 (V1 착수)

CLAUDE.md와 설계서 §5가 정한 순서다. **건너뛰지 말고 순서대로.**

### 2.1 리스크 승인 (사람이 직접)

`docs/risk_acceptance.md`를 열어서 R1~R5 리스크를 읽고, 실제로 진행할 것인지
판단한다. 진행하기로 했으면 **운영자(사용자)가 직접** 파일 맨 위 `상태`를
`승인`으로 바꾸고 승인자/일자를 채운다. **Claude가 대신 이 값을 바꾸면 안
된다** — CLAUDE.md §8에 이미 이렇게 명시돼 있으니 Claude가 자동으로 지킬 것이다.

### 2.2 실기기 정보 채우기

`docs/db_access_reference.md`를 연다.

> **이 파일은 저장소에 없다.** 공개 저장소에 리버스 엔지니어링 자료를 배포하지
> 않으려고 `.gitignore`로 제외했다(2026-08-26 운영자 결정, 설계서 §1.6 R1).
> 다른 기기에서 이어받는다면 이 파일만 따로 옮겨야 한다.

지금 들어있는 내용은 **전부 원격 조사
(2026-08-26, Windows 세션) 결과이고 미검증**이다:

- Mac 카카오톡 앱 버전, macOS 버전을 확인해 "조사 대상 카카오톡 버전" 섹션을 채운다.
- DB 파일이 실제로 그 경로(`~/Library/Containers/com.kakao.KakaoTalkMac/...`)에 있는지 확인한다.

### 2.3 참조 구현 검증

문서에 정리해 둔 두 참조를 실기기로 검증한다:

1. **1순위**: 박범준·이상진(2023) 논문 "macOS용 카카오톡 데이터베이스 복호화
   방안" — 아직 전문을 못 구했다. RISS/DBpia/학교 도서관 등에서 전문을 구해서
   정확한 키 유도 알고리즘과 userId 효율적 전수조사 방법을 확인하는 게 가장
   신뢰도 높은 시작점이다.
2. **2순위**: GitHub gist(blluv, 2026-03-04 업데이트)의 코드를 참고해 직접
   시도해본다. `IOPlatformUUID`는 문서에 있는 `ioreg` 명령으로 뽑고, `userId`는
   작은 정수부터 순차 대입 + `PRAGMA integrity_check` 통과 여부로 찾는다.
   **라이선스가 명시 안 되어 있으니 코드를 그대로 복사하지 말고 알고리즘만
   참고해서 새로 짤 것** (R3).

검증되면 `db_access_reference.md`의 남은 빈칸(암호화 방식 확정, 메시지
테이블/컬럼, 키 유도 절차)을 실측값으로 채우고 "O1 완료" 체크박스를 체크한다.

### 2.4 대상 오픈채팅방 식별 (O1')

DB 안에서 어떤 값이 "노션톡 커뮤니티" 방을 가리키는지 찾는다. 여러 방이 섞여
있을 것이므로, 방 이름/멤버 수 등으로 특정한다. `db_access_reference.md`의
"대상 오픈채팅방 식별자" 섹션을 채운다.

### 2.5 kakao-db-reader 구현

검증된 내용으로 `.claude/skills/kakao-db-reader/scripts/`의 5개 스크립트를 채운다:
`snapshot_db.py`(복사) → `fingerprint.py`(지문 계산, `references/known_fingerprints.json`에
첫 지문 등록) → `decrypt.py` → `query_room.py`(대상 방 필터 + 증분 조회) →
`cleanup.py`(임시 복사본 삭제, **반드시** 매 실행 끝에).

이어서 `.claude/skills/kakao-parser/scripts/normalize_db.py`의 `TYPE_CODE_MAP`과
필드 매핑을 실제 DB 레코드 구조에 맞게 채운다 — `merge.py`는 이미 완성되어
있으므로 `normalize_db.py`만 채우면 A0→A1→A2가 바로 이어진다.

### 2.6 V1 완료 기준 (설계서 §5)

- 대상 방 최근 메시지 100건을 앱 UI와 대조해 **100% 일치**
- 임시 복사본 잔존 **0**
- 다른 방 데이터가 어떤 파일에도 **없음** (R5)

이 세 가지를 다 통과해야 V1 완료다. 그 다음이 V1'(kakao-export 폴백,
JXA) — 착수 전에 §6 O1'''(오픈채팅방이 애초에 "대화 내보내기"를 지원하는지)를
먼저 확인할 것.

## 3. 이미 있는 것 재사용

- `lib/common/*` (로그, 에스컬레이션, 스키마, consent 파서, kb 스키마) 그대로 씀
- `kakao-parser`, `threading`, `pii-guard`는 완성돼 있으니 A0만 뚫리면 그대로 이어짐
- `scripts/run_cycle.py`도 그대로 쓰되, A0/A0' 구현되는 대로 그 단계의
  `log_event(stage, "skip", ...)` 자리표시자를 실제 스크립트 호출로 바꿔나갈 것
