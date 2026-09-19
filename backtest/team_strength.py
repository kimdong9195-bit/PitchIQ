"""
backtest/team_strength.py - STEP 2: 팀 공격력/수비력 계산 (베이스라인)

이 단계에서는 일부러 아무 보정도 안 넣는다:
- 시간 감쇠 없음 (모든 경기를 동일 가중치로 취급) -> STEP 3에서 추가
- 선수단/감독 연속성 보정 없음 -> STEP 4에서 추가
- 상대팀 강도 보정 없음 -> STEP 5에서 추가
- 장기전력/현재컨디션 구분 없음 -> STEP 8에서 추가

즉 지금은 "주어진 경기 목록에서 단순 평균으로 공격력/수비력을 뽑는 함수"만
만든다. 다음 단계들은 이 함수에 들어가는 "경기 목록"과 "가중치"를 점점
정교하게 만드는 방식으로 쌓아올라간다 - 이 함수 자체의 계산 로직은 이후에도
재사용된다.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TeamAverages:
    team_id: int
    games: int
    avg_scored: float
    avg_conceded: float


@dataclass
class LeagueAverages:
    avg_home_goals: float
    avg_away_goals: float
    games: int


@dataclass
class AttackDefenseIndex:
    team_id: int
    attack_index: float   # 1.0 = 리그 평균 수준. 1.3이면 평균보다 30% 더 넣는 팀
    defense_index: float  # 1.0 = 평균. 1.3이면 평균보다 30% 더 실점하는 팀 (수비가 약함)
    games: int             # 이 지수를 계산한 표본 경기 수 (표본이 적으면 신뢰도 낮음)


def compute_team_averages(matches: List[dict], team_id: int) -> Optional[TeamAverages]:
    """
    주어진 경기 목록에서 특정 팀의 평균 득점/실점을 계산한다.
    각 경기 dict에 'time_weight' 키가 있으면 가중평균, 없으면 단순평균
    (STEP 2에서는 없는 상태로 쓰고, STEP 3부터는 apply_time_weights()를
    먼저 거친 경기 목록을 넣어서 시간 가중치가 반영되게 한다).
    """
    scored = []
    conceded = []
    weights = []
    for m in matches:
        w = m.get("time_weight", 1.0)
        if m["home_team_id"] == team_id:
            scored.append(m["home_goals"])
            conceded.append(m["away_goals"])
            weights.append(w)
        elif m["away_team_id"] == team_id:
            scored.append(m["away_goals"])
            conceded.append(m["home_goals"])
            weights.append(w)

    if not scored:
        return None

    total_weight = sum(weights)
    if total_weight == 0:
        return None  # 전부 가중치 0 (예: half-life에 비해 너무 오래된 경기들뿐) - 데이터 부족으로 처리

    weighted_scored = sum(s * w for s, w in zip(scored, weights)) / total_weight
    weighted_conceded = sum(c * w for c, w in zip(conceded, weights)) / total_weight

    return TeamAverages(
        team_id=team_id,
        games=len(scored),
        avg_scored=weighted_scored,
        avg_conceded=weighted_conceded,
    )


def compute_league_averages(matches: List[dict]) -> Optional[LeagueAverages]:
    """리그 전체의 홈/원정 평균 득점 (팀 지수를 계산할 때 기준선으로 쓴다). 가중치 있으면 반영."""
    if not matches:
        return None

    weights = [m.get("time_weight", 1.0) for m in matches]
    total_weight = sum(weights)
    if total_weight == 0:
        return None

    home_goals = sum(m["home_goals"] * w for m, w in zip(matches, weights)) / total_weight
    away_goals = sum(m["away_goals"] * w for m, w in zip(matches, weights)) / total_weight

    return LeagueAverages(
        avg_home_goals=home_goals,
        avg_away_goals=away_goals,
        games=len(matches),
    )


def compute_attack_defense_index(
    team_averages: TeamAverages, league_averages: LeagueAverages
) -> AttackDefenseIndex:
    """
    팀의 득점/실점을 "리그 평균 골"과 비교해 지수화한다.
    리그 평균 골 = (홈 평균 + 원정 평균) / 2 로, 홈/원정 편향을 상쇄한 값을 기준선으로 쓴다.
    """
    league_avg_goals = (league_averages.avg_home_goals + league_averages.avg_away_goals) / 2

    return AttackDefenseIndex(
        team_id=team_averages.team_id,
        attack_index=team_averages.avg_scored / league_avg_goals,
        defense_index=team_averages.avg_conceded / league_avg_goals,
        games=team_averages.games,
    )


def compute_team_venue_averages(matches: List[dict], team_id: int, venue: str) -> Optional[TeamAverages]:
    """
    compute_team_averages()와 같은 방식이지만, 홈경기만 또는 원정경기만 골라서 계산한다.
    venue: "home" 이면 이 팀이 홈이었던 경기만, "away"면 원정이었던 경기만.

    STEP 7(예상득점 모델)에서 홈 어드밴티지를 제대로 다루려면 "이 팀이 홈일 때의
    득점력"과 "원정일 때의 득점력"을 구분해야 하므로 새로 추가한 함수다.
    (기존 compute_team_averages()는 홈/원정을 합쳐서 계산하며, STEP 2의 단순
    공격/수비 지수나 STEP 5의 상대팀 강도 참조용으로는 계속 그대로 쓴다.)
    """
    if venue not in ("home", "away"):
        raise ValueError("venue는 'home' 또는 'away'여야 합니다")

    scored, conceded, weights = [], [], []
    for m in matches:
        w = m.get("time_weight", 1.0)
        if venue == "home" and m["home_team_id"] == team_id:
            scored.append(m["home_goals"])
            conceded.append(m["away_goals"])
            weights.append(w)
        elif venue == "away" and m["away_team_id"] == team_id:
            scored.append(m["away_goals"])
            conceded.append(m["home_goals"])
            weights.append(w)

    if not scored:
        return None

    total_weight = sum(weights)
    if total_weight == 0:
        return None

    return TeamAverages(
        team_id=team_id,
        games=len(scored),
        avg_scored=sum(s * w for s, w in zip(scored, weights)) / total_weight,
        avg_conceded=sum(c * w for c, w in zip(conceded, weights)) / total_weight,
    )


def compute_all_team_indices(matches: List[dict]) -> dict:
    """
    주어진 경기 목록에 등장하는 모든 팀의 공격/수비 지수를 한 번에 계산.
    반환: {team_id: AttackDefenseIndex}
    """
    league_avg = compute_league_averages(matches)
    if league_avg is None:
        return {}

    team_ids = set()
    for m in matches:
        team_ids.add(m["home_team_id"])
        team_ids.add(m["away_team_id"])

    result = {}
    for team_id in team_ids:
        team_avg = compute_team_averages(matches, team_id)
        if team_avg is not None:
            result[team_id] = compute_attack_defense_index(team_avg, league_avg)

    return result
