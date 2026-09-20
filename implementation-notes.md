# 구현 노트 — TalkInsight

> 개발 중 실시간 갱신. 명세·계획·관례와 달라졌거나 해석이 필요했던 모든 지점을 기록.
> 시작: 2026-08-27 · 최종 갱신: 2026-09-03 16:55
> 입력: 사용자 요청 — 현재 UI를 감사하고 notiontalk.com의 CSS를 계승하며 커뮤니티 메뉴 배치를 검토
> 현재 명세: `/home/user/PLAN-NOTIONTALK-INSIGHT-NEWSLETTER-AUTOMATION-20260903.md` Phase 1

## 설계 결정

### 2026-09-19 — 주간 시리즈 제목과 주별 hook을 분리
- **상황/기준**: 1호는 일반 요약 제목, 2·3호는 사이음이 선택한 주제별 `hook_title`을 정식 제목으로 사용해 시리즈 정체성이 일관되지 않았다.
- **선택**: `title`은 `노션하는 교사톡 주간 인사이트 N호`로 고정하고, `hook_title`은 nullable 부제목으로 분리한다.
- **근거**: 매주 톡방 전체를 요약한다는 레터의 성격을 제목에서 고정하고, 주별 해석은 부제목 계층에만 두기 위해서다.
- **위치**: `scripts/build_publication.py`, `schemas/publication.schema.json`, `scripts/backfill_report_publication.py`

### 2026-09-19 — 구 리포트 HTML backfill은 공개 DOM만 사용
- **상황/기준**: 과거 1~3호는 A6 final.json과 embedded projection을 Git에 올리지 않았지만, 이미 검토용 HTML에 전체 정보 구조가 남아 있다.
- **선택**: 공개 HTML의 masthead·stats·topics·FAQ·open·tips·actions만 파싱하고, legacy 닉네임은 공개 운영자 이름 또는 승인된 가명으로 정규화한 뒤 현재 publication validator를 다시 통과시킨다.
- **근거**: 원문 대화·source ID·로컬 final 파일에 접근하지 않고 이미 공개 검토된 표면만 이전한다.
- **위치**: `scripts/backfill_report_publication.py`

### 2026-09-19 — 공개 운영자 이름을 anon 검증에서 명시적 허용
- **상황/기준**: 사용자 규칙의 기본 공개 이름도 일반 person-like honorific으로 차단됐다.
- **선택**: 공개 기본 이름 하나만 text·action owner 경계에서 허용하고, 다른 `OO쎤`은 계속 거부한다.
- **근거**: 사용자 실명을 추가로 노출하지 않으면서 이미 승인된 공개 이름을 지원한다.
- **위치**: `lib/publication.py`, `tests/test_publication.py`

### 2026-09-18 — 리포트 HTML 하나에 뉴스레터 투영 데이터를 함께 보존
- **상황/기준**: 별도 publication PR은 사이음의 기존 리포트 PR·main 흐름과 이중으로 운영된다.
- **선택**: A6 allowlist projection을 리포트 HTML의 `application/json` script에 비가시적으로 포함하고, 별도 publication PR 생성을 제거한다.
- **근거**: 사이음이 올리는 기존 리포트 HTML 한 파일만으로 웹 디자인과 뉴스레터 구조화 입력을 같은 커밋에 묶을 수 있다.
- **위치**: `scripts/render_report.py`, `scripts/run_cycle.py`

### 2026-09-18 — 로컬 projection 파일은 output 아래의 임시 입력으로만 사용
- **상황/기준**: HTML에 JSON을 포함하기 위해 중간 파일이 필요하지만, 이를 다시 Git에 올리면 한 호가 두 파일로 나눌다.
- **선택**: `build_publication.py --out` 경로로 `output/reports/.insight-*.publication.json`을 잠시 만들고, 최종 Git 산출물은 `reports/*.html` 한 파일만 유지한다.
- **근거**: 기존 리포트 PR 작성 방식을 바꾸지 않고 단일 산출물 계약을 유지한다.
- **위치**: `scripts/build_publication.py`, `scripts/run_cycle.py`

