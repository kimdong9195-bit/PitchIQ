"""
evaluate_predictions.py - 실전 예측 성능 집계

predictions(스냅샷) + actual_results(실제결과)를 fixture_id로 조인해서
Log Loss / Brier / 적중률 / λ 오차 / Calibration을 계산한다.

backtest/metrics.py의 계산식을 그대로 import해서 재사용한다 (이미 여러 번
검증된 코드라 중복 구현 안 함) - backtest/metrics.py 파일 자체는 전혀
수정하지 않는다. predictor.py, backtest/의 다른 파일들도 이 스크립트에서
전혀 안 건드린다 (순수하게 이미 저장된 데이터를 읽어서 집계만 함).

표본이 적을 때는 확정적인 결론처럼 보이지 않도록 경고를 같이 낸다.
"""

import sys

sys.path.insert(0, ".")

from backtest import metrics
import prediction_log

MIN_SAMPLE_FOR_CONFIDENCE = 20
CLASSES_1X2 = ["home_win", "draw", "away_win"]


def actual_outcome_1x2(hg: int, ag: int) -> str:
    if hg > ag:
        return "home_win"
    if hg < ag:
        return "away_win"
    return "draw"


def main():
    rows = prediction_log.get_predictions_with_results()
    resolved = [r for r in rows if r.get("actual_home_goals") is not None]

    print(f"전체 예측 스냅샷 수: {len(rows)}")
    print(f"실제결과 연결된 스냅샷 수: {len(resolved)}\n")

    if not resolved:
        print("아직 실제결과가 연결된 예측이 없습니다. update_results.py가 실행되고,")
        print("실제로 경기가 끝나야 여기 값이 나오기 시작합니다.")
        return

    if len(resolved) < MIN_SAMPLE_FOR_CONFIDENCE:
        print(f"주의: 표본이 {len(resolved)}건뿐이라 아래 지표는 참고용입니다 "
              f"(최소 {MIN_SAMPLE_FOR_CONFIDENCE}건 이상 쌓인 뒤 판단을 권장).\n")

    hg = [r["actual_home_goals"] for r in resolved]
    ag = [r["actual_away_goals"] for r in resolved]
    outcomes = [actual_outcome_1x2(h, a) for h, a in zip(hg, ag)]
    btts_actual = [1 if (h >= 1 and a >= 1) else 0 for h, a in zip(hg, ag)]
    o25_actual = [1 if (h + a) > 2.5 else 0 for h, a in zip(hg, ag)]
    hcap_actual = []
    for r, h, a in zip(resolved, hg, ag):
        line = r.get("handicap_line")
        if line is None:
            hcap_actual.append(None)
        else:
            hcap_actual.append(1 if (h - a + line) > 0 else 0)

    x1x2_pred = [{"home_win": r["prob_home_win"], "draw": r["prob_draw"], "away_win": r["prob_away_win"]} for r in resolved]
    btts_pred = [r["prob_btts_yes"] for r in resolved]
    o25_pred = [r["prob_over_2_5"] for r in resolved]
    hcap_pred = [r["prob_handicap_home"] for r in resolved]

    print("=" * 70)
    print("1) Log Loss / Brier Score")
    print("=" * 70)
    ll_1x2 = metrics.multiclass_log_loss(x1x2_pred, outcomes)
    br_1x2 = metrics.multiclass_brier_score(x1x2_pred, outcomes, CLASSES_1X2)
    print(f"1X2      LogLoss={ll_1x2:.4f}  Brier={br_1x2:.4f}")

    ll_btts = metrics.log_loss(btts_pred, btts_actual)
    br_btts = metrics.brier_score(btts_pred, btts_actual)
    print(f"BTTS     LogLoss={ll_btts:.4f}  Brier={br_btts:.4f}")

    ll_o25 = metrics.log_loss(o25_pred, o25_actual)
    br_o25 = metrics.brier_score(o25_pred, o25_actual)
    print(f"O/U 2.5  LogLoss={ll_o25:.4f}  Brier={br_o25:.4f}")

    hcap_pairs = [(p, a) for p, a in zip(hcap_pred, hcap_actual) if a is not None]
    if hcap_pairs:
        hp, ha = zip(*hcap_pairs)
        ll_hcap = metrics.log_loss(list(hp), list(ha))
        br_hcap = metrics.brier_score(list(hp), list(ha))
        print(f"Handicap LogLoss={ll_hcap:.4f}  Brier={br_hcap:.4f}  (표본 {len(hcap_pairs)}건)")
    else:
        print("Handicap: 라인 정보 없는 예측뿐이라 계산 불가")

    print("\n" + "=" * 70)
    print("2) 적중률 (pick이 실제로 맞았는지)")
    print("=" * 70)
    hit_1x2 = sum(1 for p, o in zip(x1x2_pred, outcomes) if max(p, key=p.get) == o) / len(resolved)
    hit_btts = sum(1 for p, a in zip(btts_pred, btts_actual) if (p > 0.5) == bool(a)) / len(resolved)
    hit_o25 = sum(1 for p, a in zip(o25_pred, o25_actual) if (p > 0.5) == bool(a)) / len(resolved)
    print(f"1X2 적중률: {hit_1x2*100:.1f}%")
    print(f"BTTS 적중률: {hit_btts*100:.1f}%")
    print(f"O/U 2.5 적중률: {hit_o25*100:.1f}%")

    print("\n" + "=" * 70)
    print("3) λ 오차 (MAE/RMSE)")
    print("=" * 70)
    lh_pred = [r["lambda_home"] for r in resolved]
    la_pred = [r["lambda_away"] for r in resolved]
    print(f"홈 λ: MAE={metrics.mae(lh_pred, hg):.4f}  RMSE={metrics.rmse(lh_pred, hg):.4f}")
    print(f"원정 λ: MAE={metrics.mae(la_pred, ag):.4f}  RMSE={metrics.rmse(la_pred, ag):.4f}")

    print("\n" + "=" * 70)
    print("4) Calibration (BTTS 기준, 10구간)")
    print("=" * 70)
    bins = metrics.calibration_bins(btts_pred, btts_actual, n_bins=10)
    for b in bins:
        if b["n_samples"] > 0:
            lo, hi = b["bin_range"]
            print(f"  ({lo:.1f},{hi:.1f}): N={b['n_samples']:>3}  예측평균={b['avg_predicted']:.3f}  실제={b['avg_actual']:.3f}")

    print("\n(model_version별로 나눠서 보고 싶으면 "
          "prediction_log.get_predictions_with_results(model_version='v1.0.0') 처럼 필터링해서 재사용 가능)")


if __name__ == "__main__":
    main()
