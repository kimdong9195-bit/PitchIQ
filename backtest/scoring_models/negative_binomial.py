"""
backtest/scoring_models/negative_binomial.py - 향후 비교 대상 (미구현)

Poisson은 "평균=분산"을 가정하는데, 실제 축구 득점 데이터는 분산이 평균보다
큰 과산포(overdispersion) 경향이 있다는 연구가 있다. Negative Binomial은
이 과산포를 표현할 수 있는 분산 파라미터를 추가로 갖는 모델이라, STEP 11
백테스트에서 Poisson+Dixon-Coles(V1)과 동일 조건으로 비교할 후보로 남겨둔다.

아직 구현하지 않음 - BaseScoringModel 인터페이스만 지키면 되므로,
필요해지면 score_matrix()에 음이항분포 확률질량함수를 채우면 된다.
지금 단계에서 미리 구현하지 않는 이유: STEP 10~11(백테스트/최적화)이
먼저 돌아가서 Poisson+DC의 실제 약점이 시장별 오차로 확인된 뒤에
착수하는 게 순서에 맞다 (스펙 17번 원칙 - 필요성이 데이터로 확인된 다음에
복잡도를 추가한다).
"""

from typing import Dict, Tuple

from backtest.scoring_models.base import BaseScoringModel


class NegativeBinomialModel(BaseScoringModel):
    name = "negative_binomial_v0_stub"

    def score_matrix(
        self, lambda_home: float, lambda_away: float, params: dict, max_goals: int = 8
    ) -> Dict[Tuple[int, int], float]:
        raise NotImplementedError(
            "Negative Binomial 모델은 아직 구현되지 않았습니다. "
            "STEP 11에서 Poisson+DC의 한계가 데이터로 확인되면 구현합니다."
        )

    def param_candidates(self) -> dict:
        return {}
