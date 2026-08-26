---
name: kakao-parser
description: DB 레코드 jsonl 또는 내보내기 txt를 공통 정규화 스키마로 변환하고, append-only 메시지 저장소에 중복 없이 병합한다 (파이프라인 A 단계 A1~A2). /inbox/db_extract 또는 /inbox/raw에 처리할 파일이 있을 때 호출한다.
---

# kakao-parser (A1~A2)

> 상태: **구현 완료 — Mac 실기기 실데이터 검증됨 (2026-08-26)**. 설계서 §2.4 A1, A2.
>
> **A1 주 경로는 `parse_csv.py`다.** Mac 카톡 26.7.0의 공식 내보내기는 txt가 아니라
> CSV(`Date,User,Message`)를 만든다 — 설계서의 txt 형식 A/B 가정이 틀렸다.
> 실데이터 26,907건(2025-01-26~2026-08-26)으로 **파싱률 100%** 확인.
> `merge.py`(A2)는 실데이터 26,907건을 처리해 26,905건 적재/진짜 중복 2건 검출,
> 재실행 시 전량 중복 제거 확인. `parse_txt.py`(형식 A/B)는 구버전·타 플랫폼
> 내보내기용으로 유지하며 합성 데이터로만 검증됐다.
> `normalize_db.py`(A1 DB 경로)는 여전히 **스텁** — A0 구현 후 실제 레코드
> 구조에 맞춰 `TYPE_CODE_MAP`·필드 매핑을 채워야 한다.

## 목적

DB 경로(jsonl)와 내보내기 경로(txt) 어느 쪽에서 왔든 동일한 공통 스키마로 정규화하고,
해시 기반 중복 제거로 `messages.sqlite`에 append한다.

## 공통 필드 (설계서 §2.4 A1)

`ts`, `nickname`, `text`, `is_system`, `links[]`, `reply_to?`, `src(db|txt)`, `native_id?`

## 처리 순서

1. **A1 정규화**: DB 레코드는 필드 매핑(타입 코드 → `is_system`/`media`, 답글 참조 보존).
   txt는 형식 감지 → 정규식 파싱.
2. **A2 병합**: 중복 키는 `native_id` 있으면 그것, 없으면 `ts+nickname+text` 해시.
   DB→txt 폴백 전환 구간은 **초 단위 ts 정규화 후 해시 비교**로 교차 중복 제거.
   신규만 삽입 후 구간 경계 산출.

## 스크립트

| 스크립트 | 역할 | 상태 |
|---------|------|------|
| `scripts/normalize_db.py` | DB jsonl → 공통 스키마 | 미구현 |
| `scripts/parse_txt.py` | 내보내기 txt → 공통 스키마 | 미구현 |
| `scripts/merge.py` | 중복 제거 후 messages.sqlite에 append | 미구현 |

## 입력/출력

- 입력: A0/A0' 산출물
- 출력: `/output/messages/parsed/<timestamp>.jsonl`, `messages.sqlite` 갱신,
  `/output/messages/period_<id>.json`

## 성공 기준 / 실패 처리

CLAUDE.md §5 표의 A1, A2 행 참조. DB 경로 매핑 실패 0건, txt 경로 파싱률 ≥99%.
신규 메시지 0건이면 스킵+로그(발행 사이클 미실행).

## 참조 문서

- `references/kakao_txt_formats.md` — 내보내기 txt 형식 (V1'에서 실물 확보 후 작성)
- `references/db_field_mapping.md` — DB 컬럼 ↔ 공통 스키마 매핑 (V1에서 작성)


## A1 경로 선택 (원천별)

| 입력 | 스크립트 | 비고 |
|------|---------|------|
| `/inbox/raw/*.csv` | **`parse_csv.py`** | Mac 카톡 26.7.0 공식 내보내기. **현재 주 경로** |
| `/inbox/raw/*.txt` | `parse_txt.py` | 형식 A(쉼표) / 형식 B(대괄호+날짜헤더). 구버전·타 플랫폼 |
| `/inbox/db_extract/*.jsonl` | `normalize_db.py` | A0(DB) 경로. **스텁** |

확장자만으로 정하지 말 것 — 메뉴 이름이 "텍스트 파일로 저장"이라 `.txt`인데 내용이
CSV일 수 있다. `kakao-export/scripts/verify_export.py`가 **내용 기반**으로 형식을
판별하므로 그 결과(`format` 필드)를 따르면 된다.

## parse_csv.py 실측 동작 (2026-08-26)

- 전체 27,189행 → 파싱 26,907건, 자리표시자 제외 282건, 미파싱 0건 → **파싱률 100%**
- 시스템 메시지 2,176건 (입퇴장). `Message == User + "님이 들어왔습니다."` **구조**로
  판정한다 — 키워드 포함으로 보면 실제 사용자 발언을 오분류한다(실측 사례 있음)
- `Date`가 빈 행(삭제·가림 자리표시자)은 버리되 **파싱률 분모에서도 제외**한다