### 2026-09-18 — 승인된 공개 projection을 뉴스레터 파이프라인의 단일 입력으로 사용
- **상황/기준**: 현재 A7은 TalkInsight 전용 Notion DB에 본문을 복제하지만, 노션톡 뉴스레터 공개 HTML·뉴스레터 DB·SES 발송과는 연결되지 않았다.
- **선택**: A6 직후 allowlist `publication.json`을 항상 생성하고, `TALKINSIGHT_GITHUB_TOKEN`이 있을 때 publication PR을 생성·갱신한다. 기존 Notion 초안 경로는 `--legacy-notion-draft`로만 명시적 실행한다.
- **근거**: 이전 구현의 hash·PII·exact-SHA 승인 계약을 재사용하고, Notion 본문 복제 원장과 노션톡 뉴스레터 원장의 이중 정본을 피한다.
- **위치**: `scripts/run_cycle.py`, `scripts/build_publication.py`, `scripts/open_publication_pr.py`

### 2026-09-03 — JSON과 날짜는 Python의 관대한 동등성·파서보다 좁게 검증
- **상황/기준**: 표준 `json.loads`는 중복 키를 마지막 값으로 덮고 Python은 `True == 1`이며 `date.fromisoformat`은 compact 날짜도 허용한다.
- **선택**: 모든 publication/A6 builder 입력에 중첩 중복 키 거부 loader를 사용하고, schema const·enum은 타입까지 같아야 하며, 날짜는 정규식과 calendar 검사를 모두 통과한 `YYYY-MM-DD`만 허용한다.
- **근거**: 앞선 PII 값·잘못된 타입·비표준 날짜가 파싱 과정에서 정상 값으로 축약되는 우회를 막기 위해서다.
- **위치**: `lib/publication.py`, `scripts/build_publication.py`, `scripts/open_publication_pr.py`, `tests/test_publication.py`

### 2026-09-03 — A6 증거에 발행 기간 경계를 포함
- **상황/기준**: builder가 CLI의 기간만 신뢰하면 같은 A6 산출물을 2030년 호로 다시 라벨링할 수 있다.
- **선택**: A6가 실제 입력의 `since`/`until` 또는 해당 구간 DB의 최소·최대 시각에서 검증한 `period_start`/`period_end`를 privacy/provenance evidence와 payload digest에 넣고, builder 인자와 정확히 같을 때만 projection을 만든다.
- **근거**: 공개 기간을 A6가 처리한 데이터 범위와 불변으로 결합해야 한다.
- **위치**: `.claude/skills/pii-guard/scripts/apply.py`, `scripts/build_publication.py`, `tests/test_publication.py`

### 2026-09-03 — base 소유 PR 게이트는 head를 데이터로만 취급
- **상황/기준**: GitHub 공식 문서상 `pull_request`는 PR merge commit의 workflow를 실행하므로 PR 작성자가 workflow 자체를 바꿀 수 있어 base-owned validator라는 설명이 성립하지 않는다.
- **선택**: secrets 없는 `pull_request_target`에서 base checkout의 validator만 실행하고, exact head SHA를 별도 디렉터리에 checkout해 데이터로만 검증한다. head의 코드·테스트·action은 실행하지 않는다.
- **근거**: base-owned workflow와 validator를 유지하면서 pwn-request 실행 경로를 없애기 위해서다. provider의 required check·CODEOWNERS 설정은 여전히 별도 운영 게이트다.
- **위치**: `.github/workflows/validate-publication.yml`, `scripts/validate_publication.py`, `README.md`

### 2026-09-03 — 공개 privacy 표시는 A6 불변 증거를 검증한 결과만 사용
- **상황/기준**: 현재 projection은 실행 시점의 `docs/consent.yaml`만 읽고 `pii_scan_passed`와 `source_ids_stripped`를 무조건 `true`로 생성해, A6 결과가 named 상태에서 anon 설정으로 바뀐 경우도 익명화 완료로 오인할 수 있다.
- **선택**: A6 입력에 period/content digest에 묶인 privacy evidence가 없으면 projection을 fail closed하고, evidence의 모드·검사 결과·source ID 제거 결과를 검증한 뒤에만 공개 privacy 필드를 만든다.
- **근거**: 현재 설정은 A6가 실제로 어떤 모드와 입력에 대해 수행됐는지 증명하지 못한다.
- **위치**: `scripts/build_publication.py`, `lib/publication.py`, `tests/fixtures/`, `tests/test_publication.py`

