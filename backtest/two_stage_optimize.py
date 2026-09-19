"""
backtest/two_stage_optimize.py - 2단계 최적화

STEP A: 구조적 선택(continuity_mode x opponent 방식 x 반복횟수)을
        작은 대표 파라미터 프리셋 3개로 먼저 스크리닝한다.
STEP B: STEP A 상위 구조에서만 연속형 파라미터(half_life, K, HA, rho 등)
        전체 그리드서치를 돌린다. 탈락한 구조도 대표 조합 몇 개로
        교차검증해서, 혹시 특정 파라미터와 만났을 때 역전되는지 확인한다
        (순수 그리디로 인한 최적해 누락을 막기 위함).

절대 규칙: 여기 있는 어떤 함수도 FINAL_VALIDATION_SEASON(2025)을 인자로
받지 않는다. train_seasons는 항상 OPTIMIZATION_SEASONS([2023,2024])여야
하고, 이건 호출하는 쪽(스크립트)이 지켜야 하는 계약이다.
"""

import itertools
from typing import List

from backtest import metrics
from backtest.scoring_models.base import BaseScoringModel
from backtest.walk_forward import run_walk_forward_backtest

CONTINUITY_STRUCTURES = ["weighted", "post_hoc", "none"]


def enumerate_opponent_structures() -> List[dict]:
    structures = [{"opponent_strength_iterations": 0, "final_opponent_adjustment_mode": "venue_specific", "label": "O2"}]
    for iterations in [1, 2, 3]:
        structures.append({"opponent_strength_iterations": iterations, "final_opponent_adjustment_mode": "venue_specific", "label": f"O1(iter={iterations})"})
        structures.append({"opponent_strength_iterations": iterations, "final_opponent_adjustment_mode": "unified_index", "label": f"O3(iter={iterations})"})
    return structures


def enumerate_structural_configs() -> List[dict]:
    configs = []
    for continuity_mode in CONTINUITY_STRUCTURES:
        for opp in enumerate_opponent_structures():
            configs.append({
                "continuity_mode": continuity_mode,
                "opponent_strength_iterations": opp["opponent_strength_iterations"],
                "final_opponent_adjustment_mode": opp["final_opponent_adjustment_mode"],
                "label": f"C({continuity_mode})+{opp['label']}",
            })
    return configs


PRESET_LOW = {"half_life": 60, "season_blend_k": 5, "home_advantage": 1.0,
              "rho": -0.05, "squad_continuity_floor": 0.5, "manager_change_penalty": 0.6}
PRESET_MID = {"half_life": 90, "season_blend_k": 10, "home_advantage": 1.15,
              "rho": -0.13, "squad_continuity_floor": 0.7, "manager_change_penalty": 0.8}
PRESET_HIGH = {"half_life": 150, "season_blend_k": 15, "home_advantage": 1.25,
               "rho": -0.20, "squad_continuity_floor": 0.9, "manager_change_penalty": 0.9}
STEP_A_PRESETS = [PRESET_LOW, PRESET_MID, PRESET_HIGH]

PROBABILISTIC_MARKETS_FOR_AGGREGATE = ["1x2", "btts", "over_under_2.5", "handicap_-1.5"]


