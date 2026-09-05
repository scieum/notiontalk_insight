# CLAUDE.md — TalkInsight

이 문서는 TalkInsight 에이전트 시스템에서 작업할 때 Claude Code가 항상 따라야 하는 운영 규칙이다. 배경·근거·미해결 이슈 등 전체 맥락은 [claude_TalkInsight_에이전트_설계서.md](claude_TalkInsight_에이전트_설계서.md)를 참조한다. 이 파일과 설계서가 충돌하면 **설계서가 최신 결정이면 이 파일을 갱신**하고, 그렇지 않으면 이 파일의 규칙을 우선한다.

> **Mac에서 이 프로젝트를 처음 여는 세션이라면** 먼저 [docs/mac_handoff_checklist.md](docs/mac_handoff_checklist.md)를 읽을 것 — Windows에서 어디까지 만들고 검증했는지, A0(kakao-db-reader) 착수 전 무엇부터 해야 하는지 정리되어 있다.

---

## 1. 프로젝트 목적과 두 파이프라인 개요

TalkInsight는 카카오톡 오픈채팅방(노션톡 커뮤니티) 대화를 주기적으로 수집해 인사이트를 노션에 발행하고, 노션 질문함의 질문에 근거 기반으로 답변하는 시스템이다.

- **파이프라인 A (수집·발행, 주 2회)**: `A0 DB읽기 → A1 정규화 → A2 병합 → A3 스레드분할 → A4 인사이트추출 → A5 검증 → A6 PII마스킹 → A7 노션발행 → A8 KB갱신`
- **파이프라인 B (지식베이스·응답)**: `B1 문서크롤링(주1회) → B2 청킹·임베딩` 그리고 별도로 `B3 질문함폴링(30분) → B4 검색 → B5 답변생성 → B6 검증 → B7 노션기입`
- 두 파이프라인은 `kb.sqlite` 지식베이스를 공유한다 (A8이 채팅 원천을, B2가 문서 원천을 채운다).
- 서버 없이 Mac 로컬 launchd로 실행한다. LLM은 Gemini 단일(`gemini-2.0-flash`, 임베딩 `text-embedding-004`).

세부 단계 정의·스키마·성공기준은 설계서 §2, §3을 반드시 참조할 것 — 이 파일은 요약이며 전문을 대체하지 않는다.

---

## 2. 불변 제약 (반드시 지킬 것)

| ID | 규칙 |
|----|------|
| C1 | **읽기 전용.** 카톡 로컬 DB는 복사본만 열람. 원본 파일·카톡 프로세스에 절대 쓰기·주입·후킹 금지. 방 안에서 발언하는 비공식 클라이언트 사용 금지 |
| C1' | DB 경로 실패·스키마 지문 불일치 시 공식 내보내기 폴백으로 **자동 전환**. 두 경로 산출물은 동일 정규화 스키마로 수렴 |
| C2 | 원문 로그는 로컬에만 보관. 외부 전송은 LLM API 호출에 필요한 범위로 한정. 동의 미확보 시 닉네임 익명화 후 발행. 전화번호·이메일·URL 토큰 등 PII는 발행 전 항상 마스킹 |
| C3 | 서버 없음, Mac 로컬 실행. Mac 미가동으로 건너뛴 사이클은 다음 실행이 `last_ts` 기준 자동 보정 |
| C4 | ~~LLM은 Gemini 단일~~ → **2026-09-05 개정**: 기본 백엔드는 이 Mac에 로그인된 **Claude Code CLI(`claude -p`)**. API 키 불필요. Gemini는 `TALKINSIGHT_LLM_PROVIDER=gemini`로 폴백(키는 Keychain/`.env`). 생성·평가는 서로 다른 모델. 배치 처리는 토큰 예산 상한 준수 |
| C5 | 주간 리포트는 사람 게이트(G-R) 필수 — 절대 자동 발행하지 않는다. FAQ/팁/액션은 자동 발행하되 `자동생성` 태그 부착 |
| C6 | 근거 3원천(채팅·공식문서·홈페이지)은 원천별 청킹 전략을 분리한다. 답변에는 원천 유형을 반드시 명시 |
| C7 | ~~스키마 지문 불일치 시 폴백~~ → **2026-08-27 개정**: katok 위임으로 지문 검증이 우리 책임에서 빠졌다. 대신 katok 호출 실패 시 폴백 + 에스컬레이션 |
| C8 | **append-only.** 메시지·로그·발행 이력은 삭제하지 않는다. 수정은 새 버전 레코드 추가로만 |
| C9 | 생성과 평가는 역할을 분리한다(LLM 자기검증 단독 금지). 확정된 산출물은 재생성하지 않고 파생시킨다. 예외는 에스컬레이션으로 처리 |

