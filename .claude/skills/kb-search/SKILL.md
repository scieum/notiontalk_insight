---
name: kb-search
description: kb.sqlite에서 질문 임베딩과 벡터 유사도가 높은 청크를 원천별 리랭킹해 상위 K개를 반환한다 (파이프라인 B 단계 B4). A5 검증 시 기존 FAQ/팁과의 중복 유사도 사전 계산에도 쓰인다.
---

# kb-search (B4, A5 보조)

> 상태: **검색·리랭킹 로직 구현·테스트 완료** (가짜 벡터로 코사인 유사도·원천별
> 캡·superseded 제외·근거부족(<0.6) 판정 전부 검증됨). 질의 임베딩 생성 자체는
> gemini-client(embed_client.embed)에 의존하며 그쪽은 API 키 없이 미검증.
> kb.sqlite를 실제로 채우는 kb-builder(A8/B1/B2)도 아직 미구현이라, 지금은
> 빈 DB가 아니라 사람이 직접 넣은 청크에 대해서만 동작 확인된 상태.

## 목적

질문에 대한 근거 청크를 찾는 검색 스킬. 로컬 임베딩 검색(SQLite + 벡터) +
메타데이터 필터 하이브리드(D10).

## 처리 순서 (B4)

1. 질문 임베딩
2. 벡터 유사도 상위 20 조회
3. 원천별 리랭킹: `chat` 최대 4, `notion_help` 최대 4, `site` 최대 2, `superseded` 제외
4. 최종 K ≤ 10

## A5 보조

인사이트 항목과 기존 FAQ/팁 간 유사도를 사전 계산해 `insight-evaluator`에 제공
(중복 판정 임계 0.9는 평가 단계 규칙, 계산 자체는 이 스킬).

## 스크립트

| 스크립트 | 역할 | 상태 |
|---------|------|------|
| `scripts/search.py` | `cosine_similarity`, `search`, `rerank_by_source`, `search_and_rerank` | 구현·테스트됨 |

kb.sqlite 스키마·임베딩 인코딩(struct 기반, numpy 불필요)은 `lib/common/kb_schema.py`에
공용으로 정의해 kb-builder와 공유한다.

## 입력/출력

- 입력: 질문 텍스트 (또는 A5용 후보 항목 텍스트)
- 출력: `/output/questions/<id>.evidence.json`

## 성공 기준 / 실패 처리

CLAUDE.md §5 표의 B4 행 참조. 최상위 유사도 < 0.6이면 LLM 호출 없이 `확인필요` +
"근거 부족" 처리.