def evaluate_records(records: list) -> dict:
    """
    walk-forward 기록 리스트에서 시장별 Log Loss/Brier + 골 MAE/RMSE를 계산.
    STEP A/B 어디서든 이 함수 하나로만 채점해서 공정성을 보장한다.
    """
    if not records:
        return None

    outcome_1x2 = []
    for r in records:
        h, a = r["actual_home_goals"], r["actual_away_goals"]
        outcome_1x2.append("home_win" if h > a else ("draw" if h == a else "away_win"))
    probs_1x2 = [r["market_1x2"] for r in records]
    ll_1x2 = metrics.multiclass_log_loss(probs_1x2, outcome_1x2)
    brier_1x2 = metrics.multiclass_brier_score(probs_1x2, outcome_1x2, ["home_win", "draw", "away_win"])

    btts_actual = [1 if (r["actual_home_goals"] >= 1 and r["actual_away_goals"] >= 1) else 0 for r in records]
    btts_pred = [r["market_btts"]["yes"] for r in records]
    ll_btts = metrics.log_loss(btts_pred, btts_actual)
    brier_btts = metrics.brier_score(btts_pred, btts_actual)

    ou_actual = [1 if (r["actual_home_goals"] + r["actual_away_goals"]) > 2.5 else 0 for r in records]
    ou_pred = [r["market_over_under"].get(2.5, {}).get("over", 0.5) for r in records]
    ll_ou = metrics.log_loss(ou_pred, ou_actual)
    brier_ou = metrics.brier_score(ou_pred, ou_actual)

    hcap_actual = []
    hcap_pred = []
    for r in records:
        diff = r["actual_home_goals"] - r["actual_away_goals"]
        adjusted = diff + (-1.5)
        hcap_actual.append(1 if adjusted > 0 else 0)
        hcap = r["market_handicap"].get(-1.5, {})
        hcap_pred.append(hcap.get("home_cover_equity", 0.5))
    ll_hcap = metrics.log_loss(hcap_pred, hcap_actual)
    brier_hcap = metrics.brier_score(hcap_pred, hcap_actual)

    home_goal_pred = [r["predicted_lambda_home"] for r in records]
    home_goal_actual = [r["actual_home_goals"] for r in records]
    away_goal_pred = [r["predicted_lambda_away"] for r in records]
    away_goal_actual = [r["actual_away_goals"] for r in records]

    return {
        "n_predictions": len(records),
        "1x2": {"log_loss": ll_1x2, "brier": brier_1x2},
        "btts": {"log_loss": ll_btts, "brier": brier_btts},
        "over_under_2.5": {"log_loss": ll_ou, "brier": brier_ou},
        "handicap_-1.5": {"log_loss": ll_hcap, "brier": brier_hcap},
        "home_goals": {"mae": metrics.mae(home_goal_pred, home_goal_actual), "rmse": metrics.rmse(home_goal_pred, home_goal_actual)},
        "away_goals": {"mae": metrics.mae(away_goal_pred, away_goal_actual), "rmse": metrics.rmse(away_goal_pred, away_goal_actual)},
    }


def aggregate_metric(evaluation: dict) -> float:
    """
    구조/파라미터 순위를 매기는 단일 집계지표.
    정의: 4개 확률형 시장(1X2/BTTS/OU2.5/핸디캡-1.5)의 Log Loss 단순평균.
    (같은 단위끼리만 평균 - 골 MAE/RMSE는 다른 단위라 여기 안 섞고 항상
    별도로 같이 보고한다). 낮을수록 좋음.
    """
    if evaluation is None:
        return float("inf")
    values = [evaluation[m]["log_loss"] for m in PROBABILISTIC_MARKETS_FOR_AGGREGATE if evaluation[m]["log_loss"] is not None]
    if not values:
        return float("inf")
    return sum(values) / len(values)


TOP_N_FOR_FULL_GRID = 3  # 확정: 구조적 강건성 우선 (속도보다). 상위 3개 구조에 STEP B 풀그리드 적용
SPOT_CHECK_PRESET_COUNT = 3  # 탈락 구조 교차검증에 쓸 프리셋 수


def _assert_not_final_validation_season(train_seasons: List[int]):
    """
    2025-26(FINAL_VALIDATION_SEASON)이 실수로라도 최적화에 섞이는 걸 막는
    마지막 방어선. STEP A/B의 모든 진입점에서 이 함수를 제일 먼저 호출한다.
    """
    from backtest.season_split import FINAL_VALIDATION_SEASON
    if FINAL_VALIDATION_SEASON in train_seasons:
        raise ValueError(
            f"train_seasons에 FINAL_VALIDATION_SEASON({FINAL_VALIDATION_SEASON})이 "
            f"포함되어 있습니다. 최종검증 시즌은 파라미터 선택에 절대 쓰면 안 됩니다."
        )


