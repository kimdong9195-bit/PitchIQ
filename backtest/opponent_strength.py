"""
backtest/opponent_strength.py - STEP 5: 상대팀 강도 보정

"강팀 상대로 넣은 2골"과 "약팀 상대로 넣은 2골"을 다르게 취급한다.
STEP 2에서 계산한 상대팀의 attack_index/defense_index를 이용해,
실제 득점을 "상대 수비력 대비 기대만큼 조정한 득점"으로 바꾼다.

방식: 상대의 수비지수(defense_index)가 낮을수록(=수비가 좋을수록) 그 팀
상대로 넣은 골의 가치를 올려주고, 반대로 상대 수비가 약하면(defense_index
높음) 가치를 낮춘다. 즉:

    adjusted_goals = actual_goals / opponent_defense_index

opponent_defense_index가 1.0(평균)보다 작으면(수비가 좋음) adjusted_goals가
커지고, 1.0보다 크면(수비가 약함) adjusted_goals가 작아진다.

실점도 대칭적으로 상대 공격력(attack_index) 대비 보정한다:

    adjusted_conceded = actual_conceded / opponent_attack_index
"""

from typing import Dict, Optional

from backtest.team_strength import AttackDefenseIndex

MIN_INDEX = 0.3  # 지수가 너무 극단적으로 작으면(거의 0) 나눗셈이 폭주하므로 하한을 둔다
MAX_INDEX = 3.0  # 상한도 마찬가지로 둔다


def _clamp(value: float) -> float:
    return max(MIN_INDEX, min(MAX_INDEX, value))


def adjust_goals_for_opponent_strength(
    actual_scored: float,
    actual_conceded: float,
    opponent_index: Optional[AttackDefenseIndex],
) -> tuple:
    """
    한 경기의 실제 득점/실점을, 그 경기 상대팀의 강도를 반영해서 보정한다.
    opponent_index가 없으면(데이터 부족) 보정 없이 원래 값 그대로 반환한다
    (스펙 12번 원칙 - 모르면 임의로 만들지 않고 원본을 그대로 씀).
    """
    if opponent_index is None:
        return actual_scored, actual_conceded

    defense_factor = _clamp(opponent_index.defense_index)
    attack_factor = _clamp(opponent_index.attack_index)

    adjusted_scored = actual_scored / defense_factor
    adjusted_conceded = actual_conceded / attack_factor

    return adjusted_scored, adjusted_conceded


def adjust_matches_for_opponent_strength(
    matches: list,
    team_id: int,
    opponent_indices: Dict[int, AttackDefenseIndex],
) -> list:
    """
    team_id 관점에서, 각 경기의 득점/실점을 상대 강도 보정한 값으로 채운
    새 필드('adjusted_scored', 'adjusted_conceded')를 추가해서 반환한다.
    (원본 home_goals/away_goals는 그대로 두고 별도 필드로 추가 - STEP 2의
    단순 평균 계산과 이 보정된 평균 계산을 둘 다 비교/전환할 수 있게 하기 위함)
    """
    result = []
    for m in matches:
        if m["home_team_id"] == team_id:
            opponent_id = m["away_team_id"]
            scored, conceded = m["home_goals"], m["away_goals"]
        elif m["away_team_id"] == team_id:
            opponent_id = m["home_team_id"]
            scored, conceded = m["away_goals"], m["home_goals"]
        else:
            continue  # 이 팀과 무관한 경기 - 그대로 스킵

        opponent_index = opponent_indices.get(opponent_id)
        adj_scored, adj_conceded = adjust_goals_for_opponent_strength(scored, conceded, opponent_index)

        updated = dict(m)
        updated["adjusted_scored"] = adj_scored
        updated["adjusted_conceded"] = adj_conceded
        result.append(updated)

    return result


