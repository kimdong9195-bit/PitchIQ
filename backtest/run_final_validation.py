"""
backtest/run_final_validation.py - 2025-26 Final Validation (holdout)

절대 규칙: 이 스크립트는 run_optimization.py가 만든 optimization_results.json의
"best_params"를 그대로 가져다 쓰기만 한다. 여기서 파라미터를 새로 고르거나
탐색하는 코드는 전혀 없다 - 그건 이미 STEP A/B에서 2023-24+2024-25만
갖고 끝난 일이다.

이 스크립트를 실행하고 나면 season_split.py의 잠금 파일이 생성되고,
그 이후 run_optimization.py를 다시 돌리려면 force=True를 명시적으로
줘야 한다 (실수로 "2025-26 성능 보고 파라미터 다시 조정" 하는 걸 막기 위함).

실행:
    python backtest/run_final_validation.py
"""

import json
import sys

sys.path.insert(0, ".")

from backtest import metrics
from backtest.scoring_models.poisson_dc import PoissonDixonColesModel
from backtest.season_split import FINAL_VALIDATION_SEASON, lock_after_final_validation, is_final_validation_locked
from backtest.two_stage_optimize import evaluate_records, summarize_diagnostics
from backtest.walk_forward import run_walk_forward_backtest

OPT_RESULTS_FILE = "optimization_results.json"
LEAGUE_ID_EPL = 39


def main():
    if is_final_validation_locked():
        print("이미 최종검증이 한 번 실행된 상태입니다 (final_validation_completed.json 존재).")
        print("결과를 보고 파라미터를 다시 조정하는 건 원칙에 어긋나므로, 이 스크립트를")
        print("다시 실행하지 않는 걸 권장합니다. 정말 처음부터 다시 해야 한다면")
        print("이 파일을 직접 지우고 재실행하세요 (신중하게 판단하세요).")
        return

    with open(OPT_RESULTS_FILE, "r", encoding="utf-8") as f:
        opt_results = json.load(f)
    best_params = opt_results["best_params"]

    print(f"최종검증 시작 - 대상 시즌: {FINAL_VALIDATION_SEASON} (2025-26)")
    print(f"사용 파라미터 (optimization_results.json에서 그대로 가져옴): {best_params}")
    print("이 파라미터는 여기서 절대 재조정하지 않습니다.\n")

    records = run_walk_forward_backtest(LEAGUE_ID_EPL, [FINAL_VALIDATION_SEASON], best_params, PoissonDixonColesModel())
    evaluation = evaluate_records(records)
    diagnostics = summarize_diagnostics(records)

    home_win_actual = [1 if r["actual_home_goals"] > r["actual_away_goals"] else 0 for r in records]
    home_win_pred = [r["market_1x2"]["home_win"] for r in records]
    calibration = metrics.calibration_bins(home_win_pred, home_win_actual, n_bins=10)

    print("=" * 70)
    print("2025-26 Final Validation (Holdout) 성능 - 최적화에 전혀 안 쓰인 시즌")
    print("=" * 70)
    print(f"예측된 경기 수: {evaluation['n_predictions']}")
    for market in ["1x2", "btts", "over_under_2.5", "handicap_-1.5"]:
        print(f"  {market:20s} LogLoss={evaluation[market]['log_loss']:.4f}  Brier={evaluation[market]['brier']:.4f}")
    print(f"  홈팀 득점: MAE={evaluation['home_goals']['mae']:.4f}  RMSE={evaluation['home_goals']['rmse']:.4f}")
    print(f"  원정팀 득점: MAE={evaluation['away_goals']['mae']:.4f}  RMSE={evaluation['away_goals']['rmse']:.4f}")

    print("\n최적화 구간(2023-24+2024-25) 성능과 비교:")
    opt_eval = opt_results["best_evaluation"]
    for market in ["1x2", "btts", "over_under_2.5", "handicap_-1.5"]:
        opt_ll = opt_eval[market]["log_loss"]
        val_ll = evaluation[market]["log_loss"]
        diff_note = "성능 유지" if abs(val_ll - opt_ll) < opt_ll * 0.2 else "⚠ 성능 차이 큼 (과적합 의심)"
        print(f"  {market:20s} 최적화기간={opt_ll:.4f} vs 최종검증={val_ll:.4f}  ({diff_note})")

    print("\n진단 (rho clipping / tail truncation):")
    print(f"  음수확률 clipping 발생: {diagnostics['negative_clip_occurred_count']}건 ({diagnostics['negative_clip_occurred_pct']:.2f}%)")
    print(f"  max_goals 범위: {diagnostics['max_goals_min']}~{diagnostics['max_goals_max']}, tail 최댓값: {diagnostics['tail_probability_max']:.2e}")

    result = {
        "final_validation_season": FINAL_VALIDATION_SEASON,
        "params_used": best_params,
        "evaluation": evaluation,
        "diagnostics": diagnostics,
        "calibration_home_win": calibration,
        "comparison_to_optimization_period": opt_eval,
    }
    with open("final_validation_results.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    lock_after_final_validation(best_params, evaluation)
    print("\nfinal_validation_results.json 저장 완료.")
    print("잠금 파일(final_validation_completed.json)이 생성되어, 이제부터 파라미터")
    print("재조정이 코드 레벨에서 막힙니다 (의도된 동작입니다).")


if __name__ == "__main__":
    main()
