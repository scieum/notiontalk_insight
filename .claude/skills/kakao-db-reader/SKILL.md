---
name: kakao-db-reader
description: katok CLI에 위임해 카카오톡 Mac 로컬 아카이브에서 대상 오픈채팅방의 신규 메시지만 추출한다 (파이프라인 A 단계 A0). 수집 사이클 시작 시 항상 먼저 호출한다. docs/risk_acceptance.md가 '승인'이 아니거나 katok이 없으면 실행하지 말고 kakao-export로 간다. katok send는 절대 호출하지 않는다(C1).
---

# kakao-db-reader (A0)

> 상태: **katok 위임 방식으로 구현, 실기기 검증 대기 (2026-08-27)**.
>
> **직접 복호화 계획을 폐기했다.** 논문·gist를 보고 복호화 코드를 직접 짜는 대신,
> [katok CLI](https://github.com/NomaDamas/katok)(MIT, Apple Silicon 전용)에 위임한다.
> 근거와 리스크 재평가는 `docs/db_access_reference.md`의 "katok CLI 경로" 절에 있다.
>
> 그래서 원래 계획했던 `snapshot_db.py` / `fingerprint.py` / `decrypt.py` /
> `query_room.py` / `cleanup.py` 5종은 만들지 않는다. 스냅샷·지문·복호화·임시파일
> 정리를 전부 katok이 대신하므로 우리 쪽에 대응물이 없다.
> `references/known_fingerprints.json`도 이 경로에서는 쓰이지 않는다.
>
> 구현체는 `scripts/katok_extract.py` 하나다.

## 목적

카카오톡 Mac 앱의 로컬 기록에서 대상 오픈채팅방의 신규 메시지(직전 사이클 이후)만 읽어
`/inbox/db_extract/` 아래로 저장한다. 원본 DB에는 절대 쓰지 않는다 — 읽기도 katok이
하고, 우리는 그 결과만 받는다.

## 실행 전 필수 확인 (CLAUDE.md §2, §8)

1. `/docs/risk_acceptance.md`의 `상태`가 `승인`인지 확인. 아니면 실행하지 않고 운영자에게 요청.
   `katok_extract.py`의 `check_risk_approval()`이 매 실행마다 강제한다.
2. ~~지문 목록 확인~~ — katok 경로에는 해당 없다(katok이 스키마 차이를 흡수한다).

## 절대 호출하지 않는 katok 명령

**`katok send`** — 카카오톡으로 메시지를 보낸다. 제약 C1이 "오픈채팅방 안에서
발언하는 비공식 클라이언트 사용 금지"로 금지한다. `_run()`이 허용 명령
화이트리스트(`doctor` / `source chats` / `sync` / `transcript`)로 실행 자체를 막고,
`send`는 별도 금지 목록에도 넣어 두 겹으로 차단한다. 회귀 테스트로 확인했다.

`index` / `search` / `chunk` 는 katok 자체 검색용이라 우리 파이프라인에 불필요해
역시 허용하지 않는다 — 우리는 kb.sqlite를 따로 만든다.

## 실행

```bash
python3 .claude/skills/kakao-db-reader/scripts/katok_extract.py \
    --since 2026-08-26T00:00:00+09:00 --probe
```

`--probe`는 katok 원본 출력을 그대로 보여준다. 출력 구조(방 레코드의 id 필드명,
`transcript --json`의 반환 형태)가 문서에 없어 첫 실행에서 확인해야 한다.

## 선행 조건

- Apple Silicon macOS + `brew install katok`
- 터미널(자동 실행 시에는 `katok` 실행 파일 자체)에 **전체 디스크 접근** 권한
- 대상 방이 카카오톡 앱에서 한 번 이상 열려 로컬 DB에 동기화돼 있을 것

## 처리 순서 (설계서 §2.4 A0)

1. 카톡 앱 버전 확인
2. DB 파일을 임시 경로로 **복사** (원본 미접촉)
3. 스키마 지문 계산 → `known_fingerprints.json`과 대조. 불일치 시 재시도 없이 즉시 폴백(A0')
4. 지문 일치 시에만 복호화
5. 대상 방 ID로 필터 + `ts > last_ts` 조회
6. 원시 레코드(ts, 발신자, 본문, 타입, 답글 참조, 메시지 ID)를 jsonl로 저장
7. 임시 복사본 **즉시 삭제**

## 스크립트

| 스크립트 | 역할 | 상태 |
|---------|------|------|
| `scripts/snapshot_db.py` | DB 파일을 임시 경로로 복사 | 미구현 |
| `scripts/fingerprint.py` | 스키마 지문 계산·대조 | 미구현 |
| `scripts/decrypt.py` | 복호화 | 미구현 |
| `scripts/query_room.py` | 대상 방 필터 + 증분 조회 | 미구현 |
| `scripts/cleanup.py` | 임시 복사본 삭제 | 미구현 |

## 입력/출력

- 입력: 없음(스케줄 트리거), 직전 사이클 마지막 `ts`
- 출력: `/inbox/db_extract/<timestamp>.jsonl` (대상 방 신규 메시지만)

## 성공 기준 / 실패 처리

CLAUDE.md §5 표의 A0 행 참조. 대상 방 최근 메시지 100건을 앱 UI와 대조해 100% 일치,
임시 복사본 잔존 0, 다른 방 데이터 미보존이 V1 완료 기준(설계서 §5).

## 참조 문서

- `references/known_fingerprints.json`
- [db_access_reference.md](../../../docs/db_access_reference.md) — O1 조사 결과 (미작성)
