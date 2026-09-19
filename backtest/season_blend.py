"""
backtest/season_blend.py - STEP 6: 직전 시즌 + 현재 시즌 데이터 결합

주의 (용어 정정): 이 모듈은 "장기 전력(long-term rating)"이 아니다.
정확히는 "직전 시즌 전체 평균 vs 현재 시즌 전체 평균의 시즌 전환 처리"다.
여러 시즌에 걸친 팀의 근본적 수준을 추정하는 진짜 장기 레이팅(예: 3~4년
치 Elo 스타일 지표)은 여기서 다루지 않는다 - V1에서는 만들지 않기로
결정했고, 지금 있는 이 시즌 결합만으로 시즌 초반 표본부족 문제를 처리한다.

확장 지점: 나중에 백테스트 결과가 부족하다고 판단되면, 이 모듈과 나란히
`long_term_rating.py` 같은 별도 모듈을 만들어서 "직전시즌+현재시즌 블렌딩
결과"에 "여러 시즌 장기 레이팅"을 한 겹 더 얹는 구조로 확장할 수 있다.
지금 `blend_team_averages()`는 current/previous 두 값만 받지만, 향후
`long_term_rating` 인자를 추가로 받아 3중 블렌딩(현재/직전/장기)으로
넓히는 것도 가능하도록 함수 시그니처를 크게 바꾸지 않고 확장 가능한
형태로 유지한다 (아래 blend_team_averages의 인자 순서/기본값 참고).

공식 (베이지안 수축/shrinkage에서 흔히 쓰는 형태를 후보로 채택):

    current_weight = current_games / (current_games + K)
    previous_weight = 1 - current_weight

K는 "이전 시즌 데이터 한 경기가 현재 시즌 데이터 한 경기와 동등해지려면
현재 시즌 경기가 몇 개나 쌓여야 하는가"를 뜻하는 파라미터다. K가 클수록
이전 시즌 영향이 오래 유지된다. 이 값 역시 사람이 임의로 정하지 않고
백테스트로 최적값을 찾는다 (K_CANDIDATES).

예시 (K=10일 때):
    현재 시즌 0경기  -> current_weight = 0/(0+10)   = 0.0  (이전 시즌 100%)
    현재 시즌 5경기  -> current_weight = 5/(5+10)   = 0.33
    현재 시즌 10경기 -> current_weight = 10/(10+10) = 0.5
    현재 시즌 30경기 -> current_weight = 30/(30+10) = 0.75
"""

from typing import Optional

from backtest.team_strength import TeamAverages

K_CANDIDATES = [3, 5, 10, 15, 20]  # 백테스트로 최적값 탐색 대상


def compute_current_season_weight(current_games: int, k: float) -> float:
    if current_games <= 0:
        return 0.0
    return current_games / (current_games + k)


def blend_team_averages(
    current: Optional[TeamAverages],
    previous: Optional[TeamAverages],
    k: float,
    long_term_rating: Optional[TeamAverages] = None,  # 확장 지점 - V1에서는 항상 None
) -> Optional[TeamAverages]:
    """
    현재 시즌과 직전 시즌의 TeamAverages를 표본 크기 기반 가중치로 결합한다.

    - 둘 다 없으면 None (데이터 자체가 없는 것 - 임의로 만들어내지 않음)
    - 현재 시즌만 있으면 그대로 반환 (직전 시즌 데이터가 아예 없는 신생/이적 케이스)
    - 직전 시즌만 있으면 그대로 반환 (현재 시즌 아직 0경기 - 직전 시즌 100% 의존)
    - 둘 다 있으면 위 공식으로 블렌딩

    long_term_rating: V1에서는 사용하지 않는다(항상 None). 향후 별도의 장기
    레이팅 모듈이 생기면 이 인자에 값을 넣어 3중 블렌딩으로 확장할 자리만
    미리 마련해둔 것 - 지금은 로직에 전혀 관여하지 않는다.
    """
    if current is None and previous is None:
        return None
    if current is None:
        return previous
    if previous is None:
        return current

    w_current = compute_current_season_weight(current.games, k)
    w_previous = 1 - w_current

    return TeamAverages(
        team_id=current.team_id,
        games=current.games + previous.games,
        avg_scored=current.avg_scored * w_current + previous.avg_scored * w_previous,
        avg_conceded=current.avg_conceded * w_current + previous.avg_conceded * w_previous,
    )
