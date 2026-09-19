"""
backtest/season_split.py - STEP 12 준비: 데이터 분리

V1 기준 시즌 배정:
    최적화(파라미터 탐색)  : 2023-24, 2024-25  (season=2023, 2024)
    최종 검증(1회성)        : 2025-26           (season=2025)
    실운영/미래 검증        : 2026-27           (season=2026) - 최적화/검증에 안 씀

중요한 규칙: 최종 검증 시즌(2025-26)의 결과를 보고 나서 파라미터를
다시 조정하면 안 된다. 이건 "한 번 보고 끝"이어야 진짜 검증이 된다
(그렇지 않으면 검증 데이터가 사실상 또 다른 최적화 데이터가 되어버려서
스펙 16번이 막으려는 과적합이 뒷문으로 재발한다).

이 규칙을 사람이 실수로 어기지 않도록, 최종 검증을 한 번 실행하고 나면
마커 파일을 남기고, 그 이후 다시 최적화를 돌리려는 시도는 명시적으로
force=True를 주지 않는 한 막는다.
"""

import json
import os
from datetime import datetime

OPTIMIZATION_SEASONS = [2023, 2024]
FINAL_VALIDATION_SEASON = 2025
FUTURE_SEASON = 2026  # 실운영/미래 예측용. 최적화·검증 어디에도 안 씀

_LOCK_FILE = os.environ.get("BACKTEST_LOCK_FILE", "final_validation_completed.json")


class FinalValidationAlreadyDoneError(Exception):
    pass


def is_final_validation_locked() -> bool:
    return os.path.exists(_LOCK_FILE)


def lock_after_final_validation(best_params: dict, metrics: dict):
    """
    최종 검증을 실행한 뒤 반드시 호출한다. 이후 optimize 재실행을 막기 위한 기록.
    """
    with open(_LOCK_FILE, "w") as f:
        json.dump({
            "locked_at": datetime.utcnow().isoformat(),
            "best_params": best_params,
            "final_validation_metrics": metrics,
            "note": "이 파일이 있으면 최종검증(2025-26)이 이미 실행된 것. "
                    "재조정하려면 force=True로 명시적으로 해제해야 함.",
        }, f, ensure_ascii=False, indent=2)


def guard_optimization_rerun(force: bool = False):
    """
    STEP 11 최적화 루프 시작 전에 호출한다. 이미 최종검증까지 끝난 상태에서
    force 없이 다시 최적화를 돌리려 하면 에러를 낸다 (실수 방지용 안전장치).
    """
    if is_final_validation_locked() and not force:
        raise FinalValidationAlreadyDoneError(
            f"'{_LOCK_FILE}' 파일이 있어서 최종검증이 이미 완료된 상태로 보입니다. "
            "최종검증 결과를 보고 파라미터를 다시 조정하는 것은 원칙에 어긋납니다. "
            "정말로 처음부터 다시 하려면 guard_optimization_rerun(force=True)로 호출하세요."
        )