def run_two_stage_optimization(league_id: int, train_seasons: List[int], model: BaseScoringModel, rho_candidates: list, n_workers: int = None) -> dict:
    """
    STEP A -> 상위 TOP_N_FOR_FULL_GRID개 구조에 STEP B 풀그리드(병렬처리) ->
    나머지는 교차검증 -> 역전되는 구조가 있으면 그것도 풀그리드로 승격, 까지
    전부 포함한 하나의 오케스트레이션 함수. 결과는 11번 요구사항대로 항목별로
    분리해서 반환한다 (하나의 숫자로 뭉치지 않음).

    n_workers: STEP B(가장 무거운 부분)를 병렬로 돌릴 프로세스 수.
    None이면 컴퓨터의 전체 코어 수를 그대로 쓴다.
    """
    _assert_not_final_validation_season(train_seasons)
    from backtest.parallel_runner import run_step_b_full_parallel

    step_a_results = run_step_a(league_id, train_seasons, model)
    top_structures = [r["structure"] for r in step_a_results[:TOP_N_FOR_FULL_GRID]]
    eliminated_structures = [r["structure"] for r in step_a_results[TOP_N_FOR_FULL_GRID:]]

    step_b_results_by_structure = {}
    for structure in top_structures:
        step_b_results_by_structure[structure["label"]] = run_step_b_full_parallel(
            league_id, train_seasons, structure, model, rho_candidates, n_workers
        )

    current_best_aggregate = min(
        results[0]["aggregate"] for results in step_b_results_by_structure.values()
    )

    spot_check_results = spot_check_eliminated_structures(
        league_id, train_seasons, eliminated_structures, model, current_best_aggregate,
        spot_presets=STEP_A_PRESETS[:SPOT_CHECK_PRESET_COUNT],
    )

    escalated = [f for f in spot_check_results if f["beats_current_best"]]
    if escalated:
        for finding in escalated:
            structure = finding["structure"]
            step_b_results_by_structure[structure["label"]] = run_step_b_full_parallel(
                league_id, train_seasons, structure, model, rho_candidates, n_workers
            )
        current_best_aggregate = min(
            results[0]["aggregate"] for results in step_b_results_by_structure.values()
        )

    best_structure_label = min(
        step_b_results_by_structure, key=lambda label: step_b_results_by_structure[label][0]["aggregate"]
    )
    best_overall = step_b_results_by_structure[best_structure_label][0]

    return {
        "step_a_results": step_a_results,
        "top_structures": top_structures,
        "eliminated_structures": eliminated_structures,
        "step_b_results_by_structure": step_b_results_by_structure,
        "spot_check_results": spot_check_results,
        "escalated_structures": [f["structure"] for f in escalated],
        "best_structure_label": best_structure_label,
        "best_params": best_overall["params"],
        "best_evaluation": best_overall["evaluation"],
        "best_aggregate": best_overall["aggregate"],
    }
def run_step_a(league_id: int, train_seasons: List[int], model: BaseScoringModel) -> list:
    """21개 구조 x 3개 프리셋 = 63번 백테스트. 구조별 3-프리셋 평균으로 순위."""
    _assert_not_final_validation_season(train_seasons)
    structures = enumerate_structural_configs()
    results = []
    for structure in structures:
        preset_results = []
        for preset in STEP_A_PRESETS:
            params = dict(preset)
            params.update({
                "continuity_mode": structure["continuity_mode"],
                "opponent_strength_iterations": structure["opponent_strength_iterations"],
                "final_opponent_adjustment_mode": structure["final_opponent_adjustment_mode"],
            })
            records = run_walk_forward_backtest(league_id, train_seasons, params, model)
            evaluation = evaluate_records(records)
            preset_results.append({"preset": preset, "evaluation": evaluation, "aggregate": aggregate_metric(evaluation)})
        avg_aggregate = sum(r["aggregate"] for r in preset_results) / len(preset_results)
        results.append({"structure": structure, "preset_results": preset_results, "avg_aggregate": avg_aggregate})
    results.sort(key=lambda r: r["avg_aggregate"])
    return results


