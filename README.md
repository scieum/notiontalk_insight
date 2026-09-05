# TalkInsight

카카오톡 오픈채팅방 **"노션하는 교사톡"** 의 대화를 주기적으로 읽어, 쓸 만한 것만
골라 **주간 리포트**로 만드는 에이전트 시스템입니다. 서버 없이 Mac 로컬에서 돕니다.

선생님들이 나눈 질문과 답이 스크롤 위로 사라지지 않게 하는 것이 목적입니다.

---

## 결과물

주간 리포트는 매 호 **같은 뼈대**로 나옵니다. 항목이 0건이어도 섹션을 없애지 않고
"이번 주에는 없었습니다"를 적습니다 — 0건이라는 사실도 정보이기 때문입니다.

| 섹션 | 내용 |
|------|------|
| This Week | 이번 주 주요 주제 3~5개 |
| Solved | 해결된 질문 (Q&A + 실무 팁 한 줄) |
| Open | 아직 답이 없는 질문 |
| Tips | 공유된 노하우 (노션 기능별 묶음) |
| Actions | 오픈채팅방에서 하기로 한 것 |
| Get Involved | 참여 안내 (매 호 동일) |

**1호 실측** (2026.08.19 ~ 08.26): 메시지 323건 · 스레드 84개 → FAQ 13 · 팁 26 ·
액션 3 · 미해결 3. LLM 호출 9회.

리포트는 `output/reports/1호_260819~260826.html` 형태로 쌓입니다.

---

## 파이프라인

두 갈래가 `kb.sqlite` 지식베이스를 공유합니다.

**A. 수집·발행** (주 2회)

```
A0  로컬 DB 읽기 ─┐
                  ├→ A1 정규화 → A2 병합 → A3 스레드분할 → A4 인사이트추출
A0' 내보내기 폴백 ┘        → A5 검증 → A6 PII마스킹 → A7 노션발행 → A8 KB갱신
```

**B. 지식베이스·응답**

```
B1 문서크롤링(주1회) → B2 청킹·임베딩
B3 질문함폴링(30분) → B4 검색 → B5 답변생성 → B6 검증 → B7 노션기입
```

### 구현 상태

