"""
backtest/cache.py - 반복 계산/조회 캐싱

핵심 통찰: STEP B 그리드서치에서 continuity_mode/opponent 구조가 고정된
채로 half_life, K, HA, rho 같은 "가중치/스케일 파라미터"만 바뀌는 조합을
수백~수천 개 도는데, 이때 "이 시점(as_of_date) 이전의 원본 경기 목록
자체"는 파라미터가 뭐든 항상 완전히 동일하다 (뭘 가중치로 곱하느냐만
다르지, 어떤 원본 경기를 포함하느냐는 안 바뀜). 그래서 원본 경기 조회
결과를 (league_id, season, before_date) 기준으로 캐싱해두면, SQLite를
매 조합마다 다시 두드릴 필요가 없다 - 이게 캐싱 효과가 가장 큰 지점이다.

주의: 이 캐시는 "읽기 전용 원본 데이터"만 캐싱한다. 파라미터에 따라
달라지는 계산 결과(팀강도, λ, score_matrix)는 여기서 캐싱하지 않는다 -
그건 매번 다시 계산하는 게 맞다 (파라미터가 다르면 값도 달라야 하므로).
"""

from backtest import schema

_matches_before_cache = {}
_all_matches_cache = {}


def cached_get_matches_before(league_id: int, season: int, before_date: str) -> list:
    key = (league_id, season, before_date)
    if key not in _matches_before_cache:
        _matches_before_cache[key] = schema.get_matches_before(league_id, season, before_date)
    return _matches_before_cache[key]


def cached_get_all_matches(league_id: int, season: int) -> list:
    key = (league_id, season)
    if key not in _all_matches_cache:
        _all_matches_cache[key] = schema.get_all_matches(league_id, season)
    return _all_matches_cache[key]


def clear_cache():
    """리그/시즌 데이터가 바뀌었을 때(예: 재수집 후)만 호출하면 된다."""
    _matches_before_cache.clear()
    _all_matches_cache.clear()


def cache_stats() -> dict:
    return {
        "matches_before_entries": len(_matches_before_cache),
        "all_matches_entries": len(_all_matches_cache),
    }