**특히 항상 지킬 것**: DB는 읽기 전용 · 원본 미접촉 · 임시 복사본은 사용 직후 즉시 삭제 · 대상 방 외 다른 방 데이터는 어떤 파일에도 남기지 않음(R5) · 원문 채팅 로그를 KB/발행물 이외의 목적으로 외부 전송하지 않음.

DB 직접 읽기 경로는 카카오 서비스 약관상 리스크가 있음을 운영자가 인지하고 `/docs/risk_acceptance.md`에 기록한 상태에서만 진행한다(설계서 §1.6 R1). **파일 존재만으로는 승인이 아니다** — 파일을 열어 `상태: 승인`인지 반드시 확인한다. `미승인`이면 A0을 실행하지 말고 운영자에게 먼저 확인을 요청한다.

---

## 2'. DB → 내보내기 폴백 전환 규칙

> **2026-08-27 개정.** A0을 직접 복호화에서 **katok CLI 위임**으로 바꿨다. 그래서
> 스냅샷·지문 검증·복호화 절차가 우리 쪽에서 사라졌다(katok이 대신 한다).
> 근거는 `docs/db_access_reference.md`의 "katok CLI 경로" 절, 승인 기록은
> `docs/risk_acceptance.md`.

1. 사이클 시작 시 `docs/risk_acceptance.md`의 `상태`가 `승인`이 아니거나 `katok`이 설치돼 있지 않으면 **kakao-db-reader를 호출하지 않고** 바로 kakao-export(폴백)로 간다.
2. A0 실행 순서: 승인 확인 → `katok doctor` → `katok source chats`로 대상 방 **정확히 일치** 검색 → `katok sync`(증분) → `katok transcript --chat <id> --since <last_ts>` → 결과 저장.
3. **`katok send`는 어떤 경우에도 호출하지 않는다**(C1). 화이트리스트로 강제한다.
4. 대상 방을 못 찾거나 katok 호출이 실패하면 1회 재시도 후 폴백 전환.
4. 폴백(kakao-export, JXA)이 3사이클 연속 발생하면 자동 처리를 멈추고 운영자에게 "DB 경로 점검 또는 폴백 상시화" 결정을 에스컬레이션으로 요청한다.
5. `/inbox/raw/`에 미처리 폴백 파일이 이미 있으면 내보내기를 다시 실행하지 말고 그 파일을 사용한다.
6. 폴백 전환 구간에서는 DB 레코드와 txt 레코드가 겹칠 수 있으므로 A2 병합 시 초 단위 ts 정규화 후 해시 비교로 교차 중복을 제거한다.

---

## 3. 오케스트레이션 순서와 상태 파일 위치

**메시지 구간 상태 전이**: `exported → parsed → merged → threaded → extracted → validated → published`

**실행 순서와 산출물 경로** (모든 경로는 프로젝트 루트 기준):

| 단계 | 산출물 |
|------|--------|
| A0 | `/inbox/db_extract/<timestamp>.jsonl` |
| A0' | `/inbox/raw/<timestamp>.csv` (Mac 카톡 26.7.0 실측 — 메뉴 이름은 "텍스트 파일로 저장"이지만 산출물은 CSV. 구버전 txt도 계속 인식) |
| A1 | `/output/messages/parsed/<timestamp>.jsonl` |
| A2 | `/output/messages/messages.sqlite`, `/output/messages/period_<id>.json` |
| A3 | `/output/threads/period_<id>.json` |
| A4 | `/output/insights/period_<id>.draft.json` |
| A5 | `/output/insights/period_<id>.eval.json` |
| A6 | `/output/insights/period_<id>.final.json` |
| A7 | 노션 DB 레코드, `/output/published/period_<id>.manifest.json` |
| A8 | `/output/kb/kb.sqlite` 갱신(원천=`chat`) |
| B1 | `/output/kb/raw/<source>/<slug>.md` |
| B2 | `kb.sqlite` 갱신(원천=`notion_help`/`notiontalk_site`) |
| B3 | `/output/questions/<id>.json` |
| B4 | `/output/questions/<id>.evidence.json` |
| B5 | `/output/answers/<id>.draft.json` |
| B6 | `/output/answers/<id>.eval.json` |
| B7 | 노션 질문함 행 `답변` 속성 |

각 단계는 이전 단계의 산출물 파일 경로를 입력으로 받는다. 신규 구간 메시지가 0건이면 A3부터는 스킵한다. 전체 스킬·스크립트 매핑은 설계서 §4.4를, 스킬 폴더 구조는 §4.1을 참조.

