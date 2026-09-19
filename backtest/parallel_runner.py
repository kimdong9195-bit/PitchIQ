"""
backtest/parallel_runner.py - 멀티프로세스 병렬 실행

STEP B의 각 파라미터 조합은 서로 완전히 독립적이라(한 조합의 결과가 다른
조합에 전혀 영향을 안 줌) 여러 CPU 코어에 나눠 돌려도 정확성에 문제이
없다. multiprocessing.Pool로 코어 수만큼 워커를 띄워서 조합들을 나눠준다.

SQLite 동시 읽기: 각 워커 프로세스가 독립적으로 자기 커넥션을 열어서
읽기 전용으로만 쓰기 때문에(schema.py의 _connect()가 호출마다 새
커넥션을 만듦) 여러 프로세스가 동시에 읽어도 안전하다.
"""

import multiprocessing
import os
from typing import List

from backtest.two_stage_optimize import evaluate_records, aggregate_metric
from backtest.walk_forward import run_walk_forward_backtest


def _evaluate_one_combo(args) -> dict:
    """워커 프로세스 하나가 처리하는 작업 단위: 파라미터 조합 하나 -> 평가결과."""
    league_id, train_seasons, params, model = args
    records = run_walk_forward_backtest(league_id, train_seasons, params, model)
    evaluation = evaluate_records(records)
    return {"params": params, "evaluation": evaluation, "aggregate": aggregate_metric(evaluation)}


def run_grid_parallel(
    league_id: int,
    train_seasons: List[int],
    param_grid: List[dict],
    model,
    n_workers: int = None,
) -> list:
    """
    param_grid(딕셔너리 리스트)를 n_workers개 프로세스에 나눠서 처리한다.
    n_workers를 안 주면 os.cpu_count()를 그대로 쓴다.
    """
    n_workers = n_workers or os.cpu_count() or 4
    tasks = [(league_id, train_seasons, params, model) for params in param_grid]

    with multiprocessing.Pool(processes=n_workers) as pool:
        results = pool.map(_evaluate_one_combo, tasks, chunksize=max(1, len(tasks) // (n_workers * 4)))

    results.sort(key=lambda r: r["aggregate"])
    return results


def run_step_b_full_parallel(league_id, train_seasons, structure: dict, model, rho_candidates: list, n_workers: int = None) -> list:
    """two_stage_optimize.run_step_b_full()과 결과는 동일하지만 병렬로 처리."""
    from backtest.two_stage_optimize import step_b_param_grid, _assert_not_final_validation_season
    _assert_not_final_validation_season(train_seasons)
    grid = step_b_param_grid(structure, rho_candidates)
    return run_grid_parallel(league_id, train_seasons, grid, model, n_workers)
