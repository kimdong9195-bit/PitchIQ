"""
backtest/metrics.py - 시장별 평가지표

확률형 시장(1X2, BTTS, O/U, 핸디캡): Log Loss, Brier Score
득점형(홈/원정/총 득점): MAE, RMSE

전부 "예측값 리스트, 실제값 리스트"를 받는 순수 함수라서, STEP10 백테스트
결과(prediction record 리스트)에서 바로 뽑아 쓸 수 있다.
"""

import math
from typing import List, Tuple

EPS = 1e-15  # log(0) 방지용


def log_loss(predicted_probs: List[float], actual_outcomes: List[int]) -> float:
    """
    이진 분류 Log Loss. actual_outcomes는 0 또는 1.
    (3-way인 1X2는 클래스마다 따로 이진화해서 각각 넣거나, multiclass_log_loss 사용)
    """
    n = len(predicted_probs)
    if n == 0:
        return None
    total = 0.0
    for p, y in zip(predicted_probs, actual_outcomes):
        p_clipped = min(max(p, EPS), 1 - EPS)
        total += -(y * math.log(p_clipped) + (1 - y) * math.log(1 - p_clipped))
    return total / n


def multiclass_log_loss(predicted_prob_dicts: List[dict], actual_labels: List[str]) -> float:
    """
    다중클래스(예: 1X2 = home_win/draw/away_win) Log Loss.
    predicted_prob_dicts: [{"home_win":0.5,"draw":0.3,"away_win":0.2}, ...]
    actual_labels: ["home_win", "draw", ...]
    """
    n = len(predicted_prob_dicts)
    if n == 0:
        return None
    total = 0.0
    for probs, label in zip(predicted_prob_dicts, actual_labels):
        p = min(max(probs.get(label, 0.0), EPS), 1 - EPS)
        total += -math.log(p)
    return total / n


def brier_score(predicted_probs: List[float], actual_outcomes: List[int]) -> float:
    """이진 Brier Score. (예측확률-실제결과)^2의 평균."""
    n = len(predicted_probs)
    if n == 0:
        return None
    return sum((p - y) ** 2 for p, y in zip(predicted_probs, actual_outcomes)) / n


def multiclass_brier_score(predicted_prob_dicts: List[dict], actual_labels: List[str], classes: List[str]) -> float:
    """
    다중클래스 Brier Score (각 클래스를 0/1로 취급해서 제곱오차 합, 관례적으로
    클래스 수만큼 더한 값을 그대로 쓰거나 평균낸다 - 여기서는 평균).
    """
    n = len(predicted_prob_dicts)
    if n == 0:
        return None
    total = 0.0
    for probs, label in zip(predicted_prob_dicts, actual_labels):
        for c in classes:
            y = 1.0 if c == label else 0.0
            total += (probs.get(c, 0.0) - y) ** 2
    return total / n


def mae(predicted_values: List[float], actual_values: List[float]) -> float:
    n = len(predicted_values)
    if n == 0:
        return None
    return sum(abs(p - a) for p, a in zip(predicted_values, actual_values)) / n


def rmse(predicted_values: List[float], actual_values: List[float]) -> float:
    n = len(predicted_values)
    if n == 0:
        return None
    return math.sqrt(sum((p - a) ** 2 for p, a in zip(predicted_values, actual_values)) / n)


def calibration_bins(predicted_probs: List[float], actual_outcomes: List[int], n_bins: int = 10) -> List[dict]:
    """
    캘리브레이션 확인용: 예측확률을 n_bins개 구간으로 나눠서, 각 구간의
    평균 예측확률 vs 실제 적중률을 비교할 수 있게 원본 쌍으로 집계한다.
    나중에 시각화하거나 캘리브레이션 곡선을 그릴 때 이 결과를 쓴다.
    """
    bins = [{"bin_range": (i / n_bins, (i + 1) / n_bins), "predicted": [], "actual": []} for i in range(n_bins)]
    for p, y in zip(predicted_probs, actual_outcomes):
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx]["predicted"].append(p)
        bins[idx]["actual"].append(y)

    result = []
    for b in bins:
        n = len(b["predicted"])
        result.append({
            "bin_range": b["bin_range"],
            "n_samples": n,
            "avg_predicted": sum(b["predicted"]) / n if n > 0 else None,
            "avg_actual": sum(b["actual"]) / n if n > 0 else None,
        })
    return result