---

## 4. 서브에이전트 호출 규칙

- **메인 오케스트레이터만 서브에이전트를 호출한다.** 서브에이전트 간 직접 호출은 금지.
- 생성과 평가는 반드시 분리된 서브에이전트·별도 프롬프트·별도 호출로 수행한다(C9). 하나의 LLM 호출이 자기 결과를 스스로 평가하지 않는다.
- 서브에이전트 간 데이터 전달은 **`/output/` 파일 경로**로 한다. 프롬프트 인라인 전달은 반려 사유·질문 본문 등 1KB 이하 소량 텍스트만 허용.

| 서브에이전트 | 역할 | 트리거 |
|------------|------|-------|
| `insight-extractor` | 스레드 경계 판정(2차), 유형 분류, 4종 인사이트 생성 | A3 애매 구간 존재 시 / A4 |
| `insight-evaluator` | 충실성·근거일치·중복·공개적절성 평가 | A5 (insight-extractor와 항상 분리 호출) |
| `answer-writer` | 근거 기반 답변 생성 | B5 |
| `answer-evaluator` | 근거지지·환각·라벨 검증 | B6 (answer-writer와 항상 분리 호출) |

---

## 5. 실패 처리 표

| 단계 | 실패 조건 | 처리 |
|------|----------|------|
| A0 | 복호화·조회 오류 | 1회 재시도 → 실패 시 A0' 자동 전환 + 에스컬레이션 |
| A0 | 승인 미기록 / katok 미설치 / 대상 방 미발견 | 즉시 A0' 전환 |
| A0' | 내보내기 실패 | 60초 간격 2회 재시도 → 실패 시 macOS 알림 + `/output/escalations/` 기록 |
| A0' | 3사이클 연속 폴백 | 에스컬레이션: "DB 경로 점검 또는 폴백 상시화" 결정 요청 |
| A1 | 파싱률 95~99% | 미파싱 라인 로그 후 진행 |
| A1 | 파싱률 < 95% 또는 형식 미인식 | 에스컬레이션(파서 업데이트 필요) |
| A2 | 신규 메시지 0건 | 스킵+로그, 발행 사이클 미실행 |
| A2 | 해시 충돌(동일 해시·다른 원문) | 로그 후 둘 다 보존 |
| A3 | 스레드 크기 > 200 | 시간 기준 강제 분할 후 로그 |
| A3 | LLM 경계 판정 실패 | 1차 규칙 결과로 대체(스킵+로그) |
| A4 | 스키마 오류 | 최대 2회 재시도(오류 메시지 주입) → 지속 실패 시 해당 스레드 스킵+로그, 리포트 실패는 에스컬레이션 |
| A5 | 충실성·근거 실패 | 항목 재생성 1회 → 재실패 시 폐기+로그 |
| A5 | 공개 적절성 실패 | 에스컬레이션(자동 발행 금지) |
| A5 | 기존 항목과 유사도 ≥ 0.9 | 신규 발행 대신 기존 항목에 병합 |
| A6 | PII 재검사 매치 | 해당 항목 발행 보류 + 에스컬레이션 |
| A7 | API 429/5xx | 지수 백오프 3회 재시도 → 지속 실패 시 manifest에 `pending` 기록, 다음 사이클 재시도. 리포트 발행 실패는 에스컬레이션 |
| A8 | 임베딩 API 실패 | 3회 재시도 → 미처리 큐 기록, 다음 사이클 재시도 |
| B1 | 페이지 수 -20% 초과 감소 | 에스컬레이션(사이트 구조 변경 의심), 기존 KB 유지 |
| B3 | 빈 질문(<5자) | 상태 `확인필요` + 사유 기입 |
| B4 | 최상위 유사도 < 0.6 | LLM 호출 없이 `확인필요` + "근거 부족" |
| B5 | 스키마 오류 | 2회 재시도 → 실패 시 `확인필요` |
| B6 | 근거지지 실패 | 재생성 1회(지적 주입) → 재실패 시 `확인필요` |
| B6 | confidence < 0.7 | `확인필요` |
| B7 | API 실패 지속 | 상태 `처리중` 유지, 다음 폴링 재시도(중복 생성 방지: 답변 파일 존재 시 생성 스킵) |

**에스컬레이션 파일 형식**: `/output/escalations/<date>.md`, append-only. 각 항목은 최소 `시각 · 단계 · 사유 · 관련 파일 경로`를 포함한다.

---

## 6. 사람 게이트 3종과 반려 사유 주입 규칙