HALF_LIFE_CANDIDATES = [30, 60, 90, 120, 180]
K_CANDIDATES = [3, 5, 10, 15, 20]
HA_CANDIDATES = [1.0, 1.10, 1.15, 1.20, 1.30]


def step_b_param_grid(structure: dict, rho_candidates: list) -> list:
    """선택된 구조에 필요한 파라미터만 그리드로 만든다 (불필요한 축은 뺌)."""
    base_axes = {
        "half_life": HALF_LIFE_CANDIDATES,
        "season_blend_k": K_CANDIDATES,
        "home_advantage": HA_CANDIDATES,
        "rho": rho_candidates,
    }
    if structure["continuity_mode"] in ("weighted", "post_hoc"):
        from backtest.continuity import SQUAD_CONTINUITY_FLOOR_CANDIDATES, MANAGER_CHANGE_PENALTY_CANDIDATES
        base_axes["squad_continuity_floor"] = SQUAD_CONTINUITY_FLOOR_CANDIDATES
        base_axes["manager_change_penalty"] = MANAGER_CHANGE_PENALTY_CANDIDATES
    else:
        base_axes["squad_continuity_floor"] = [1.0]
        base_axes["manager_change_penalty"] = [1.0]

    keys = list(base_axes.keys())
    combos = []
    for values in itertools.product(*[base_axes[k] for k in keys]):
        combo = dict(zip(keys, values))
        combo.update({
            "continuity_mode": structure["continuity_mode"],
            "opponent_strength_iterations": structure["opponent_strength_iterations"],
            "final_opponent_adjustment_mode": structure["final_opponent_adjustment_mode"],
        })
        combos.append(combo)
    return combos


def run_step_b_full(league_id: int, train_seasons: List[int], structure: dict, model: BaseScoringModel, rho_candidates: list) -> list:
    _assert_not_final_validation_season(train_seasons)
    grid = step_b_param_grid(structure, rho_candidates)
    results = []
    for params in grid:
        records = run_walk_forward_backtest(league_id, train_seasons, params, model)
        evaluation = evaluate_records(records)
        results.append({"params": params, "evaluation": evaluation, "aggregate": aggregate_metric(evaluation)})
    results.sort(key=lambda r: r["aggregate"])
    return results


def spot_check_eliminated_structures(
    league_id: int, train_seasons: List[int], eliminated_structures: List[dict],
    model: BaseScoringModel, current_best_aggregate: float, spot_presets: list = None,
) -> list:
    """
    탈락한 구조도 STEP B 수준 파라미터 몇 개로 다시 찔러봐서, 순수 그리디로
    인해 진짜 최적해를 놓치지 않았는지 확인한다. current_best_aggregate보다
    좋은 값이 하나라도 나오면 그 구조는 STEP B 전체 그리드로 승격이 필요하다는
    신호(beats_current_best=True)를 준다.
    """
    spot_presets = spot_presets or STEP_A_PRESETS
    findings = []
    for structure in eliminated_structures:
        best_for_structure = float("inf")
        for preset in spot_presets:
            params = dict(preset)
            params.update({
                "continuity_mode": structure["continuity_mode"],
                "opponent_strength_iterations": structure["opponent_strength_iterations"],
                "final_opponent_adjustment_mode": structure["final_opponent_adjustment_mode"],
            })
            records = run_walk_forward_backtest(league_id, train_seasons, params, model)
            agg = aggregate_metric(evaluate_records(records))
            best_for_structure = min(best_for_structure, agg)
        findings.append({
            "structure": structure,
            "best_spot_check_aggregate": best_for_structure,
            "beats_current_best": best_for_structure < current_best_aggregate,
        })
    return findings
