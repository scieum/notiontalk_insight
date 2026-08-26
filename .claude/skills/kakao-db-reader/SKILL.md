---
name: kakao-db-reader
description: 카카오톡 Mac 로컬 DB를 복사·지문검증·복호화해 대상 오픈채팅방의 신규 메시지만 추출한다 (파이프라인 A 단계 A0). 수집 사이클 시작 시 항상 먼저 호출한다. known_fingerprints.json이 비어 있으면 이 스킬을 호출하지 말고 kakao-export로 간다.
---

# kakao-db-reader (A0)

> 상태: **미구현 (V1 예정)**. 설계서 §2.4 A0, §6 O1/O1' 선행 필요.

## 목적

카카오톡 Mac 앱의 로컬 DB에서 대상 오픈채팅방의 신규 메시지(직전 사이클 이후)만 읽어
`/inbox/db_extract/<timestamp>.jsonl`로 저장한다. 원본 DB에는 절대 쓰지 않는다.

## 실행 전 필수 확인 (CLAUDE.md §2, §8)

1. `/docs/risk_acceptance.md`의 `상태`가 `승인`인지 확인. 아니면 실행하지 않고 운영자에게 요청.
2. `references/known_fingerprints.json`의 `fingerprints`가 비어 있으면 실행하지 않고 kakao-export로 전환.

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
