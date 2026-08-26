"""V0 스캐폴딩 진입점 — 파이프라인 오케스트레이션의 자리표시자.

각 단계 스킬이 아직 미구현이므로(V1~V6에서 순차 구현), 지금은 정해진 순서대로
단계를 "돈다"는 사실과 append-only 로그가 실제로 남는다는 사실만 검증한다.
V0 완료 기준(설계서 §5): "빈 파이프라인이 로그를 남기며 실행".

launchd plist(launchd/*.plist)가 호출하는 실제 진입점이기도 하다. 각 스킬이
구현되면 해당 단계의 log_event(..., "skip", ...) 호출을 실제 스크립트 호출로
교체한다 — 이 파일의 구조(단계 나열 → 순서대로 실행 → 로그) 자체는 유지된다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows 콘솔(cp949 등)에서도 한글 로그 출력이 깨지지 않도록 강제한다.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.common.log import log_event
from lib.common.paths import ensure_output_dirs

PIPELINE_STAGES = {
    "collect_publish": ["A0", "A0'", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"],
    "kb_refresh": ["B1", "B2"],
    "poll_questions": ["B3", "B4", "B5", "B6", "B7"],
}


def run_pipeline(name: str) -> None:
    stages = PIPELINE_STAGES[name]
    print(f"[run_cycle] {name} 시작 — 단계: {', '.join(stages)}")
    for stage in stages:
        # TODO: 각 단계가 구현되면 여기서 해당 스킬 스크립트를 호출하고,
        # 실제 성공/실패/스킵 결과를 log_event에 반영한다.
        log_event(stage, "skip", detail="스킬 미구현 (V0 스캐폴딩 단계)")
        print(f"  - {stage}: skip (미구현)")
    print(f"[run_cycle] {name} 종료")


def main() -> None:
    parser = argparse.ArgumentParser(description="TalkInsight 파이프라인 실행 진입점")
    parser.add_argument(
        "--pipeline",
        required=True,
        choices=sorted(PIPELINE_STAGES.keys()),
    )
    args = parser.parse_args()

    ensure_output_dirs()
    run_pipeline(args.pipeline)


if __name__ == "__main__":
    main()