### 2026-09-03 — content PR 검증은 base revision 소유 validator를 exact head payload에 적용
- **상황/기준**: PR checkout 안의 validator와 workflow만 실행하면 같은 PR이 검증 코드를 약화해 publication을 통과시킬 수 있다.
- **선택**: `pull_request_target`의 base-owned workflow가 base SHA에서 validator·schema를 가져오고 exact head tree는 데이터로만 검증한다. head의 Python·테스트·action은 실행하지 않으며 required status check와 protected base가 운영 의존성이다.
- **근거**: secret 없이 read-only token만 사용하면서 신뢰 코드와 비신뢰 데이터를 분리하고, head 변경 시 SHA 결합 검증을 다시 수행할 수 있다.
- **위치**: `.github/workflows/validate-publication.yml`, `scripts/validate_publication.py`, `README.md`

### 2026-09-03 — publication 저장소 전체를 폐쇄형 경로·slug 집합으로 검증
- **상황/기준**: 단일 변경 파일만 검사하면 기존 publication 간 slug 충돌과 publication 경로 밖의 raw/final/draft/eval/log 산출물 유입을 놓친다.
- **선택**: tracked tree 전체에 repo-wide forbidden artifact denylist를 적용하고 모든 publication의 slug 고유성을 함께 검사한다.
- **근거**: content PR과 code PR 모두에서 민감 산출물의 우발 커밋과 공개 URL 충돌을 차단해야 한다.
- **위치**: `scripts/validate_publication.py`, `tests/test_publication.py`

### 2026-09-03 — 공개 projection은 폐쇄형 허용 목록으로 재구성
- **상황/기준**: A6 `final.json`에는 공개하면 안 되는 `source_message_ids`, `source_thread_ids`, 항목 `id`, `merge_into`가 남아 있다.
- **선택**: 입력 객체를 복사한 뒤 제거하지 않고, 공개 필드만 새 객체에 옮긴다. 액션 담당자는 `owner_nickname` 키 대신 공개 의미의 `owner`로 내보낸다.
- **근거**: 새 내부 필드가 추가돼도 공개 JSON으로 자동 전파되지 않게 하기 위해서다.
- **위치**: `scripts/build_publication.py`, `lib/publication.py`

### 2026-09-03 — 생성 시각을 필수 입력으로 고정
- **상황/기준**: `generated_at`은 해시 대상인데 실행 시각을 자동 사용하면 같은 A6 입력을 두 번 실행해도 해시가 달라진다.
- **선택**: RFC 3339 `--generated-at`을 필수로 받고, 같은 입력·메타데이터는 바이트 단위로 같은 결과를 만든다.
- **근거**: 계획의 같은 입력 no-op 계약과 콘텐츠 해시 재현성을 함께 지키기 위해서다.
- **위치**: `scripts/build_publication.py`

### 2026-09-03 — PR 자동화는 GitHub Contents API 단일 파일 커밋으로 제한
- **상황/기준**: 자동화가 로컬 브랜치를 바꾸거나 worktree 전체를 커밋하면 content PR에 코드·workflow가 섞일 수 있다.
- **선택**: 실행 모드는 고정 저장소와 `publications/<content_id>/publication.json` 한 경로만 Contents API로 생성·갱신하고, PR API로 같은 head branch를 생성·갱신한다.
- **근거**: 한 PR 한 publication 파일 계약을 API 경계에서 강제할 수 있다.
- **위치**: `scripts/open_publication_pr.py`

### 2026-09-03 — 무비밀 PR CI는 base-owned `pull_request_target`과 read-only token만 사용
- **상황/기준**: publication PR의 JSON은 검증해야 하지만 PR 작성자가 workflow를 바꾸거나 head 코드를 secret·쓰기 권한과 함께 실행하면 안 된다.
- **선택**: `pull_request_target`, `permissions: contents: read`, secret 0개로 고정하고 base validator만 실행한다. exact head checkout은 데이터 입력이며 head의 실행 파일·테스트·action은 호출하지 않는다.
- **근거**: GitHub 공식 문서상 `pull_request` workflow는 PR merge commit에서 오지만 `pull_request_target` workflow는 base default branch에서 온다. 위험한 지점은 privileged context에서 untrusted head 코드를 실행하는 것이므로 그 실행 경로를 두지 않는다.
- **위치**: `.github/workflows/validate-publication.yml`

