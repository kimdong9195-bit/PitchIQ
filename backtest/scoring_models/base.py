"""
backtest/scoring_models/base.py - 스코어 확률 모델의 공통 인터페이스

Poisson+Dixon-Coles든 나중에 Negative Binomial이든, 이 인터페이스만
구현하면 백테스트 루프(STEP 10)와 시장 계산(STEP 9)이 어떤 모델인지
전혀 몰라도 동일하게 작동한다. 모델 비교(STEP 11 이후)는 이 인터페이스를
구현한 객체를 바꿔 끼우는 것만으로 가능해야 한다.

중요: 여기서 "V1 기준 모델"이라는 표현을 쓰는 이유 - Poisson+Dixon-Coles를
최종 정답으로 가정하지 않는다. Time-forward Backtesting으로 실제 성능이
검증되어야 하고, 다른 모델이 시장별 오차 지표에서 더 낫다는 게 확인되면
교체될 수 있는 "첫 번째 버전"일 뿐이다.
"""

from abc import ABC, abstractmethod
from typing import Dict, Tuple


class BaseScoringModel(ABC):
    """
    모든 스코어 확률 모델이 구현해야 하는 인터페이스.
    입력은 항상 (lambda_home, lambda_away) + 모델별 파라미터,
    출력은 항상 {(home_goals, away_goals): probability} 형태의 score_matrix.
    """

    name: str = "base"

    @abstractmethod
    def score_matrix(
        self, lambda_home: float, lambda_away: float, params: dict, max_goals: int = 8
    ) -> Dict[Tuple[int, int], float]:
        """
        (h, a) 스코어별 확률을 담은 dict를 반환한다. 반드시 총합이 1이 되도록
        재정규화해서 반환해야 한다 (모델마다 절단/보정 방식이 달라 합이
        어긋날 수 있으므로 이 계약을 인터페이스 레벨에서 강제한다).
        """
        raise NotImplementedError

    @abstractmethod
    def param_candidates(self) -> dict:
        """
        이 모델이 갖는 하이퍼파라미터의 후보값들을 {파라미터명: [후보,...]} 형태로 반환.
        STEP 11 최적화 루프가 이걸 보고 그리드서치 대상을 정한다.
        """
        raise NotImplementedError