# ----------------------------------------------------------------------
# 순환참조 해결 - 반복(iterative) 방식. STEP 5 초기 버전은 1-pass(반복 없음)
# 근사로만 처리했으나, 이게 최선인지는 검증되지 않았다. "반복이 이론적으로
# 더 낫다"는 이유만으로 채택하지 않고, 1/2/3회 반복을 전부 같은 인터페이스로
# 만들어서 STEP 10~11 백테스트가 실제 오차로 비교/선택할 수 있게 한다.
# ----------------------------------------------------------------------

OPPONENT_STRENGTH_ITERATION_CANDIDATES = [0, 1, 2, 3]  # 0 = 보정 없음(모델 B), 1 = 1-pass, 2~3 = 반복


def _weighted_average_from_adjusted(adjusted_matches: list) -> Optional[tuple]:
    """adjusted_scored/adjusted_conceded 필드를 가중평균한다 (time_weight 있으면 반영)."""
    if not adjusted_matches:
        return None
    weights = [m.get("time_weight", 1.0) for m in adjusted_matches]
    total_weight = sum(weights)
    if total_weight == 0:
        return None
    avg_scored = sum(m["adjusted_scored"] * w for m, w in zip(adjusted_matches, weights)) / total_weight
    avg_conceded = sum(m["adjusted_conceded"] * w for m, w in zip(adjusted_matches, weights)) / total_weight
    return avg_scored, avg_conceded


def compute_iterative_team_indices(matches: list, iterations: int = 1) -> Dict[int, AttackDefenseIndex]:
    """
    상대팀 강도 지수를 iterations회 반복 계산한다.

    iterations=0: 상대강도 보정을 아예 안 쓴다 (빈 dict 반환 -> 호출하는 쪽에서
                  opponent_indices.get(x)가 항상 None이 되어 보정 없이 원본
                  그대로 쓰임). 이게 "모델 B" - 과거 상대강도 보정 없이
                  기본 공격/수비력만 쓰는 비교 대상이다.
    iterations=1: STEP 2와 동일 (가중치 없는/있는 원시 득점 기반 단순 지수, 반복 없음)
    iterations=2: 1회차 지수를 "상대 강도"로 써서 득점을 보정한 뒤, 그 보정된
                  값으로 지수를 다시 계산 (1번 갱신)
    iterations=3: 2회차에서 나온 지수를 다시 "상대 강도"로 써서 한 번 더 보정/재계산

    리그 평균(league_avg_goals)은 반복 내내 "원시 득점 기준"으로 고정한다 -
    그래야 attack_index=1.0의 의미("리그 평균 원시 득점 수준")가 반복 횟수와
    상관없이 일관되게 유지된다. 반복마다 바뀌는 건 분자(팀별 보정된 득점)뿐이다.
    """
    if iterations == 0:
        return {}

    from backtest.team_strength import compute_league_averages, AttackDefenseIndex as _ADI

    league_avg = compute_league_averages(matches)
    if league_avg is None:
        return {}
    league_avg_goals = (league_avg.avg_home_goals + league_avg.avg_away_goals) / 2

    team_ids = set()
    for m in matches:
        team_ids.add(m["home_team_id"])
        team_ids.add(m["away_team_id"])

    # 1회차: 원시 득점 기반 (STEP 2 로직 재사용)
    from backtest.team_strength import compute_all_team_indices
    indices = compute_all_team_indices(matches)

    for _ in range(iterations - 1):
        new_indices = {}
        for team_id in team_ids:
            team_matches = [m for m in matches if m["home_team_id"] == team_id or m["away_team_id"] == team_id]
            adjusted = adjust_matches_for_opponent_strength(team_matches, team_id, indices)
            averaged = _weighted_average_from_adjusted(adjusted)
            if averaged is not None:
                avg_scored, avg_conceded = averaged
                new_indices[team_id] = _ADI(
                    team_id=team_id,
                    attack_index=avg_scored / league_avg_goals,
                    defense_index=avg_conceded / league_avg_goals,
                    games=len(adjusted),
                )
        if new_indices:
            indices = new_indices

    return indices
