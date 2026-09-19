"""
backtest/markets.py - STEP 9: score_matrix에서 6개 시장 확률 도출

입력은 항상 STEP 8(scoring_models/*.score_matrix())이 만든
{(home_goals, away_goals): probability} 형태의 score_matrix 하나뿐이다.
이 파일은 어떤 모델(Poisson+DC든 나중의 Negative Binomial이든)이 그
score_matrix를 만들었는지 전혀 몰라도 동작한다 (STEP 7/8/9 책임 분리 유지).

핸디캡은 Asian Handicap이다 (3-way 유러피언 핸디캡 아님) - 푸시(무효)가
있을 수 있고, 쿼터라인(.25, .75)도 지원한다. 기존 실서비스(predictor.py)의
handicap_prob()와 동일한 관례를 따른다.
"""

from typing import Dict, Tuple

ScoreMatrix = Dict[Tuple[int, int], float]


def market_1x2(score_matrix: ScoreMatrix) -> dict:
    """승/무/패. home_win + draw + away_win = 1.0 (score_matrix가 이미 정규화되어 있으므로)."""
    home_win = sum(p for (h, a), p in score_matrix.items() if h > a)
    draw = sum(p for (h, a), p in score_matrix.items() if h == a)
    away_win = sum(p for (h, a), p in score_matrix.items() if h < a)
    return {"home_win": home_win, "draw": draw, "away_win": away_win}


def market_btts(score_matrix: ScoreMatrix) -> dict:
    """양팀득점. 두 팀 다 1골 이상."""
    yes = sum(p for (h, a), p in score_matrix.items() if h >= 1 and a >= 1)
    return {"yes": yes, "no": 1 - yes}


def market_over_under(score_matrix: ScoreMatrix, line: float = 2.5) -> dict:
    """
    오버/언더. line은 기본 2.5지만 임의의 라인을 받을 수 있다
    (0.5, 1.5, 3.5 등 - 정수+0.5 형태를 가정, 정수 라인은 여기서 다루지 않음
    - 축구 오버언더는 관례적으로 항상 .5 라인을 쓰므로).
    """
    over = sum(p for (h, a), p in score_matrix.items() if (h + a) > line)
    under = 1 - over
    return {"line": line, "over": over, "under": under}


def _grade_fraction(diff: int, line: float) -> float:
    """
    단일 라인 기준 커버 비율. 이김=1.0, 푸시=0.5, 짐=0.0 (블렌딩 계산용 내부 스케일).
    diff = home_goals - away_goals, line은 홈팀 기준 핸디캡.
    """
    adjusted = diff + line
    if adjusted > 1e-9:
        return 1.0
    elif abs(adjusted) < 1e-9:
        return 0.5
    else:
        return 0.0


def market_handicap(score_matrix: ScoreMatrix, line: float) -> dict:
    """
    아시안 핸디캡. line은 홈팀 기준 (예: -1.5면 홈팀이 2골차 이상 이겨야 커버).

    쿼터라인(.25, .75)은 실제 아시안핸디캡 관례대로 두 개의 인접 라인에 절반씩
    베팅한 것으로 정확히 처리한다 - 그냥 두 라인 결과를 뭉뚱그려 "푸시"로
    퉁치지 않고, full_win/half_win/half_loss/full_loss 4단계로 정확히 구분한다.
    정수 라인(0, ±1, ±2...)만 진짜 푸시(무효)가 존재한다.

    반환: home_full_win, home_half_win, push, home_half_loss, home_full_loss
    (정수/반정수 라인이면 half_win/half_loss는 항상 0, 쿼터라인이면 push가 항상 0)
    그리고 하위호환용 home_cover_equity(0~1 사이 기대비율)도 같이 준다.
    """
    line_x4 = round(line * 4)
    is_quarter = (line_x4 % 2 != 0)  # .25 또는 .75로 끝나는 라인인지

    outcomes = {
        "home_full_win": 0.0, "home_half_win": 0.0,
        "push": 0.0,
        "home_half_loss": 0.0, "home_full_loss": 0.0,
    }
    home_cover_equity = 0.0

    for (h, a), p in score_matrix.items():
        diff = h - a
        if is_quarter:
            f = (_grade_fraction(diff, line - 0.25) + _grade_fraction(diff, line + 0.25)) / 2
        else:
            f = _grade_fraction(diff, line)

        home_cover_equity += p * f

        if f >= 0.999:
            outcomes["home_full_win"] += p
        elif f >= 0.749:
            outcomes["home_half_win"] += p
        elif f >= 0.499:
            outcomes["push"] += p
        elif f >= 0.249:
            outcomes["home_half_loss"] += p
        else:
            outcomes["home_full_loss"] += p

    outcomes["line"] = line
    outcomes["is_quarter_line"] = is_quarter
    outcomes["home_cover_equity"] = home_cover_equity
    # 하위호환: 기존 3버킷(home_cover/push/away_cover)도 같이 제공
    outcomes["home_cover"] = outcomes["home_full_win"] + outcomes["home_half_win"]
    outcomes["away_cover"] = outcomes["home_full_loss"] + outcomes["home_half_loss"]
    return outcomes


def market_home_goals(score_matrix: ScoreMatrix, max_display: int = 4) -> dict:
    """
    홈팀 득점 분포. 0골, 1골, ..., (max_display-1)골, max_display골 이상으로 묶는다.
    """
    dist = {str(k): 0.0 for k in range(max_display)}
    dist[f"{max_display}+"] = 0.0
    for (h, a), p in score_matrix.items():
        key = str(h) if h < max_display else f"{max_display}+"
        dist[key] += p
    return dist


def market_away_goals(score_matrix: ScoreMatrix, max_display: int = 4) -> dict:
    """원정팀 득점 분포. market_home_goals와 동일한 방식, away 기준."""
    dist = {str(k): 0.0 for k in range(max_display)}
    dist[f"{max_display}+"] = 0.0
    for (h, a), p in score_matrix.items():
        key = str(a) if a < max_display else f"{max_display}+"
        dist[key] += p
    return dist


def compute_all_markets(score_matrix: ScoreMatrix, ou_line: float = 2.5, handicap_line: float = 0.0) -> dict:
    """6개 시장을 한 번에 계산 (STEP 10 백테스트 루프가 매 경기 이걸 한 번 호출하면 됨)."""
    return {
        "1x2": market_1x2(score_matrix),
        "btts": market_btts(score_matrix),
        "over_under": market_over_under(score_matrix, ou_line),
        "handicap": market_handicap(score_matrix, handicap_line),
        "home_goals": market_home_goals(score_matrix),
        "away_goals": market_away_goals(score_matrix),
    }
