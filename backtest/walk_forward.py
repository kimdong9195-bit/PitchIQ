"""
backtest/walk_forward.py - STEP 10: Time-forward Walk-forward Backtesting

핵심 원칙 (반드시 지킨다):
1. 각 예측 시점 T에서, match_date < T인 데이터만 사용한다.
   (schema.get_matches_before가 이걸 강제한다 - 이미 단위테스트로 검증됨)
2. Continuity 계산에 쓰는 라인업은 반드시 schema.get_most_recent_lineup_before()로만
   가져온다. 예측 대상 fixture의 실제 라인업(schema.get_lineup(target_fixture_id, ...))은
   이 파일 어디에서도 호출하지 않는다 - 코드 검토 시 이 규칙이 지켜지는지
   확인할 수 있도록 주석으로 명시한다.
3. 랜덤 train/test 분할을 쓰지 않는다. 오직 날짜 순서대로만 진행한다.

중요한 근사(명시적으로 남겨둠, 스펙 요구사항):
    "Historical opponent strength is calculated using all information
    available up to the prediction date, rather than recursively
    reconstructing the opponent strength that would have been known
    before each historical match."
    이건 예측 대상(target) 경기에 대한 데이터 누수는 아니지만, 과거 경기
    하나하나를 정규화할 때 "그 경기 시점에는 몰랐을, 그러나 오늘(예측
    시점)까지는 이미 일어난" 같은 시즌 내 다른 경기 정보가 상대팀 지수에
    섞여 들어가는 형태의 근사다 (feature construction의 look-ahead 성격).
    실제 계산은 backtest/expected_goals.py의 compute_expected_goals()가
    backtest/opponent_strength.py의 compute_iterative_team_indices()를
    직접 호출하는 방식으로 되어 있다. V2에서 완전 incremental 방식(과거
    경기마다 그 시점 이전 데이터로만 상대팀 지수를 다시 계산)으로 바꾸려면,
    opponent_strength.py에 새 함수를 추가하고 expected_goals.py의 해당
    import/호출부만 바꾸면 된다.
"""

from typing import List, Optional

from backtest import schema
from backtest.expected_goals import compute_expected_goals
from backtest.scoring_models.base import BaseScoringModel


def _build_leakage_free_lineups(home_team_id: int, away_team_id: int, as_of_date: str) -> dict:
    """
    continuity 계산용 '현재 라인업'을 반드시 이 함수로만 만든다.
    schema.get_lineup(target_fixture_id, ...)는 여기서도, 다른 어디서도
    호출하지 않는다 - get_most_recent_lineup_before만 쓴다 (그 시점 이전에
    실제로 있었던 마지막 경기 라인업). 못 찾으면 빈 dict를 반환하고,
    그러면 expected_goals.py가 자동으로 continuity를 중립(1.0)으로 처리한다
    (continuity_mode="none"과 동일한 효과 - 별도 분기 처리 필요 없음).
    """
    result = {}
    for team_id in (home_team_id, away_team_id):
        info = schema.get_most_recent_lineup_before(team_id, before_date=as_of_date)
        if info:
            result[team_id] = info
    return result


def _lineup_lookup_factory(as_of_date: str):
    """
    expected_goals.py 내부에서 '과거 경기 하나하나'의 라인업을 조회할 때 쓰는
    콜백. 이것도 과거 경기(m['fixture_id'])의 라인업을 그대로 쓰는 거라
    leakage가 아니다 (그 경기는 이미 끝난 과거 경기이므로 그 경기 자체의
    라인업을 아는 건 문제없음 - continuity 비교의 대상이지 예측 대상이 아님).
    """
    def _lookup(fixture_id: int, team_id: int):
        lineup = schema.get_lineup(fixture_id, team_id)
        if not lineup:
            return None
        import json
        return {
            "player_ids": json.loads(lineup["starting_player_ids"]),
            "coach_name": lineup["coach_name"],
        }
    return _lookup