- 독립형 HTML 리포트 생성 방식과 Python 표준 라이브러리 구성은 유지한다. 이번 작업은 `scripts/render_report.py`의 정보 구조·HTML·CSS와 대응 문서만 좁게 다룬다.
- 실제 notiontalk.com 코드의 디자인 토큰과 컴포넌트 관례를 기준으로 삼는다. 현재 리포트의 별도 회보라 다크 테마는 브랜드 일관성을 해치므로 그대로 보존하지 않는다.
- 정본 경로는 `/contents/community/insights/`로 제안한다. `/community`는 본체에서 이미 `/contents/community/`로 308되는 레거시 경로라 새 콘텐츠의 정본으로 쓸 수 없다.
- 새 최상위 메뉴는 만들지 않는다. 먼저 커뮤니티 랜딩의 소개/다가오는 일정 다음, 이벤트 갤러리 전에 `주간 인사이트` 진입 영역을 두는 구성이 현재 정보 구조에 맞다.
- 생성 HTML을 완결된 문서로 바꿔 `doctype`, `lang`, viewport, `<main>`, 건너뛰기 링크, 고정 섹션 목차, print 규칙을 포함한다.
- notiontalk.com의 원본 muted 토큰은 작은 글자에서 대비가 부족해 필수 메타에는 접근성 파생 토큰 `#746F6A`를 쓴다. 원본 토큰 자체는 변경하지 않는다.
- 실제 운영 데이터의 통계 5개를 지원하도록 마지막 통계 셀을 전체 폭으로 배치한다.
- 글꼴 서브셋 대상은 생성 콘텐츠뿐 아니라 내비게이션·건너뛰기 링크·판권을 포함한 최종 문서 전체에서 수집한다. 실제 샘플의 화면 글자 누락이 0개임을 확인했다.
- 사용자 시각 검토를 반영해 호별 머리말을 랜딩페이지형 히어로에서 컴팩트한 기사형 위계로 낮춘다. 제목 최대 크기를 4.35rem에서 2.75rem으로 줄이고 통계는 우측 부유 카드 대신 가로 메타 스트립으로 둔다.

## 편차

- 없음.

## 트레이드오프

### 2026-09-03 — A6 digest는 우발적 변조 탐지이며 작성자 인증은 아님
- **상황/기준**: unkeyed SHA-256 evidence는 산출물과 기간의 우발적 드리프트를 찾지만 악의적인 작성자가 새 digest를 계산하는 것은 막지 못한다.
- **선택**: 로컬 A6에서 넓은 PII·가명 계약을 fail closed로 검사하되, 적대적 작성자에 대해서는 upstream 보호 브랜치의 trusted gate와 코드 소유자 리뷰를 필수 운영 전제로 문서화한다.
- **근거**: 암호학적 작성자 신뢰와 provenance 무결성을 혼동하지 않기 위해서다.
- **위치**: `.claude/skills/pii-guard/scripts/mask.py`, `.claude/skills/pii-guard/scripts/apply.py`, `README.md`

### 2026-09-03 — 표준 라이브러리 validator와 JSON Schema를 함께 유지
- **상황/기준**: 프로젝트는 Python 표준 라이브러리 기반이고 PR CI는 비밀·설치 의존성 없이 실행해야 한다.
- **선택**: 계약 문서는 JSON Schema로 두고, CI 실행 검증은 같은 폐쇄형 계약을 구현한 표준 라이브러리 validator가 수행한다.
- **근거**: PR마다 패키지 설치나 공급망 의존성을 만들지 않는 대신, schema-validator 정합성은 fixture 테스트로 묶는다.
- **위치**: `schemas/publication.schema.json`, `lib/publication.py`, `tests/test_publication.py`

### 2026-09-03 — PR dry-run은 원격 상태를 추정하지 않음
- **상황/기준**: 실제 open PR의 존재와 head 내용은 네트워크 조회 없이는 확정할 수 없다.
- **선택**: 기본 dry-run은 `would_create_or_update`로 보고하고, `--existing`에 로컬 대체 파일을 명시한 검증에서만 같은 hash `noop`을 판정한다.
- **근거**: 네트워크 0건 계약을 유지하면서 원격 상태를 확인한 것처럼 오인시키지 않기 위해서다.
- **위치**: `scripts/open_publication_pr.py`, `tests/test_publication.py`

