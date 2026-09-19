"""
backtest/time_decay.py - STEP 3: Time Decay

공식: time_weight = 0.5 ^ (elapsed_days / half_life)

half_life는 코드에 고정하지 않는다. STEP 11(파라미터 최적화)에서
HALF_LIFE_CANDIDATES 중 실제 오차가 가장 낮은 값을 찾아서 쓴다.
"""

from datetime import date, datetime
from typing import List

HALF_LIFE_CANDIDATES = [30, 60, 90, 120, 180]  # 일 단위. 백테스트로 최적값 탐색 대상


def elapsed_days(match_date: str, as_of_date: str) -> float:
    """match_date로부터 as_of_date까지 며칠 지났는지. 둘 다 'YYYY-MM-DD' 형식."""
    d1 = datetime.fromisoformat(match_date)
    d2 = datetime.fromisoformat(as_of_date)
    return (d2 - d1).days


def time_weight(elapsed: float, half_life: float) -> float:
    if elapsed < 0:
        return 0.0  # 미래 경기는 가중치 0 (애초에 원칙 1에 의해 데이터에 없어야 하지만 안전장치)
    return 0.5 ** (elapsed / half_life)


def apply_time_weights(matches: List[dict], as_of_date: str, half_life: float) -> List[dict]:
    """
    각 경기 dict에 'time_weight' 키를 추가해서 반환한다 (원본은 안 건드리고 복사본 반환).
    """
    result = []
    for m in matches:
        w = time_weight(elapsed_days(m["match_date"], as_of_date), half_life)
        weighted = dict(m)
        weighted["time_weight"] = w
        result.append(weighted)
    return result
