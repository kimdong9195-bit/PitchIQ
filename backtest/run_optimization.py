"""
backtest/run_optimization.py - STEP A/B 전체 최적화 실행 (메인 진입점)

실행:
    python backtest/run_optimization.py

절대 원칙: 여기서는 OPTIMIZATION_SEASONS(2023-24, 2024-25)만 쓴다.
FINAL_VALIDATION_SEASON(2025-26)은 이 스크립트에서 절대 안 건드린다
(코드 레벨 assert로도 이미 막혀있음 - 이건 이중 안전장치).
"""

import json
import sys
import time

sys.path.insert(0, ".")

from backtest.season_split import OPTIMIZATION_SEASONS, guard_optimization_rerun
from backtest.scoring_models.poisson_dc import PoissonDixonColesModel, RHO_CANDIDATES
from backtest.two_stage_optimize import run_two_stage_optimization, evaluate_records, summarize_diagnostics
from backtest.walk_forward import run_walk_forward_backtest
from backtest import metrics

LEAGUE_ID_EPL = 39
RESULTS_FILE = "optimization_results.json"


def _serialize(obj):
    """구조체(dict) 안의 특수 키(예: 튜플)를 JSON으로 저장 가능하게 변환."""
    if isinstance(obj, dict):
        return {str(k): _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    return obj


def print_separated_report(result: dict):
    print("\n" + "=" * 70)
    print("1. 전체 조합 수")
    print("=" * 70)
    total = sum(len(v) for v in result["step_b_results_by_structure"].values())
    print(f"STEP A: {len(result['step_a_results'])}개 구조 평가")
    print(f"STEP B: {total}개 파라미터 조합 평가 (상위 {len(result['top_structures'])}개 구조)")
    print(f"교차검증: {len(result['spot_check_results'])}개 구조 스팟체크")
    if result["escalated_structures"]:
        print(f"승격된 구조(교차검증에서 역전 발견): {[s['label'] for s in result['escalated_structures']]}")

    print("\n" + "=" * 70)
    print("2. 구조(variant)별 성능 - STEP A 순위")
    print("=" * 70)
    for r in result["step_a_results"]:
        print(f"  {r['structure']['label']:30s} avg_aggregate(Log Loss)={r['avg_aggregate']:.4f}")

    print("\n" + "=" * 70)
    print("3~4. 최종 선택된 구조 내 최고 파라미터의 시장별 Log Loss / Brier")
    print("=" * 70)
    ev = result["best_evaluation"]
    for market in ["1x2", "btts", "over_under_2.5", "handicap_-1.5"]:
        print(f"  {market:20s} LogLoss={ev[market]['log_loss']:.4f}  Brier={ev[market]['brier']:.4f}")

    print("\n" + "=" * 70)
    print("5. 득점 MAE/RMSE")
    print("=" * 70)
    print(f"  홈팀 득점: MAE={ev['home_goals']['mae']:.4f}  RMSE={ev['home_goals']['rmse']:.4f}")
    print(f"  원정팀 득점: MAE={ev['away_goals']['mae']:.4f}  RMSE={ev['away_goals']['rmse']:.4f}")

    print("\n" + "=" * 70)
    print("6. Calibration (예측확률 구간별 실제 적중률) - 1X2 홈승 기준")
    print("=" * 70)
    for b in result.get("calibration_home_win", []):
        if b["n_samples"] > 0:
            print(f"  구간 {b['bin_range']}: 예측평균={b['avg_predicted']:.3f}  실제적중률={b['avg_actual']:.3f}  (표본{b['n_samples']}개)")

    print("\n" + "=" * 70)
    print("8~9. 모델 진단 (rho clipping 발생 여부 / tail truncation 확인)")
    print("=" * 70)
    diag = result.get("diagnostics")
    if diag:
        print(f"  전체 예측 수: {diag['n_predictions']}")
        print(f"  음수확률 clipping 발생: {diag['negative_clip_occurred_count']}건 ({diag['negative_clip_occurred_pct']:.2f}%)")
        print(f"  max_goals 사용범위: {diag['max_goals_min']} ~ {diag['max_goals_max']} (평균 {diag['max_goals_avg']:.1f})")
        print(f"  tail probability 최댓값: {diag['tail_probability_max']:.2e}")
        print(f"  전부 1e-8 기준 이하: {diag['tail_probability_under_threshold']}")

    print("\n" + "=" * 70)
    print("7. 2025-26 Final Validation")
    print("=" * 70)
    print("  아직 실행 안 함 - 이 스크립트는 최적화 전용이고, 최종검증은 별도 스크립트로")
    print("  '최적화가 완전히 끝난 뒤 딱 한 번만' 실행해야 함 (season_split.py의 잠금장치 참고)")
    print("  -> backtest/run_final_validation.py 실행하세요")

    print("\n" + "=" * 70)
    print("8. 선택된 최종 파라미터 세트")
    print("=" * 70)
    print(f"  구조: {result['best_structure_label']}")
    for k, v in result["best_params"].items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 70)
    print("9. 선택 이유 (최적화 데이터 기준)")
    print("=" * 70)
    print(f"  4개 확률형 시장(1X2/BTTS/OU2.5/핸디캡-1.5) Log Loss 평균이")
    print(f"  {OPTIMIZATION_SEASONS} 시즌 전체에서 {result['best_aggregate']:.4f}로 가장 낮았음")
    print(f"  (이 값이 낮을수록 예측 확률이 실제 결과와 더 잘 맞았다는 뜻)")


def main():
    # 9번 감사에서 발견된 허점 수정: 최종검증(2025-26)이 이미 끝났으면
    # 여기서 막는다. force가 필요하면 코드를 직접 고쳐야만 우회되게 해서,
    # "실수로 재실행"은 막고 "의도적 재시작"은 가능하게 한다.
    from backtest.season_split import FinalValidationAlreadyDoneError
    try:
        guard_optimization_rerun(force=False)
    except FinalValidationAlreadyDoneError as e:
        print(f"실행 중단: {e}")
        return

    print(f"최적화 시작 - 대상 시즌: {OPTIMIZATION_SEASONS} (2025-26은 이 스크립트에서 절대 안 씀)")
    start = time.time()

    result = run_two_stage_optimization(
        LEAGUE_ID_EPL, OPTIMIZATION_SEASONS, PoissonDixonColesModel(), RHO_CANDIDATES
    )

    elapsed = time.time() - start
    print(f"\n총 소요시간: {elapsed/60:.1f}분")

    # 최고 파라미터로 한 번 더 돌려서 원본 record를 얻는다 (calibration/진단용 -
    # STEP B 그리드서치 도중엔 evaluation 요약값만 남기고 원본은 버렸으므로)
    best_records = run_walk_forward_backtest(LEAGUE_ID_EPL, OPTIMIZATION_SEASONS, result["best_params"], PoissonDixonColesModel())
    home_win_actual = [1 if r["actual_home_goals"] > r["actual_away_goals"] else 0 for r in best_records]
    home_win_pred = [r["market_1x2"]["home_win"] for r in best_records]
    result["calibration_home_win"] = metrics.calibration_bins(home_win_pred, home_win_actual, n_bins=10)
    result["diagnostics"] = summarize_diagnostics(best_records)

    print_separated_report(result)

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(_serialize(result), f, ensure_ascii=False, indent=2)
    print(f"\n결과가 {RESULTS_FILE}에 저장되었습니다. 이 파일을 공유해주시면 같이 검토할 수 있어요.")


if __name__ == "__main__":
    main()
