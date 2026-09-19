"""
backtest/scoring_models/poisson_dc.py - V1 기준 모델: Poisson + Dixon-Coles

주의: 이건 "최종 정답"이 아니라 V1 기준 모델이다. STEP 11 이후 백테스트
결과에 따라 Negative Binomial 등 다른 모델(scoring_models/ 하위에 같은
인터페이스로 추가)과 비교되어 교체될 수 있다.

rho(저득점 상관계수)는 고정값이 아니라 다른 하이퍼파라미터와 동일하게
후보 리스트 중 Time-forward Backtesting으로 값을 고른다 (최적화 구간
시즌의 데이터만 사용 - 검증 구간 시즌은 이 선택에 관여하지 않아 미래
데이터 누수가 없다).
"""

import math
from typing import Dict, Tuple

from backtest.scoring_models.base import BaseScoringModel

RHO_CANDIDATES = [-0.05, -0.10, -0.13, -0.20, -0.30]  # 백테스트로 최적값 탐색 대상


def _poisson_prob(lam: float, k: int) -> float:
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def _dixon_coles_tau(h: int, a: int, lambda_home: float, lambda_away: float, rho: float) -> float:
    if h == 0 and a == 0:
        return 1 - (lambda_home * lambda_away * rho)
    elif h == 0 and a == 1:
        return 1 + (lambda_home * rho)
    elif h == 1 and a == 0:
        return 1 + (lambda_away * rho)
    elif h == 1 and a == 1:
        return 1 - rho
    return 1.0


DEFAULT_TAIL_THRESHOLD = 1e-8  # 격자 밖으로 새는 확률의 허용 상한 (STEP10 요구사항)
MAX_GOALS_CEILING = 40  # 무한루프 방지용 상한


def _sufficient_max_goals(lambda_home: float, lambda_away: float, tail_threshold: float = DEFAULT_TAIL_THRESHOLD, ceiling: int = MAX_GOALS_CEILING) -> int:
    """
    tail probability(격자 밖으로 새는 확률)가 threshold 밑으로 떨어지는
    최소 max_goals를 찾는다. 고정값(예: 8)을 쓰면 λ가 큰 경기(예: 6.6)에서
    9골 이상 나올 확률이 22%나 새는 문제가 실측으로 확인됐다 - 이후 단순
    재정규화로는 이 정도 손실을 제대로 못 메꾼다. 그래서 λ에 맞춰 격자
    크기 자체를 늘리는 방식으로 해결한다. threshold는 1e-8로 훨씬 엄격하게
    잡는다 (STEP10에서 시장별 확률을 정밀하게 비교해야 하므로).
    """
    higher_lambda = max(lambda_home, lambda_away)
    max_goals = 8
    while max_goals < ceiling:
        tail = 1 - sum(_poisson_prob(higher_lambda, k) for k in range(max_goals + 1))
        if tail < tail_threshold:
            break
        max_goals += 2
    return min(max_goals, ceiling)


def validate_rho_nonnegative(rho: float, lambda_home: float, lambda_away: float) -> bool:
    """
    이 rho와 이 λ 조합에서 Dixon-Coles tau 보정이 (0,0),(1,0),(0,1),(1,1) 네 칸의
    확률을 음수로 만들지 않는지 확인한다. rho 자체는 후보 리스트에서 정하지만,
    특정 λ와 결합했을 때 이론적으로 음수가 나올 수 있는 조합이 있어서
    (특히 rho가 크고 λ도 클 때 (0,0)칸: 1 - λh*λa*rho < 0 가능),
    STEP10 루프가 매 경기 계산 후 이 함수로 검증하고, 위반되면 로그에 남긴다.
    """
    tau_00 = 1 - (lambda_home * lambda_away * rho)
    tau_01 = 1 + (lambda_home * rho)
    tau_10 = 1 + (lambda_away * rho)
    tau_11 = 1 - rho
    return tau_00 >= 0 and tau_01 >= 0 and tau_10 >= 0 and tau_11 >= 0


class PoissonDixonColesModel(BaseScoringModel):
    name = "poisson_dixon_coles_v1"

    def __init__(self):
        # 마지막 score_matrix() 호출의 진단 정보 (STEP10 요구사항 8/9번 - clipping
        # 발생 여부, 실제 사용된 max_goals). walk_forward.py가 매 예측 직후
        # 이 값을 읽어서 record에 남기고, 나중에 전체 실행에서 몇 번 발생했는지
        # 집계할 수 있게 한다.
        self.last_had_negative_clip = False
        self.last_max_goals_used = None
        self.last_tail_probability = None

    def score_matrix(
        self, lambda_home: float, lambda_away: float, params: dict, max_goals: int = None
    ) -> Dict[Tuple[int, int], float]:
        rho = params.get("rho", -0.13)
        if max_goals is None:
            max_goals = _sufficient_max_goals(lambda_home, lambda_away)

        higher_lambda = max(lambda_home, lambda_away)
        tail_probability = 1 - sum(_poisson_prob(higher_lambda, k) for k in range(max_goals + 1))

        matrix = {}
        negative_before_clip = False
        for h in range(max_goals + 1):
            for a in range(max_goals + 1):
                p = _poisson_prob(lambda_home, h) * _poisson_prob(lambda_away, a)
                p *= _dixon_coles_tau(h, a, lambda_home, lambda_away, rho)
                if p < 0:
                    negative_before_clip = True
                    p = 0.0
                matrix[(h, a)] = p

        total = sum(matrix.values())
        assert total > 0, f"score_matrix 총합이 0 이하입니다 (lambda_home={lambda_home}, lambda_away={lambda_away}, rho={rho})"
        normalized = {k: v / total for k, v in matrix.items()}

        final_sum = sum(normalized.values())
        assert abs(final_sum - 1.0) < 1e-9, f"정규화 후 총합이 1이 아닙니다: {final_sum}"

        self.last_had_negative_clip = negative_before_clip
        self.last_max_goals_used = max_goals
        self.last_tail_probability = tail_probability

        if negative_before_clip:
            import warnings
            warnings.warn(
                f"score_matrix: 음수 확률이 발생해서 0으로 clip했습니다 "
                f"(lambda_home={lambda_home:.3f}, lambda_away={lambda_away:.3f}, rho={rho}). "
                f"이 rho/λ 조합은 validate_rho_nonnegative()로 사전 점검하는 걸 권장합니다."
            )
        return normalized

    def param_candidates(self) -> dict:
        return {"rho": RHO_CANDIDATES}
