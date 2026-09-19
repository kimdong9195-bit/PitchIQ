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

from backtest.season_split import OPTIMIZATION_SEASONS
from backtest.scoring_models.poisson_dc import PoissonDixonColesModel, RHO_CANDIDATES
from backtest.two_stage_optimize import run_two_stage_optimization

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
    print("6. Calibration - 추후 별도 스크립트로 확인 (원본 확률/결과 쌍은 record에 남아있음)")
    print("=" * 70)

    print("\n" + "=" * 70)
    print("7. 2025-26 Final Validation")
    print("=" * 70)
    print("  아직 실행 안 함 - 이 스크립트는 최적화 전용이고, 최종검증은 별도 스크립트로")
    print("  '최적화가 완전히 끝난 뒤 딱 한 번만' 실행해야 함 (season_split.py의 잠금장치 참고)")

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
    print(f"최적화 시작 - 대상 시즌: {OPTIMIZATION_SEASONS} (2025-26은 이 스크립트에서 절대 안 씀)")
    start = time.time()

    result = run_two_stage_optimization(
        LEAGUE_ID_EPL, OPTIMIZATION_SEASONS, PoissonDixonColesModel(), RHO_CANDIDATES
    )

    elapsed = time.time() - start
    print(f"\n총 소요시간: {elapsed/60:.1f}분")

    print_separated_report(result)

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(_serialize(result), f, ensure_ascii=False, indent=2)
    print(f"\n결과가 {RESULTS_FILE}에 저장되었습니다. 이 파일을 공유해주시면 같이 검토할 수 있어요.")


if __name__ == "__main__":
    main()
