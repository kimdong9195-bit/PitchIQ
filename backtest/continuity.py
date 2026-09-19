"""
backtest/continuity.py - STEP 4: 선수단/감독 변화 보정

과거 경기의 선발 라인업이 "지금 시점 예상 라인업"과 얼마나 겹치는지,
그리고 그때 감독과 지금 감독이 같은지를 계산해서 별도의 계수를 만든다.
이 계수는 STEP 6에서 time_weight와 곱해져서 최종 가중치를 구성한다.

핵심 선수 가중치(공격수/미드필더/수비수/골키퍼를 다르게 취급)는 초기
버전에서는 "전원 동일 가중치"로 시작하고, PLAYER_IMPORTANCE_WEIGHTS를
통해 나중에 확장 가능하게만 만들어둔다 (지금 당장 채우지 않음 - 포지션별
중요도를 정하려면 그 자체도 임의로 정하지 말고 백테스트로 검증해야 하는데,
이건 STEP 11 이후 데이터가 쌓이면 다룰 문제).
"""

from dataclasses import dataclass
from typing import List, Optional

# 선수단 연속성 보정의 최소/최대값 후보 - 백테스트로 최적값 탐색
SQUAD_CONTINUITY_FLOOR_CANDIDATES = [0.5, 0.7, 0.85, 1.0]  # 완전히 다른 선수단이어도 최소 이 정도는 인정

# 감독 교체 보정 후보 (스펙 5번 초기 후보값)
MANAGER_CHANGE_PENALTY_CANDIDATES = [0.6, 0.7, 0.8, 0.9, 1.0]

# 포지션별 핵심 선수 가중치 - 지금은 빈 상태로 시작 (전원 동일 취급).
# 나중에 "이 선수가 빠지면 더 크게 깎는다"는 식으로 특정 player_id에 가중치를
# 넣을 수 있도록 구조만 마련해둔다.
PLAYER_IMPORTANCE_WEIGHTS: dict = {}  # {player_id: weight}, 비어있으면 전원 weight=1.0


@dataclass
class ContinuityFactors:
    squad_continuity: float    # 0~1, 1이면 선발 11명이 완전히 동일
    manager_continuity: float  # 0 또는 1 (같은 감독=1, 교체=0) - 실제 페널티 적용은 별도 함수에서


def compute_squad_continuity(
    past_starting_ids: List[int],
    current_expected_ids: List[int],
) -> float:
    """
    과거 경기 선발 11명과 "현재 예상 라인업"(또는 최근 라인업)의 일치 비율.
    가중치를 준 선수(PLAYER_IMPORTANCE_WEIGHTS)가 있으면 그 비중을 더 크게 본다.
    양쪽 다 비어있으면(데이터 없음) 1.0을 반환해 "보정 없음"으로 처리한다
    (데이터가 없다고 0으로 깎아버리면 오히려 왜곡되므로, 스펙 12번 원칙에 따라
     "모른다"를 "중립"으로 취급).
    """
    if not past_starting_ids or not current_expected_ids:
        return 1.0

    current_set = set(current_expected_ids)

    def weight_of(pid):
        return PLAYER_IMPORTANCE_WEIGHTS.get(pid, 1.0)

    total_weight = sum(weight_of(pid) for pid in past_starting_ids)
    if total_weight == 0:
        return 1.0

    matched_weight = sum(weight_of(pid) for pid in past_starting_ids if pid in current_set)
    return matched_weight / total_weight


def apply_squad_continuity_floor(raw_continuity: float, floor: float) -> float:
    """
    연속성이 너무 낮게 나와도(선수 다 바뀜) 완전히 0으로 만들지 않고
    최소 floor 값은 보장한다 (팀 자체의 스타일/전술은 선수 개개인보다
    오래 유지되는 경향이 있다는 걸 반영하는 안전장치).
    """
    return max(raw_continuity, floor)


def compute_manager_continuity(past_coach_name: Optional[str], current_coach_name: Optional[str]) -> float:
    """
    감독 이름 비교. 둘 중 하나라도 모르면(데이터 없음) 1.0으로 취급 (중립,
    보정 없음 - 모른다고 페널티를 주면 안 됨).
    """
    if not past_coach_name or not current_coach_name:
        return 1.0
    return 1.0 if past_coach_name.strip().lower() == current_coach_name.strip().lower() else 0.0


def compute_continuity_multiplier(
    squad_continuity: float,
    manager_same: float,
    squad_floor: float,
    manager_penalty: float,
) -> float:
    """
    선수단 연속성과 감독 연속성을 하나의 곱셈 계수로 합친다.
    - 선수단: floor로 하한선을 보장한 연속성 비율 그대로 사용
    - 감독: 같으면 1.0, 다르면 manager_penalty (후보값 중 하나)
    """
    squad_factor = apply_squad_continuity_floor(squad_continuity, squad_floor)
    manager_factor = 1.0 if manager_same == 1.0 else manager_penalty
    return squad_factor * manager_factor
