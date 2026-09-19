"""
backtest/expected_goals.py - STEP 7: 예상 득점(λ_home, λ_away) 계산

이 함수는 모델(Poisson이든 Negative Binomial이든)과 무관하게 "기대득점
숫자 두 개"를 만드는 역할만 한다. 스코어 확률로 바꾸는 건 scoring_models/가
담당한다 (STEP 7과 STEP 8의 책임 분리).

설계 결정 (사용자 확인 완료):
- Time Decay(STEP 3)는 "같은 시즌 안에서만" 적용한다. 시즌 경계를 넘어
  감쇠시키지 않는다 - 그 역할은 season_blend(STEP 6)가 별도로 담당한다.
- 홈/원정 득점력을 분리해서 계산한다 (team_strength.compute_team_venue_averages).
- 상대팀 강도 보정(STEP 5)은 각 시즌의 리그 지수를 기준으로, 시즌 내
  평균을 내기 전에 개별 경기 단위로 적용한다.
- 선수단/감독 연속성(STEP 4)은 시간가중치와 곱해져서 "이 과거 경기를
  얼마나 신뢰할지"를 결정하는 하나의 최종 가중치가 된다:
      final_weight = time_weight × continuity_multiplier
- 홈 어드밴티지는 후보 리스트를 갖는 별도의 곱셈 계수로 취급한다
  (HOME_ADVANTAGE_CANDIDATES - 이전엔 1.15로 고정되어 있던 값을
  최적화 대상으로 전환).
"""

from typing import List, Optional

from backtest import team_strength
from backtest.continuity import compute_continuity_multiplier
from backtest.opponent_strength import adjust_goals_for_opponent_strength
from backtest.season_blend import blend_team_averages
from backtest.team_strength import TeamAverages
from backtest.time_decay import apply_time_weights

FINAL_OPPONENT_MODE_CANDIDATES = ["venue_specific", "unified_index"]  # 모델 A/B는 iterations로, 모델 C는 이걸로 비교
HOME_ADVANTAGE_CANDIDATES = [1.0, 1.10, 1.15, 1.20, 1.30]  # 백테스트로 최적값 탐색 대상


def _get_unified_index(team_id: int, current_indices: dict, previous_indices: dict):
    """오늘 상대의 '통합(venue 구분 없는) STEP5 지수'를 가져온다. 없으면 None(중립 처리)."""
    return current_indices.get(team_id) or previous_indices.get(team_id)


CONTINUITY_MODE_CANDIDATES = ["weighted", "post_hoc", "none"]  # "none" = continuity 자체를 안 쓰는 베이스라인