def predict_one_match(
    fixture: dict,
    league_id: int,
    season: int,
    params: dict,
    model: BaseScoringModel,
    ou_lines: List[float] = (2.5,),
    handicap_lines: List[float] = (-1.5, 1.5),
) -> Optional[dict]:
    """
    경기 하나를 예측하고, 요청된 모든 필드를 담은 기록(dict)을 반환한다.
    데이터 부족 등으로 예측 자체가 불가능하면 None을 반환한다.
    """
    as_of_date = fixture["match_date"]
    home_id, away_id = fixture["home_team_id"], fixture["away_team_id"]

    from backtest import cache
    current_season_matches = cache.cached_get_matches_before(league_id, season, before_date=as_of_date)
    previous_season_matches = [
        m for m in cache.cached_get_all_matches(league_id, season - 1) if m["match_date"] < as_of_date
    ]

    current_lineups = _build_leakage_free_lineups(home_id, away_id, as_of_date)
    lineup_lookup = _lineup_lookup_factory(as_of_date)

    eg_result = compute_expected_goals(
        home_id, away_id, current_season_matches, previous_season_matches,
        as_of_date, params, lineup_lookup, current_lineups,
    )
    if eg_result.get("lambda_home") is None:
        return None

    lambda_home, lambda_away = eg_result["lambda_home"], eg_result["lambda_away"]

    from backtest.scoring_models.poisson_dc import validate_rho_nonnegative
    rho = params.get("rho", -0.13)
    rho_safe = validate_rho_nonnegative(rho, lambda_home, lambda_away)

    score_matrix = model.score_matrix(lambda_home, lambda_away, params)
    diagnostic_had_clip = getattr(model, "last_had_negative_clip", None)
    diagnostic_max_goals = getattr(model, "last_max_goals_used", None)
    diagnostic_tail_prob = getattr(model, "last_tail_probability", None)

    from backtest.markets import market_1x2, market_btts, market_over_under, market_handicap, market_home_goals, market_away_goals
    market_1x2_result = market_1x2(score_matrix)
    market_btts_result = market_btts(score_matrix)
    market_ou_result = {line: market_over_under(score_matrix, line) for line in ou_lines}
    market_handicap_result = {line: market_handicap(score_matrix, line) for line in handicap_lines}
    market_home_goals_result = market_home_goals(score_matrix)
    market_away_goals_result = market_away_goals(score_matrix)

    record = {
        "fixture_id": fixture["fixture_id"],
        "match_date": as_of_date,
        "home_team": fixture.get("home_team_name"),
        "away_team": fixture.get("away_team_name"),
        "home_team_id": home_id,
        "away_team_id": away_id,

        "predicted_lambda_home": lambda_home,
        "predicted_lambda_away": lambda_away,

        "selected_model_variant": model.name,
        "selected_half_life": params.get("half_life"),
        "selected_continuity_mode": params.get("continuity_mode"),
        "selected_opponent_strength_mode": params.get("final_opponent_adjustment_mode"),
        "selected_opponent_iterations": params.get("opponent_strength_iterations"),
        "selected_season_k": params.get("season_blend_k"),
        "selected_home_advantage": params.get("home_advantage"),
        "selected_rho": rho,
        "rho_nonnegative_ok": rho_safe,
        "diagnostic_negative_clip_occurred": diagnostic_had_clip,
        "diagnostic_max_goals_used": diagnostic_max_goals,
        "diagnostic_tail_probability": diagnostic_tail_prob,

        "actual_home_goals": fixture["home_goals"],
        "actual_away_goals": fixture["away_goals"],

        "market_1x2": market_1x2_result,
        "market_btts": market_btts_result,
        "market_over_under": market_ou_result,
        "market_handicap": market_handicap_result,
        "market_home_goals": market_home_goals_result,
        "market_away_goals": market_away_goals_result,
    }
    return record


def run_walk_forward_backtest(
    league_id: int,
    seasons: List[int],
    params: dict,
    model: BaseScoringModel,
    ou_lines: List[float] = (2.5,),
    handicap_lines: List[float] = (-1.5, 1.5),
) -> List[dict]:
    """
    주어진 시즌들을 날짜순으로 쭉 훑으면서 매 경기를 예측하고 기록한다.
    랜덤 셔플 없음 - 리스트 자체를 날짜순 정렬해서 순회한다.
    """
    records = []
    for season in seasons:
        from backtest import cache
        matches = cache.cached_get_all_matches(league_id, season)
        matches_sorted = sorted(matches, key=lambda m: m["match_date"])
        for fixture in matches_sorted:
            record = predict_one_match(fixture, league_id, season, params, model, ou_lines, handicap_lines)
            if record is not None:
                records.append(record)
    return records