- 리포트는 외부 리소스 없이 단일 HTML로 열려야 하므로 본체 Next.js/Tailwind 컴포넌트를 직접 공유하지 못한다. 대신 동일 토큰·타이포·간격·상태 표현을 순수 CSS 변수로 옮긴다.
- 글로벌 `커뮤니티` 메뉴를 드롭다운으로 바꾸면 `@notiontalk/nav-config`, 랜딩, 뉴스레터 앱, Bullet 내비게이션을 함께 동기화해야 한다. 이 저장소의 디자인 PR에서는 그 공유 상태를 건드리지 않고 통합 권장안만 기록한다.
- 단일 HTML에 사이트 축약 내비게이션과 섹션 목차를 포함하면 순수 CSS만 바꾸는 것보다 변경 폭이 늘지만, 현재 문서에 없던 사이트 복귀 경로와 긴 리포트 탐색을 제공한다. 이 동작은 데이터·발행 상태를 바꾸지 않는다.

## 미결 질문

- [해결됨] 과거 1~3호 모두 publication 복원·검증과 실 provider shadow dry-run을 통과했다. 세 호 모두 `would_publish=true`, 식별자 충돌 없음, 외부 쓰기 0건이었다. embedded script를 제거한 HTML은 각 기존 PR 파일과 바이트 단위로 같았다 (`scripts/backfill_report_publication.py`, `reports/`).
- [해결됨] A6 projection을 HTML에 안전하게 포함하고 newsletter parser가 다시 검증하는 계약을 fixture 왕복 검증과 전체 58개 테스트로 확인했다 (`scripts/render_report.py`, `tests/test_render_report.py`).
- [해결됨] 2026-09-18 최신 `origin/main` 위에 공개 projection 코드를 이식하고 `run_cycle.py`에 A7P 생성·HTML embed를 연결했다 (`scripts/run_cycle.py`, `tests/test_publication.py`).
- [미해결] 현재 브랜치는 로컬 커밋 준비 상태이며, upstream push·PR·main 보호 설정·required check 등록은 각각 별도 승인 전에 수행하지 않는다.
- [확인필요] upstream `scieum/notiontalk_insight` 관리자가 `main` 보호, `validate-publication / trusted-content-gate` required check, CODEOWNERS 승인 요구를 실제로 설정해야 한다. 현재 계정은 upstream admin 권한이 없어 로컬 코드로 강제하거나 확인 완료 처리할 수 없다 (`README.md`, `.github/CODEOWNERS`).
- [해결됨] Phase 1 최종 재감사의 중복 JSON 키, 타입 민감 const·enum, full-date, local/remote 공통 byte ceiling 검증까지 보강했다. A6→builder 통합 및 적대 케이스를 포함한 전체 41개 테스트, `/tmp` pycache를 사용한 `py_compile`, 전체 publication validator, `git diff --check`가 통과했다 (`tests/test_publication.py`).
- [해결됨] Phase 1 차단 항목을 보강하고 전체 29개 테스트, 전체 publication validator, `py_compile`(별도 `/tmp` pycache), status 포함 changed-path validator, `git diff --check`를 통과했다 (`tests/test_publication.py`).
- [해결됨] Phase 1 공개 projection, 무비밀 검증 workflow, offline dry-run, token 선확인, 같은 hash no-op을 synthetic full/0건 fixture로 검증했다. 전체 24개 테스트, `py_compile`, 전체 publication validator, `git diff --check`가 통과했다 (`tests/test_publication.py`).
- [해결됨] 공개 경로는 `/contents/community/insights/`로 제안한다. `/community`는 레거시 리다이렉트다 (`/mnt/c/dev/notiontalk-landing/next.config.ts`).
- [해결됨] 이 PR은 리포트 저장소 내부 디자인 개선과 통합 계약 문서화로 한정한다. 본체 라우트·사이트맵·내비게이션은 별도 PR 대상이다 (`docs/design.md`).
- [해결됨] 데스크톱 1280px, 모바일 390px, 통계 5개, Pretendard 포함·미포함 조건을 시각 확인했다. 표준 라이브러리 회귀 테스트 8개와 `py_compile`, `git diff --check`가 통과했다.
