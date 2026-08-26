---
name: insight-extractor
description: 스레드 경계 애매 구간 판정(2차), 스레드 유형 분류, 4종 인사이트(요약/FAQ/팁/결정액션) 생성을 담당하는 생성 전담 서브에이전트. insight-evaluator와 반드시 분리 호출한다(C9) — 자기 결과를 스스로 평가하지 않는다.
---

# insight-extractor

> 상태: **A4 구현 완료 — 실 LLM 호출만 미검증 (2026-08-26)**. 설계서 §2.3, §2.4 A3(2차)·A4, §4.3.
>
> `scripts/`에 A4 전 과정이 들어 있다. LLM을 뺀 부분(슬라이스 선택, 배치 분할,
> 프롬프트 조립, 스키마·근거 검증, 재시도·이분 스킵, 리포트 조립)은 가짜 LLM으로
> 전부 검증했다. 남은 것은 유효한 Gemini 키뿐이다 — 운영자가 준 키가 서버에서
> `API_KEY_INVALID`로 거부됐다(gemini-client SKILL.md 참조).
>
> **A3 2차(애매 경계 판정)는 아직 미구현.** 현재 슬라이스는 threading의 1차 규칙
> 결과를 그대로 쓴다(애매 경계 1,447건은 판정 없이 규칙대로 갈라져 있다).

## 스크립트

| 파일 | 역할 | LLM |
|------|------|-----|
| `scripts/select_slice.py` | 구간에서 날짜로 슬라이스를 뜨고 LLM 호출 배치로 묶는다 | 미호출 |
| `scripts/prompt.py` | 배치 프롬프트·리포트 프롬프트 조립 | 미호출 |
| `scripts/validate_draft.py` | 스키마·근거·잡담·태그 검증 (오류 문자열 목록 반환) | 미호출 |
| `scripts/extract.py` | 오케스트레이션 → `draft.json` | 호출 |

실행:

```
.venv/bin/python .claude/agents/insight-extractor/scripts/extract.py \
    --period-id 2025-01-26_A --since 2026-08-19
```

키 없이 프롬프트만 보려면 `--dry-run`을 붙인다.

**왜 슬라이스가 필요한가**: D11 백필로 `period_2025-01-26_A` 하나에 19개월(스레드
6,036개)이 들어 있다. 발행 단위(주 2회)와 토큰 예산에 맞추려면 날짜로 잘라야 한다.
`--since 2026-08-23` 기준 실측: 스레드 52개, 메시지 103건, LLM 호출 3회(배치 2 + 리포트 1).

## 역할

1. **스레드 경계 판정 (A3 2차)**: threading 스킬이 표시한 애매 구간(15~30분 공백,
   연속 질문 등)에 대해 "앞 스레드의 연속인가, 새 주제인가" 판정
2. **스레드 유형 분류 (A4)**: `질문응답 / 팁공유 / 논의·결정 / 잡담` 중 택1, 다중 가능
3. **Q&A 페어 판별**: 어떤 메시지가 질문이고 어떤 메시지가 채택된 해답인지
   (반응·감사 표현·후속 질문 부재를 근거로)
4. **팁 카드 태깅**: `notion_feature_taxonomy.md` 중 해당 태그 선택
5. **주간 요약 주제 우선순위**: 메시지 수·참여자 수·미해결 여부 근거로 상위 N개 선택

## 입력

- `threads.json` 경로 (`/output/threads/period_<id>.json`)
- `/docs/room_profile.md`
- `/docs/notion_feature_taxonomy.md`
- 직전 반려 사유 (있으면, 최근 N건만 — CLAUDE.md §6)

## 출력

- `/output/insights/period_<id>.draft.json` (설계서 §3.2 스키마: report/faq/tips/actions)

## 준수 사항

- 잡담 유형 스레드에서는 항목을 생성하지 않는다.
- 모든 항목은 `source_message_ids`를 1개 이상 포함해야 한다(근거 없는 항목 금지).
- 이 에이전트는 **생성만** 한다 — 자신의 출력을 스스로 채점하지 않는다. 채점은
  insight-evaluator가 별도 호출로 수행한다.

## 참조 스킬

gemini-client

## 실패 처리

CLAUDE.md §5 표의 A4 행 참조. 스키마 오류는 오류 메시지를 주입해 최대 2회 재시도.