def _weighted_venue_average(
    matches: List[dict],
    team_id: int,
    venue: str,
    opponent_indices: dict,
    half_life: float,
    as_of_date: str,
    squad_floor: float,
    manager_penalty: float,
    current_lineup_ids: Optional[List[int]],
    current_coach: Optional[str],
    lineup_lookup,
    continuity_mode: str = "weighted",
    neutral_scored_prior: Optional[float] = None,
    neutral_conceded_prior: Optional[float] = None,
) -> Optional[TeamAverages]:
    """
    한 시즌 안에서, 특정 팀의 특정 venue(home/away) 경기들을 갖고
    상대팀 강도 보정(STEP5) + 시간가중치×연속성(STEP3+STEP4)을 다 반영한
    가중평균을 계산한다.

    continuity_mode:
      "weighted"  (방식 A) - 선수단/감독 연속성을 과거 경기 하나하나의
                  가중치에 곱해서, 연속성 낮은 경기는 평균에 덜 반영되게 한다.
                  final_weight = time_weight × continuity_multiplier
      "post_hoc"  (방식 B) - 평균 자체는 time_weight만으로 계산하고, 그 뒤에
                  "이 표본 전체가 지금 선수단/감독과 평균적으로 얼마나
                  비슷했는지"(aggregate_continuity)를 하나만 구해서,
                  최종 평균을 리그 중립값 쪽으로 얼마나 끌어당길지 정하는
                  수축(shrinkage) 계수로 쓴다:
                      adjusted = raw_avg × aggregate_continuity
                               + neutral_prior × (1 - aggregate_continuity)
                  (raw_avg에 continuity를 그냥 곱하기만 하면 0 쪽으로 쏠려버려
                  통계적으로 이상해지므로, "모르면 리그 평균으로 되돌아간다"는
                  중립점을 쓴다 - STEP 4의 floor 개념과 같은 발상.)
      "none"      (베이스라인) - continuity를 아예 안 본다 (라인업 조회도
                  안 하고 time_weight만 사용). continuity 자체가 실제로
                  예측력을 더해주는지 확인하려면 이 베이스라인과 반드시
                  비교해야 한다 ("일단 넣는 게 낫다"고 가정하지 않는다).

    어느 방식이 실제로 더 정확한지는 STEP 10~11 백테스트가 판단한다.
    """
    venue_matches = [
        m for m in matches
        if (venue == "home" and m["home_team_id"] == team_id)
        or (venue == "away" and m["away_team_id"] == team_id)
    ]
    if not venue_matches:
        return None

    time_weighted = apply_time_weights(venue_matches, as_of_date, half_life)

    prepared = []
    continuity_values = []
    base_weights = []
    for m in time_weighted:
        opponent_id = m["away_team_id"] if venue == "home" else m["home_team_id"]
        scored = m["home_goals"] if venue == "home" else m["away_goals"]
        conceded = m["away_goals"] if venue == "home" else m["home_goals"]

        adj_scored, adj_conceded = adjust_goals_for_opponent_strength(
            scored, conceded, opponent_indices.get(opponent_id)
        )

        if continuity_mode == "none":
            # 베이스라인: 선수단/감독 연속성을 아예 안 본다 (라인업 조회조차 안 함).
            # continuity가 실제로 예측력을 더해주는지 확인하려면 이 베이스라인과
            # 비교해야 한다 - "무조건 넣는 게 낫다"고 가정하지 않는다.
            continuity = 1.0
        else:
            lineup_info = lineup_lookup(m["fixture_id"], team_id)
            squad_continuity = 1.0
            manager_same = 1.0
            if lineup_info and current_lineup_ids:
                from backtest.continuity import compute_squad_continuity, compute_manager_continuity
                squad_continuity = compute_squad_continuity(lineup_info.get("player_ids") or [], current_lineup_ids)
                manager_same = compute_manager_continuity(lineup_info.get("coach_name"), current_coach)
            continuity = compute_continuity_multiplier(squad_continuity, manager_same, squad_floor, manager_penalty)

        if continuity_mode == "weighted":
            final_weight = m["time_weight"] * continuity
        elif continuity_mode == "post_hoc":
            final_weight = m["time_weight"]
            continuity_values.append(continuity)
            base_weights.append(m["time_weight"])
        else:  # "none" - 베이스라인, continuity 자체를 적용하지 않음
            final_weight = m["time_weight"]

        prepared.append({
            "home_team_id": m["home_team_id"],
            "away_team_id": m["away_team_id"],
            "home_goals": adj_scored if venue == "home" else adj_conceded,
            "away_goals": adj_conceded if venue == "home" else adj_scored,
            "time_weight": final_weight,
        })

    raw_avg = team_strength.compute_team_venue_averages(prepared, team_id, venue)
    if raw_avg is None:
        return None

    if continuity_mode == "post_hoc" and continuity_values:
        total_base_weight = sum(base_weights)
        aggregate_continuity = (
            sum(c * w for c, w in zip(continuity_values, base_weights)) / total_base_weight
            if total_base_weight > 0 else 1.0
        )
        neutral_scored = neutral_scored_prior if neutral_scored_prior is not None else raw_avg.avg_scored
        neutral_conceded = neutral_conceded_prior if neutral_conceded_prior is not None else raw_avg.avg_conceded
        return TeamAverages(
            team_id=team_id,
            games=raw_avg.games,
            avg_scored=raw_avg.avg_scored * aggregate_continuity + neutral_scored * (1 - aggregate_continuity),
            avg_conceded=raw_avg.avg_conceded * aggregate_continuity + neutral_conceded * (1 - aggregate_continuity),
        )

    return raw_avg