| 게이트 | 시점 | 사람이 하는 일 |
|-------|------|--------------|
| G-R | A7 이후 | 노션 리포트 `초안` 검토 → `발행` 또는 `반려`(사유 기입) |
| G-E | 에스컬레이션 발생 시 | `/output/escalations/`·macOS 알림 확인 → 조치(수동 내보내기 투입, 파서 수정, 공개 여부 판단, 답변 직접 작성) |
| G-C | 운영 시작 전 | `/docs/consent.yaml` 모드 결정, 방 공지로 동의 안내 |

**반려 사유 주입**: G-R에서 리포트가 `반려`되면 그 사유를 다음 발행 사이클의 A4(인사이트 추출) 프롬프트에 반드시 포함시킨다. 반려 사유가 누적되어 프롬프트가 비대해지지 않도록 최근 N건만 유지한다(정확한 N은 설계서 O7 미해결 — 기본 3건, 운영자 확정 전까지 임시값).

리포트는 **자동 발행 금지**(C5) — G-R을 거치지 않고 상태를 `발행`으로 바꾸는 코드·프롬프트를 작성하지 않는다.

---

## 7. 로그·매니페스트 형식

- **실행 로그**: `/output/logs/<date>.jsonl`, append-only. 모든 단계 실행마다 1줄(성공/실패/스킵 포함).
- **발행 매니페스트**: `/output/published/period_<id>.manifest.json` — 노션 페이지 ID 매핑, `pending` 상태 항목 포함.
- **에스컬레이션**: `/output/escalations/<date>.md`, append-only, 사람이 읽는 형식.
- 중간 산출물은 JSON, 사람이 읽는 리포트·에스컬레이션은 md, 저장소는 SQLite, 로그는 JSONL — 이 매핑을 벗어나지 않는다(설계서 §4.7).
- 로그·산출물에 복호화 키 자료·계정 고유값을 **절대 기록하지 않는다**(R4).

---

## 8. 운영자 수동 개입 절차

**수동 내보내기 투입**: A0/A0'가 모두 실패했거나 운영자가 직접 내보내기한 경우, 카카오톡 공식 "대화 내보내기" txt 파일을 `/inbox/raw/`에 넣으면 다음 실행에서 A1부터 재개된다. 파일명은 타임스탬프를 포함해 구분한다.

**consent 모드 전환**: `/docs/consent.yaml`의 모드를 `named`(닉네임 유지) ↔ `anon`(익명화)으로 바꾸면 다음 A6 실행부터 적용된다. 전환 시 방 공지로 참여자에게 안내가 선행되어야 한다(G-C).

**DB 경로 리스크 수용**: A0(kakao-db-reader)를 처음 활성화하기 전 `/docs/risk_acceptance.md`를 열어 `상태: 승인`인지 확인한다. `미승인`이면 운영자에게 리스크(§1.6 R1~R5) 인지 여부를 먼저 확인하고, 승인 시 운영자가 직접 그 파일의 상태·서명란을 채우게 한다(Claude가 대신 "승인"으로 바꾸지 않는다).

**카톡 앱 업데이트 대응**: R2 완화를 위해 카톡 자동 업데이트를 끄고 수동 업데이트 후 V1(A0) 회귀 테스트를 거치는 것을 권장한다. 업데이트 후 지문 불일치가 발생하면 정상 동작이며(§2' 참조), `known_fingerprints.json`에 새 지문을 추가하는 결정은 운영자가 한다.

---

## 참고: 폴더 구조 (설계서 §4.1 요약)

```
/CLAUDE.md
/.claude/skills/   kakao-db-reader, kakao-export, kakao-parser, threading,
                    pii-guard, notion-publish, kb-builder, kb-search, gemini-client
/.claude/agents/   insight-extractor, insight-evaluator, answer-writer, answer-evaluator
/docs/             room_profile.md, notion_feature_taxonomy.md, consent.yaml,
                    sources.yaml, risk_acceptance.md, db_access_reference.md,
                    report_format.md, design.md
/inbox/db_extract  /inbox/raw
/output/           messages, insights, threads, kb, published, answers, questions, logs, escalations
/launchd/          collect_publish.plist(월/목 09:00), kb_refresh.plist(일 03:00), poll_questions.plist(30분)
```

세부 스킬 트리거 조건은 설계서 §4.5, launchd 스케줄은 §4.6, 스키마는 §3을 참조.

이 구조는 아직 구현 전이며, 빌드 순서는 설계서 §5(V0~V6, A0 최우선)를 따른다. 미해결 이슈(§6 O1~O8)가 해결되지 않은 항목은 구현 전 운영자 확인이 필요하다.
