"""
predictor.py - 축구 경기 예측 핵심 로직 (API 연동과 분리된 순수 계산 모듈)

이 파일은 이전에 만든 soccer_predictor_with_core_player.py의 계산 로직을
"웹 서비스에서 재사용 가능한 함수" 형태로 옮긴 것입니다.
print() 대신 analyze_match()가 결과를 dict로 반환합니다.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional


# ----------------------------
# 1. 데이터 구조
# ----------------------------

@dataclass
class RecentMatch:
    goals_for: int
    goals_against: int
    is_home: bool


@dataclass
class H2HMatch:
    team_a_goals: int
    team_b_goals: int
    team_a_home: bool


@dataclass
class CorePlayer:
    name: str
    season_goals: int
    season_minutes: float
    is_available: bool = True
    team_goals_per90_with: Optional[float] = None
    team_goals_per90_without: Optional[float] = None
    backup_goals: Optional[int] = None
    backup_minutes: Optional[float] = None


@dataclass
class Team:
    name: str
    recent_matches: List[RecentMatch] = field(default_factory=list)
    core_players: List[CorePlayer] = field(default_factory=list)


LEAGUE_AVG_GOALS = 1.35
HOME_ADVANTAGE = 1.15
AWAY_DISADVANTAGE = 0.90
DECAY = 0.85
H2H_MAX_WEIGHT = 0.35
DEFAULT_COMPENSATION_FACTOR = 0.65


# ----------------------------
# 2. 최근 폼 기반 가중 평균
# ----------------------------

def weighted_form(team: Team):
    if not team.recent_matches:
        return LEAGUE_AVG_GOALS, LEAGUE_AVG_GOALS

    total_weight = 0.0
    scored_sum = 0.0
    conceded_sum = 0.0

    for i, m in enumerate(team.recent_matches):
        w = DECAY ** i
        scored_sum += m.goals_for * w
        conceded_sum += m.goals_against * w
        total_weight += w

    return scored_sum / total_weight, conceded_sum / total_weight


# ----------------------------
# 3. 상대전적(H2H) 기반 기대 득점
# ----------------------------

def h2h_expected_goals(h2h_matches: List[H2HMatch]):
    if not h2h_matches:
        return None, None, 0
    a_goals_total = sum(m.team_a_goals for m in h2h_matches)
    b_goals_total = sum(m.team_b_goals for m in h2h_matches)
    n = len(h2h_matches)
    return a_goals_total / n, b_goals_total / n, n


# ----------------------------
# 4. 핵심 선수 결장 보정
# ----------------------------

def calculate_per90(goals: int, minutes: float) -> float:
    if minutes <= 0:
        return 0.0
    return goals / minutes * 90


def calculate_goal_share(player: CorePlayer, team_avg_goals_per_game: float) -> float:
    if team_avg_goals_per_game <= 0:
        return 0.0
    per90 = calculate_per90(player.season_goals, player.season_minutes)
    return min(per90 / team_avg_goals_per_game, 1.0)


def determine_compensation_factor(player: CorePlayer):
    if (
        player.team_goals_per90_with is not None
        and player.team_goals_per90_without is not None
        and player.team_goals_per90_with > 0
    ):
        loss = 1 - (player.team_goals_per90_without / player.team_goals_per90_with)
        return max(0.0, min(loss, 1.0)), "On/Off 실측"

    if player.backup_goals is not None and player.backup_minutes is not None:
        player_per90 = calculate_per90(player.season_goals, player.season_minutes)
        backup_per90 = calculate_per90(player.backup_goals, player.backup_minutes)
        if player_per90 > 0:
            loss = 1 - (backup_per90 / player_per90)
            return max(0.0, min(loss, 1.0)), "백업 선수 비교"

    return DEFAULT_COMPENSATION_FACTOR, "기본값(데이터 부족)"


def core_player_adjustment(team: Team, team_avg_goals_per_game: float):
    total_loss = 0.0
    details = []
    for player in team.core_players:
        goal_share = calculate_goal_share(player, team_avg_goals_per_game)
        if not player.is_available:
            comp_factor, method = determine_compensation_factor(player)
            loss = goal_share * comp_factor
            total_loss += loss
            details.append({
                "name": player.name, "available": False,
                "goal_share": goal_share, "compensation_factor": comp_factor,
                "method": method, "loss": loss,
            })
        else:
            details.append({
                "name": player.name, "available": True,
                "goal_share": goal_share, "compensation_factor": None,
                "method": None, "loss": 0.0,
            })
    adjustment = max(1 - total_loss, 0.1)
    return adjustment, details


# ----------------------------
# 5. 최종 기대 득점(λ)
# ----------------------------

def blended_expected_goals(home: Team, away: Team, h2h_matches: List[H2HMatch]):
    home_scored, home_conceded = weighted_form(home)
    away_scored, away_conceded = weighted_form(away)

    home_attack = home_scored / LEAGUE_AVG_GOALS
    away_defense = away_conceded / LEAGUE_AVG_GOALS
    away_attack = away_scored / LEAGUE_AVG_GOALS
    home_defense = home_conceded / LEAGUE_AVG_GOALS

    form_home_lambda = home_attack * away_defense * LEAGUE_AVG_GOALS * HOME_ADVANTAGE
    form_away_lambda = away_attack * home_defense * LEAGUE_AVG_GOALS * AWAY_DISADVANTAGE

    h2h_home_goals, h2h_away_goals, n_h2h = h2h_expected_goals(h2h_matches)

    if n_h2h == 0:
        base_home_lambda, base_away_lambda = form_home_lambda, form_away_lambda
        h2h_weight = 0.0
    else:
        h2h_weight = min(n_h2h / 10, 1.0) * H2H_MAX_WEIGHT
        base_home_lambda = (1 - h2h_weight) * form_home_lambda + h2h_weight * h2h_home_goals
        base_away_lambda = (1 - h2h_weight) * form_away_lambda + h2h_weight * h2h_away_goals

    home_adj, home_details = core_player_adjustment(home, home_scored)
    away_adj, away_details = core_player_adjustment(away, away_scored)

    final_home_lambda = base_home_lambda * home_adj
    final_away_lambda = base_away_lambda * away_adj

    return {
        "home_lambda": final_home_lambda, "away_lambda": final_away_lambda,
        "h2h_weight": h2h_weight,
        "home_adjustment": home_adj, "away_adjustment": away_adj,
        "home_core_player_details": home_details, "away_core_player_details": away_details,
    }


# ----------------------------
# 6. 포아송 확률 계산 (+ Dixon-Coles 저득점 보정)
# ----------------------------

def poisson_prob(lam: float, k: int) -> float:
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


# Dixon-Coles(1997) 저득점 상관관계 보정계수.
# 독립 포아송 모델은 실제 데이터보다 0-0, 1-1 같은 저득점 무승부를 과소평가하고
# 1-0, 0-1 같은 한 골 차 승부를 과대평가하는 경향이 있다는 게 알려져 있어서,
# 이 네 스코어에만 보정을 가한다. RHO는 원 논문에서 추정된 값대(-0.1~-0.15) 범위로 설정.
DIXON_COLES_RHO = -0.13

# 경기 맥락별 보정값. 세 가지는 서로 다른 메커니즘이라 따로 관리한다.
#
# - 타이틀경쟁/상위권 맞대결: 실력 있는 두 팀이 실수를 피하려 신중하게 플레이 ->
#   득점 자체가 줄고, 무승부가 잦음. (예: 2023-24 시즌 맨시티-리버풀, 아스널-맨시티가
#   실제로 다 저득점 무승부였는데 일반 모델은 한쪽 승리를 과신했던 사례로 확인됨)
#   -> 기대득점을 낮추고(CAUTION) 무승부 보정(RHO)도 강하게.
#
# - 지역 더비/라이벌전: 감정적으로 격해져서 화끈하게 터지거나(대승) 팽팽하게
#   막히거나(무승부) 하는 양극화된 패턴. 머지사이드 더비 실제 기록(248경기 중 78무,
#   최근 22경기 중 12무)을 보면 무승부율은 매우 높지만 7-4, 6-0 같은 대승 기록도
#   있어서 "득점 자체가 줄어드는" 건 아님.
#   -> 기대득점은 그대로 두고, 무승부 보정(RHO)만 강하게.
#
# - 강등권 다툼: 팀 실력 자체가 낮은데 긴장감까지 더해져서 결정적 찬스가 적고
#   답답하게 흘러가는 경우가 많다는 정황(기사/해설 논조)을 반영.
#   -> 기대득점을 낮추고 무승부 보정도 강하게 (타이틀경쟁과 유사하지만 조금 더 세게).
#
# 셋 다 데이터로 정밀하게 튜닝한 값이 아니라, 관찰된 경향을 반영한 휴리스틱이다.
TITLE_RACE_CAUTION = 0.85
TITLE_RACE_RHO = -0.30

LOCAL_DERBY_CAUTION = 1.0  # 득점 기댓값은 건드리지 않음
LOCAL_DERBY_RHO = -0.35

RELEGATION_CAUTION = 0.80
RELEGATION_RHO = -0.30


# ----------------------------
# 6-1. 확률 보정 (Shrinkage) - 2025-26 holdout으로 검증 완료된 값
#
# STEP1~13 백테스트(backtest/ 폴더)에서 시장별로 독립적으로 alpha를 탐색하고,
# 2023-24+2024-25 최적화 -> 2025-26 holdout 검증까지 거쳐 확정된 값이다.
# 여기서는 그 결과값을 "그대로 가져다 쓰기만" 한다 - 이 파일 안에서 alpha를
# 다시 추정하거나 조정하지 않는다 (재조정은 backtest/ 쪽에서만 일어난다).
#
# 공식: p' = center + alpha*(p-center)
#   - 1X2처럼 K개 클래스가 서로 합해서 1이 되어야 하는 경우 center=1/K
#     (K=3이면 1/3) -> 이러면 보정 후에도 자동으로 합계가 1로 유지된다
#     (증명: sum[center + alpha*(p_c-center)] = K*center + alpha*(sum(p_c) - K*center)
#            = 1 + alpha*(1-1) = 1, center=1/K일 때이므로 K*center=1)
#   - BTTS/오버언더처럼 이진(둘 중 하나) 시장은 center=0.5
#
# Handicap은 alpha=1.00으로 확정되어(holdout에서도 변화 없음), 사실상
# shrink(p, 1.0, ...) = p 항등식이라 여기서 별도 코드를 추가하지 않는다
# (사용자 확인: "손대지 않고 원본 그대로 유지"로 결정됨).
# ----------------------------
SHRINKAGE_ALPHA_1X2 = 0.85    # center = 1/3
SHRINKAGE_ALPHA_BTTS = 0.30   # center = 0.5
SHRINKAGE_ALPHA_OU = 0.45     # center = 0.5


def shrink(p: float, alpha: float, center: float = 0.5) -> float:
    return center + alpha * (p - center)


def _assert_sums_to_one(probs: dict, label: str):
    total = sum(probs.values())
    assert abs(total - 1.0) < 1e-9, f"{label} 확률 합이 1이 아닙니다: {total}"


def dixon_coles_tau(h: int, a: int, home_lambda: float, away_lambda: float, rho: float) -> float:
    if h == 0 and a == 0:
        return 1 - (home_lambda * away_lambda * rho)
    elif h == 0 and a == 1:
        return 1 + (home_lambda * rho)
    elif h == 1 and a == 0:
        return 1 + (away_lambda * rho)
    elif h == 1 and a == 1:
        return 1 - rho
    return 1.0


def match_outcome_probs(home_lambda: float, away_lambda: float, max_goals: int = 8, rho: float = DIXON_COLES_RHO):
    home_win = draw = away_win = 0.0
    over_2_5 = under_2_5 = 0.0
    score_matrix = {}

    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson_prob(home_lambda, h) * poisson_prob(away_lambda, a)
            p *= dixon_coles_tau(h, a, home_lambda, away_lambda, rho)
            score_matrix[(h, a)] = p

    # tau 보정과 max_goals 절단으로 총합이 1에서 살짝 벗어나므로 재정규화
    total = sum(score_matrix.values())
    for key in score_matrix:
        score_matrix[key] /= total

    for (h, a), p in score_matrix.items():
        if h > a:
            home_win += p
        elif h == a:
            draw += p
        else:
            away_win += p
        if h + a >= 3:
            over_2_5 += p
        else:
            under_2_5 += p

    return {
        "home_win": home_win, "draw": draw, "away_win": away_win,
        "over_2_5": over_2_5, "under_2_5": under_2_5,
        "score_matrix": score_matrix,
    }


def most_likely_scores(score_matrix: dict, top_n: int = 3):
    top = sorted(score_matrix.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return [{"home_goals": h, "away_goals": a, "probability": p} for (h, a), p in top]


def btts_prob(score_matrix: dict):
    yes = sum(p for (h, a), p in score_matrix.items() if h >= 1 and a >= 1)
    return yes, 1 - yes


def draw_no_bet_prob(result: dict):
    """
    무승부를 제외하고 홈/원정 승리 확률만 재정규화 (Draw No Bet 마켓).
    빅매치처럼 무승부가 자주 나올 것 같은 경기에서, 승무패 하나를 억지로
    찍기보다 '무승부면 환불, 이기면 적중'인 이 마켓이 훨씬 안전한 픽이 된다.
    """
    home_win, away_win = result["home_win"], result["away_win"]
    total = home_win + away_win
    if total == 0:
        return 0.5, 0.5
    return home_win / total, away_win / total


def handicap_prob(score_matrix: dict, handicap: float):
    def _single_line(h_line):
        home_cover = push = away_cover = 0.0
        for (h, a), p in score_matrix.items():
            diff = (h + h_line) - a
            if diff > 0:
                home_cover += p
            elif diff == 0:
                push += p
            else:
                away_cover += p
        return home_cover, push, away_cover

    remainder = round(abs(handicap) % 0.5, 2)
    if remainder == 0.25:
        lines = [handicap - 0.25, handicap + 0.25]
        results = [_single_line(l) for l in lines]
        return tuple(sum(r[i] for r in results) / 2 for i in range(3))

    return _single_line(handicap)


def suggest_handicap_line(home_lambda: float, away_lambda: float) -> float:
    margin = home_lambda - away_lambda
    return round(margin * 2) / 2 * -1


# ----------------------------
# 7. 최종 분석 함수 (웹 서비스에서 호출하는 진입점)
# ----------------------------

def analyze_match(
    home: Team, away: Team, h2h_matches: List[H2HMatch],
    title_race: bool = False, local_derby: bool = False, relegation_battle: bool = False,
) -> dict:
    """
    두 팀과 상대전적 데이터를 받아 전체 분석 결과를 dict로 반환한다.
    Flask 라우트에서 이 함수 하나만 호출하면 됨.

    title_race / local_derby / relegation_battle 는 서로 다른 메커니즘으로 보정한다
    (각 상수의 주석 참고). 여러 개를 동시에 체크하면 효과가 누적된다.
    이 판단은 통계로 자동 감지하기 어려운 "맥락" 정보라서 사용자가 직접 체크한다.
    """
    lambdas = blended_expected_goals(home, away, h2h_matches)
    home_lambda = lambdas["home_lambda"]
    away_lambda = lambdas["away_lambda"]

    caution_factor = 1.0
    rho = DIXON_COLES_RHO

    if title_race:
        caution_factor *= TITLE_RACE_CAUTION
        rho = min(rho, TITLE_RACE_RHO)
    if relegation_battle:
        caution_factor *= RELEGATION_CAUTION
        rho = min(rho, RELEGATION_RHO)
    if local_derby:
        rho = min(rho, LOCAL_DERBY_RHO)

    home_lambda *= caution_factor
    away_lambda *= caution_factor

    is_special_context = title_race or local_derby or relegation_battle

    result = match_outcome_probs(home_lambda, away_lambda, rho=rho)
    btts_yes, btts_no = btts_prob(result["score_matrix"])

    # ---- 확률 보정 (Shrinkage) 적용 ----
    # score_matrix/λ는 전혀 안 건드리고, 이미 계산이 끝난 "확률값"에만
    # 사후적으로 적용한다. 이 지점 이후로는 result["home_win"] 등 원본값을
    # 직접 쓰지 않고 아래 보정된 값(*_shrunk)만 쓴다.
    home_win_shrunk = shrink(result["home_win"], SHRINKAGE_ALPHA_1X2, center=1 / 3)
    draw_shrunk = shrink(result["draw"], SHRINKAGE_ALPHA_1X2, center=1 / 3)
    away_win_shrunk = shrink(result["away_win"], SHRINKAGE_ALPHA_1X2, center=1 / 3)
    _assert_sums_to_one(
        {"home_win": home_win_shrunk, "draw": draw_shrunk, "away_win": away_win_shrunk}, "1X2(보정후)"
    )

    over_2_5_shrunk = shrink(result["over_2_5"], SHRINKAGE_ALPHA_OU, center=0.5)
    under_2_5_shrunk = 1 - over_2_5_shrunk  # 이진쌍이라 한쪽만 보정하고 나머지는 1-x로 - 합계 자동 보장
    _assert_sums_to_one({"over": over_2_5_shrunk, "under": under_2_5_shrunk}, "오버언더(보정후)")

    btts_yes_shrunk = shrink(btts_yes, SHRINKAGE_ALPHA_BTTS, center=0.5)
    btts_no_shrunk = 1 - btts_yes_shrunk
    _assert_sums_to_one({"yes": btts_yes_shrunk, "no": btts_no_shrunk}, "BTTS(보정후)")

    # DNB: 사용자 확인 결과 "보정된 1X2 값을 그대로 사용"으로 결정됨 -
    # 기존처럼 result 딕셔너리 형태를 넘기되, 안의 값만 보정된 값으로 교체.
    dnb_home, dnb_away = draw_no_bet_prob(
        {"home_win": home_win_shrunk, "away_win": away_win_shrunk}
    )

    # 특수 맥락이면 핸디캡 라인을 더 보수적으로(작게) 잡는다
    suggested_line = suggest_handicap_line(home_lambda, away_lambda)
    if is_special_context and abs(suggested_line) > 0.5:
        suggested_line = -0.5 if suggested_line < 0 else 0.5
    # Handicap: 사용자 확인 결과 alpha=1.00(항등식)이라 "원본 그대로 유지"로 결정됨 - 보정 코드 추가 안 함
    h_cover, push, a_cover = handicap_prob(result["score_matrix"], suggested_line)
    _assert_sums_to_one({"home": h_cover, "push": push, "away": a_cover}, "Handicap(원본, 무보정)")

    probs = {
        "home_win": home_win_shrunk,
        "draw": draw_shrunk,
        "away_win": away_win_shrunk,
    }
    top_outcome = max(probs, key=probs.get)
    top_confidence = probs[top_outcome]
    outcome_label = {"home_win": home.name, "draw": "무승부", "away_win": away.name}[top_outcome]

    # 특수 맥락이면 승무패 신뢰도 기준을 더 보수적으로 (40% -> 55%)
    result_confidence_threshold = 0.55 if is_special_context else 0.40

    picks = {
        "result": {
            "pick": outcome_label if top_confidence >= result_confidence_threshold else "보류(접전 예상)",
            "confidence": round(top_confidence, 4),
        },
        "btts": {
            "pick": "Yes" if btts_yes_shrunk > btts_no_shrunk else "No",
            "confidence": round(max(btts_yes_shrunk, btts_no_shrunk), 4),
        },
        "handicap": {
            "pick": f"{home.name} {suggested_line:+.1f}" if h_cover > a_cover else f"{away.name} {-suggested_line:+.1f}",
            "confidence": round(max(h_cover, a_cover), 4),
        },
        "draw_no_bet": {
            "pick": home.name if dnb_home > dnb_away else away.name,
            "confidence": round(max(dnb_home, dnb_away), 4),
        },
    }

    return {
        "home_team": home.name,
        "away_team": away.name,
        "match_context": {
            "title_race": title_race, "local_derby": local_derby, "relegation_battle": relegation_battle,
            "is_special_context": is_special_context,
        },
        "home_lambda": round(home_lambda, 2),
        "away_lambda": round(away_lambda, 2),
        "h2h_weight": round(lambdas["h2h_weight"], 2),
        "home_core_player_details": lambdas["home_core_player_details"],
        "away_core_player_details": lambdas["away_core_player_details"],
        "probabilities": {
            "home_win": round(home_win_shrunk, 4),
            "draw": round(draw_shrunk, 4),
            "away_win": round(away_win_shrunk, 4),
            "over_2_5": round(over_2_5_shrunk, 4),
            "under_2_5": round(under_2_5_shrunk, 4),
            "btts_yes": round(btts_yes_shrunk, 4),
            "btts_no": round(btts_no_shrunk, 4),
        },
        "most_likely_scores": most_likely_scores(result["score_matrix"]),
        "handicap": {
            "suggested_line": suggested_line,
            "home_cover": round(h_cover, 4),
            "push": round(push, 4),
            "away_cover": round(a_cover, 4),
        },
        "picks": picks,
    }