def compute_expected_goals(
    home_team_id: int,
    away_team_id: int,
    current_season_matches: List[dict],
    previous_season_matches: List[dict],
    as_of_date: str,
    params: dict,
    lineup_lookup,
    current_lineups: dict,
) -> dict:
    """
    STEP 2~6을 전부 결합해서 최종 lambda_home, lambda_away를 계산한다.

    params 딕셔너리로 받는 하이퍼파라미터 (전부 후보 리스트 중 하나가 여기 들어옴 - STEP 11 대상):
        half_life, squad_continuity_floor, manager_change_penalty,
        season_blend_k, home_advantage, opponent_strength_iterations,
        continuity_mode ("weighted" | "post_hoc"),
        final_opponent_adjustment_mode ("venue_specific" | "unified_index")
    """
    half_life = params["half_life"]
    squad_floor = params["squad_continuity_floor"]
    manager_penalty = params["manager_change_penalty"]
    k = params["season_blend_k"]
    home_advantage = params["home_advantage"]
    opponent_iterations = params.get("opponent_strength_iterations", 1)
    continuity_mode = params.get("continuity_mode", "weighted")
    final_opponent_mode = params.get("final_opponent_adjustment_mode", "venue_specific")

    # STEP 5의 "상대팀 강도"는 시즌 전체를 참조하며, 반복 횟수(0~3)는
    # params로 받아 STEP 10~11 백테스트가 실제 오차로 비교/선택하게 한다.
    from backtest.opponent_strength import compute_iterative_team_indices
    current_indices = compute_iterative_team_indices(current_season_matches, iterations=opponent_iterations)
    previous_indices = compute_iterative_team_indices(previous_season_matches, iterations=opponent_iterations)

    # post_hoc continuity 모드에서 "모르면 돌아갈 중립값"으로 쓸 리그 평균을
    # 먼저 구해둔다 (연속성 정보가 없을 때 0으로 쏠리지 않고 리그 평균으로
    # 되돌아가게 하기 위함 - STEP4 floor와 같은 발상).
    league_avg_for_prior = team_strength.compute_league_averages(current_season_matches) \
        or team_strength.compute_league_averages(previous_season_matches)

    home_current_lineup = current_lineups.get(home_team_id, {})
    away_current_lineup = current_lineups.get(away_team_id, {})

    def venue_avg(team_id, venue, matches, opponent_indices, current_lineup):
        if league_avg_for_prior:
            neutral_scored = league_avg_for_prior.avg_home_goals if venue == "home" else league_avg_for_prior.avg_away_goals
            neutral_conceded = league_avg_for_prior.avg_away_goals if venue == "home" else league_avg_for_prior.avg_home_goals
        else:
            neutral_scored = neutral_conceded = None
        return _weighted_venue_average(
            matches, team_id, venue, opponent_indices, half_life, as_of_date,
            squad_floor, manager_penalty,
            current_lineup.get("player_ids"), current_lineup.get("coach_name"),
            lineup_lookup, continuity_mode, neutral_scored, neutral_conceded,
        )

    home_current = venue_avg(home_team_id, "home", current_season_matches, current_indices, home_current_lineup)
    home_previous = venue_avg(home_team_id, "home", previous_season_matches, previous_indices, home_current_lineup)
    home_blended = blend_team_averages(home_current, home_previous, k)

    away_current = venue_avg(away_team_id, "away", current_season_matches, current_indices, away_current_lineup)
    away_previous = venue_avg(away_team_id, "away", previous_season_matches, previous_indices, away_current_lineup)
    away_blended = blend_team_averages(away_current, away_previous, k)

    if home_blended is None or away_blended is None:
        return {"lambda_home": None, "lambda_away": None, "reason": "표본 데이터 부족"}

    league_avg = league_avg_for_prior
    if league_avg is None:
        return {"lambda_home": None, "lambda_away": None, "reason": "리그 평균 계산 불가 (경기 데이터 없음)"}
    if league_avg.avg_home_goals <= 0 or league_avg.avg_away_goals <= 0:
        # 시즌 극초반 표본이 너무 적어서 리그 평균이 0이 되는 극단적 경우 -
        # 0으로 나누면 크래시나거나 무의미하게 큰 값이 나오므로, 임의로
        # 보정하지 않고 "아직 예측 불가"로 명시적으로 처리한다.
        return {"lambda_home": None, "lambda_away": None, "reason": "리그 평균이 0 - 표본 부족으로 예측 불가"}

    # 주의(중요): 원래 수식은 (H_scored/LA_home) x (A_conceded/LA_away) x LA_home x HA
    # 였는데, LA_home으로 나눴다가 바로 다시 곱하는 부분이 대수적으로 완전히
    # 상쇄된다 (검증 결과, 사용자 확인). 그래서 아래처럼 상쇄된 형태로 정리했다 -
    # 숫자 결과는 이전과 완전히 동일하고, 코드만 더 명확해졌다.
    #
    # 이 정리 과정에서 드러난 사실: H_scored는 애초에 "홈경기만" 모은 값이라
    # 팀별 홈효과가 이미 실려있다. 그 위에 HA를 또 곱하면 홈효과를 중복
    # 반영할 가능성이 있다 - HOME_ADVANTAGE_CANDIDATES에 1.0(효과 없음)이
    # 포함되어 있으므로, 최적화 결과 HA≈1.0으로 수렴하면 이 중복 가설이
    # 맞다는 뜻이다. 이론만으로 단정하지 않고 백테스트로 확인한다.
    #
    # final_opponent_adjustment_mode:
    #   "venue_specific" (모델 A/B) - 오늘 상대의 venue별 시즌결합 평균을 씀 (기존 방식)
    #   "unified_index"  (모델 C)   - 오늘 상대의 STEP5 통합 지수를 씀 (과거 정규화에
    #                     쓴 것과 동일한 지표를 일관되게 재사용 - 내적 일관성 비교용)
    # 주의(중요, 실사용자 검증으로 발견/수정된 버그): 원래는
    # away_defense_factor = away_blended.avg_conceded / league_avg.avg_away_goals
    # 였는데 이게 틀렸다. away_blended.avg_conceded(원정팀이 원정에서 먹는 실점)는
    # 사실 "그 원정팀이 만난 홈팀들이 넣은 골"과 같은 모집단이라, 비교 기준은
    # LA_away가 아니라 LA_home이어야 한다 (같은 성질의 숫자끼리 정규화해야 함).
    # 검증: 두 팀이 완전히 리그평균 수준(H_scored=LA_home, A_conceded=LA_home)이면
    #   수정 전: lambda_home = LA_home x (LA_home/LA_away) x HA  -> LA_home x HA가 아님 (버그)
    #   수정 후: lambda_home = LA_home x (LA_home/LA_home) x HA = LA_home x HA (정상)
    # λ_away 쪽은 원래부터 H_conceded/LA_away 조합이 올바른 모집단 매칭이라 안 건드림.
    if final_opponent_mode == "unified_index":
        away_defense_ref = _get_unified_index(away_team_id, current_indices, previous_indices)
        home_defense_ref = _get_unified_index(home_team_id, current_indices, previous_indices)
        away_defense_factor = away_defense_ref.defense_index if away_defense_ref else 1.0
        home_defense_factor = home_defense_ref.defense_index if home_defense_ref else 1.0
    else:
        away_defense_factor = away_blended.avg_conceded / league_avg.avg_home_goals  # 수정: LA_away -> LA_home
        home_defense_factor = home_blended.avg_conceded / league_avg.avg_away_goals  # 원래부터 맞음

    lambda_home = home_blended.avg_scored * away_defense_factor * home_advantage
    lambda_away = away_blended.avg_scored * home_defense_factor

    return {
        "lambda_home": lambda_home,
        "lambda_away": lambda_away,
        "home_sample_games": home_blended.games,
        "away_sample_games": away_blended.games,
    }
