---
name: pii-guard
description: 검증 통과한 인사이트 항목에서 전화번호·이메일·주민번호 등 PII를 마스킹하고, consent.yaml 모드에 따라 닉네임을 유지하거나 익명화한다 (파이프라인 A 단계 A6). 발행 직전 항상 호출한다.
---

# pii-guard (A6)

> 상태: **구현·테스트 완료 — Mac 실기기 재검증됨 (2026-08-26)**. 설계서 §2.4 A6.
> 합성 인사이트로 전화번호·이메일 마스킹, `anon` 모드 닉네임 일관 치환
> (민수→참여자A, 지영→참여자B) 동작 확인.
> A4/A5(인사이트 생성·검증)가 아직 없어 실제 운영 데이터로는 미검증.

## 목적

노션에 공개 발행되기 직전 마지막 방어선. PII 패턴은 동의 모드와 무관하게 항상
마스킹하고, 닉네임 처리는 `/docs/consent.yaml`의 `mode`를 따른다.

## 처리 규칙

- 전화번호·이메일·주민번호 패턴·노션 공유 링크의 토큰 파라미터: **항상** 마스킹
- `consent.yaml: mode: anon` → 닉네임을 구간 내 일관된 가명(`참여자A`)으로 치환
- `consent.yaml: mode: named` → 닉네임 유지
- 시스템 메시지의 입장/퇴장 닉네임: **항상** 제거

## 스크립트

| 스크립트 | 역할 | 상태 |
|---------|------|------|
| `scripts/mask.py` | PII 정규식 마스킹 (`mask_text`, `rescan`) | 구현됨 |
| `scripts/anonymize.py` | 닉네임 가명 치환 + 시스템 입장/퇴장 문구 제거 | 구현됨 |
| `scripts/apply.py` | 오케스트레이터: 위 둘을 인사이트 JSON 전체에 적용, 항목별 재검사, `final.json` 작성 | 구현됨 |

`apply.py <draft_or_eval.json>` 형태로 실행한다 (실제 호출은 메인 오케스트레이터가 함).

## 입력/출력

- 입력: A5 검증 통과 항목 (`period_<id>.eval.json` 중 통과분)
- 출력: `/output/insights/period_<id>.final.json`

## 성공 기준 / 실패 처리

CLAUDE.md §5 표의 A6 행 참조. PII 정규식 재검사 0건 매치, anon 모드 시 원본 닉네임
0건 잔존. 재검사 매치 시 해당 항목 발행 보류 + 에스컬레이션.

## 참조 문서

- `references/pii_patterns.md` — 마스킹 정규식 목록
