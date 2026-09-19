"""
backtest/optimize.py - STEP 11 준비: 파라미터 탐색

V1은 Grid Search(전체 조합 탐색)를 쓴다. 후보가 늘어나서 계산량이 감당
안 되면 그때 다른 탐색 전략(순차 최적화, 랜덤서치, 베이지안 최적화 등)으로
바꿀 수 있도록, "탐색 전략"과 "평가 함수"를 분리해서 설계한다.

이 파일은 STEP 10(Time-forward Backtesting 루프)이 아직 없어서 실제
백테스트 평가 함수는 스텁(자리만 채움) 상태다. STEP 10이 만들어지면
evaluate_combo에 진짜 백테스트 실행 로직을 연결하면 된다.
"""

import itertools
from typing import Callable, Dict, List


def build_grid(param_candidates: Dict[str, list]) -> List[Dict]:
    """
    {파라미터명: [후보,...]} 형태를 받아서, 가능한 모든 조합을
    [{파라미터명: 값, ...}, ...] 리스트로 펼친다 (Cartesian product).
    """
    keys = list(param_candidates.keys())
    value_lists = [param_candidates[k] for k in keys]
    combos = []
    for values in itertools.product(*value_lists):
        combos.append(dict(zip(keys, values)))
    return combos


def run_grid_search(
    param_candidates: Dict[str, list],
    evaluate_combo: Callable[[Dict], Dict],
) -> List[Dict]:
    """
    전체 조합을 순회하며 evaluate_combo(params) -> {"market_name": error_value, ...}
    를 호출하고, 모든 결과를 리스트로 반환한다 (최적값 선택은 호출하는 쪽에서
    시장별로 따로 판단 - 이 함수는 "다 돌려서 기록"까지만 책임진다. 스펙 15번
    "시장별 성능을 하나의 오차값으로 뭉치지 않는다" 원칙을 지키기 위함).

    evaluate_combo는 STEP 10 백테스트 루프가 완성되면 연결한다. 지금은
    호출하는 쪽에서 스텁 함수를 넣어 grid 생성 자체만 검증할 수 있다.
    """
    combos = build_grid(param_candidates)
    results = []
    for params in combos:
        metrics = evaluate_combo(params)
        results.append({"params": params, "metrics": metrics})
    return results


def combo_count(param_candidates: Dict[str, list]) -> int:
    """실제로 몇 개 조합이 나오는지 미리 계산 (실행 전에 규모를 가늠하기 위함)."""
    total = 1
    for candidates in param_candidates.values():
        total *= len(candidates)
    return total