| 단계 | 상태 |
|------|------|
| A0 로컬 DB 읽기 | **미착수** — 리스크 수용(`docs/risk_acceptance.md`)이 `미승인` |
| A0' 내보내기 폴백 | 파일 검증만 구현. UI 자동화(JXA) 미구현 |
| A1·A2 파싱·병합 | **완료** — 실데이터 26,907건 파싱률 100% |
| A3 스레드 분할 | 규칙 기반 1차 완료. LLM 2차 판정 미구현 |
| A4 인사이트 추출 | **완료** |
| A5 검증 | **완료** |
| A6 PII 마스킹·익명화 | **완료** |
| A7 노션 발행 | 구현(PR #2). 노션 토큰 등록 후 실발행 1회 검증 필요. HTML 렌더러가 검토 화면 역할 |
| A8 / B1~B7 | 미구현 |
| 오케스트레이션 | **완료** — `scripts/run_cycle.py`가 A0'~A7을 순서대로 부른다. launchd 월/목 09:00 |

각 스킬의 정확한 상태는 `.claude/skills/<이름>/SKILL.md` 맨 위에 적혀 있습니다.
이 표보다 그쪽을 신뢰하세요.

---

## 지켜야 하는 것

이 시스템은 **남의 대화**를 다룹니다. 아래는 타협하지 않는 규칙입니다.

- **읽기 전용.** 카톡 로컬 DB는 복사본만 열람하고, 원본과 카톡 프로세스에 절대
  쓰지 않습니다. 오픈채팅방 안에서 발언하는 비공식 클라이언트를 쓰지 않습니다.
- **원문은 로컬에만.** 외부 전송은 LLM API 호출에 필요한 범위로 한정합니다.
  대화 로그·메시지 DB·발행 전 산출물은 전부 `.gitignore`로 저장소에서 제외됩니다.
- **발행 전 익명화.** 전화번호·이메일 등은 항상 마스킹하고, 닉네임은
  `docs/consent.yaml`의 모드에 따라 가명(`함께한 선생님A`)으로 바꿉니다.
  운영진만 `docs/room_profile.md`에 등록해 예외로 둡니다 — 등록 자체가 그분의
  공개 동의를 전제합니다.
- **가명↔실명 대응표를 저장하지 않습니다.** 그 파일이 곧 재식별 열쇠입니다.
  필요할 때 `scripts/whois.py`가 같은 방식으로 다시 계산합니다.
- **리포트는 자동 발행하지 않습니다.** 사람이 검토(G-R)한 뒤에만 나갑니다.
  FAQ·팁·액션만 `자동생성` 태그를 달고 자동 발행됩니다.
- **생성과 평가를 분리합니다.** 인사이트를 만든 LLM이 자기 결과를 채점하지 않습니다.
  프롬프트 파일도 호출도 모델도 따로입니다.

전체 제약은 [CLAUDE.md](CLAUDE.md) §2에 있습니다.

---

## 실행

Python 3.9+ (표준 라이브러리만). LLM을 부르는 부분만 `google-genai`가 필요합니다.

```bash
python3 -m venv .venv
.venv/bin/pip install google-genai fonttools brotli
security add-generic-password -a "$USER" -s talkinsight-gemini -w   # Gemini API 키
```

카카오톡에서 "대화 내용 저장"으로 받은 파일을 `inbox/raw/`에 넣고:

```bash
# A1~A3
python3 .claude/skills/kakao-parser/scripts/parse_csv.py inbox/raw/<파일>.csv
python3 .claude/skills/kakao-parser/scripts/merge.py output/messages/parsed/<파일>.jsonl
python3 .claude/skills/threading/scripts/split_threads.py --period-id <구간>

# A4~A6 (최근 1주를 잘라 처리)
.venv/bin/python .claude/agents/insight-extractor/scripts/extract.py \
    --period-id <구간> --since 2026-08-19
.venv/bin/python .claude/agents/insight-evaluator/scripts/evaluate.py \
    --draft output/insights/period_<id>.draft.json
python3 .claude/agents/insight-evaluator/scripts/apply_verdicts.py \
    --draft output/insights/period_<id>.draft.json \
    --eval  output/insights/period_<id>.eval.json
python3 .claude/skills/pii-guard/scripts/apply.py output/insights/period_<id>.passed.json

# 리포트 HTML
.venv/bin/python scripts/render_report.py \
    --final output/insights/period_<id>.final.json \
    --draft output/insights/period_<id>.draft.json \
    --eval  output/insights/period_<id>.eval.json \
    --font  <Pretendard woff2 경로>
```

`--dry-run`을 붙이면 LLM을 부르지 않고 프롬프트만 확인할 수 있습니다.

### 매주 운영 (자동 사이클)

위 명령을 손으로 치는 대신 `scripts/run_cycle.py`가 전부 이어서 돌립니다.

```bash
.venv/bin/python scripts/run_cycle.py --pipeline collect_publish
```

1. 카카오톡 → 채팅방 설정 → 대화 내용 관리 → **텍스트 파일로 저장** → 받은 CSV를 `inbox/raw/`에 넣습니다.
   (아직 이 한 단계는 사람이 합니다. A0/A0' 자동화가 검증되면 사라집니다.)
2. 월/목 09:00에 launchd가 `run_cycle.py`를 부릅니다. 새 파일이 없으면 macOS 알림만 띄우고 끝납니다.
3. 새 파일이 있으면 A1~A6 → `output/reports/<N>호_….html` 검토본을 만들고, 노션 토큰이 있으면
   노션 DB에 **초안**으로도 올립니다. 리포트를 자동으로 발행 상태로 바꾸지는 않습니다(C5).
4. 검토 후 노션에서 상태를 직접 바꿉니다. 반려했다면 그 사유를 `output/state.json`의
   `rejections`에 적어 두면 다음 호 프롬프트에 주입됩니다.

사이클 상태(지난 호가 다룬 마지막 시각, 호수, 처리한 파일)는 `output/state.json`에 있습니다.
launchd 설치는 `launchd/com.talkinsight.collect_publish.plist` 머리말을 보세요.

---

## 구조

```
.claude/skills/     단계별 도구 (파서, 스레드분할, PII, 노션, KB, Gemini 래퍼)
.claude/agents/     생성·평가 에이전트 (insight-extractor / insight-evaluator 등)
lib/common/         로그·에스컬레이션·스키마·경로 공용 모듈
scripts/            run_cycle.py(오케스트레이션), render_report.py, whois.py
docs/               형식·디자인·동의·방 정보 문서
launchd/            스케줄 3종 (수집·KB갱신·질문폴링)
inbox/ output/      로컬 전용. 저장소에는 폴더 구조만 남습니다
```

문서 안내:

- [CLAUDE.md](CLAUDE.md) — 운영 규칙. 작업 전에 먼저 읽습니다
- [claude_TalkInsight_에이전트_설계서.md](claude_TalkInsight_에이전트_설계서.md) — 전체 설계와 결정 근거
- [docs/report_format.md](docs/report_format.md) — 리포트에 **무엇을** 싣는가
- [docs/design.md](docs/design.md) — 리포트가 **어떻게 보이는가**
- [docs/mac_handoff_checklist.md](docs/mac_handoff_checklist.md) — 다른 기기에서 이어받을 때

---

## 모델

Gemini 단일 구성입니다. 생성과 평가에 **다른 모델**을 씁니다 — 같은 모델이 만들고
채점하면 같은 맹점을 공유하고, 무료 등급 할당량도 모델별로 계산되기 때문입니다.

| 용도 | 모델 |
|------|------|
| 생성 (A4) | `gemini-2.5-flash` |
| 평가 (A5) | `gemini-3.5-flash` |
| 임베딩 | `gemini-embedding-001` (3072차원) |

무료 등급은 **모델당 하루 20요청**입니다. A4 한 사이클이 9회 정도이니 여유롭지
않습니다. 배치 결과는 캐시되므로 중간에 끊긴 실행을 다시 돌려도 재과금되지 않습니다.

---

## 저장소에 없는 것

- 대화 원문, 메시지 DB, 발행 전 산출물, 실행 로그 — 전부 로컬에만 둡니다
- `docs/db_access_reference.md` (카카오톡 DB 접근 조사 기록) — 공개 배포하지 않습니다
- API 키
