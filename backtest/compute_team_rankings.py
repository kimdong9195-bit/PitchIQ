"""
backtest/compute_team_rankings.py - 홈화면 "우리 모델이 잘 맞추는 팀 TOP5" 표용 데이터 생성

2023-24+2024-25+2025-26 전체(백테스트로 이미 검증된 기간)를 확정
파라미터+alpha로 다시 돌려서, 팀별로 1X2/BTTS/O-U/핸디캡 각각 몇 %
적중했는지 집계한다. 표본이 너무 적은 팀은 제외하고 TOP5를 뽑는다.

결과는 team_rankings.json으로 저장 - 이 파일을 웹앱 저장소 루트에 넣으면
app.py가 홈화면(분석 전 빈 화면)에 그대로 표시한다.

predictor.py, backtest/의 다른 파일은 전혀 수정하지 않는다.

실행: python backtest/compute_team_rankings.py
"""

import json
import sys

sys.path.insert(0, ".")

from backtest.experiment_calibration import apply_shrinkage_1x2, apply_shrinkage_binary
from backtest.scoring_models.poisson_dc import PoissonDixonColesModel
from backtest.season_split import FINAL_VALIDATION_SEASON
from backtest.walk_forward import run_walk_forward_backtest

LEAGUE_ID_EPL = 39
# 중요: 2023-24+2024-25는 파라미터/alpha를 "구하는 데" 쓴 데이터라 여기 쓰면 안 됨
# (자기가 맞춘 답으로 자기 실력을 자랑하는 꼴). 이 랭킹은 반드시 그 확정된
# 모델을 순수 holdout인 2025-26에 적용한 결과만 써야 진짜 실력을 보여준다.
RANKING_SEASON = [FINAL_VALIDATION_SEASON]  # = [2025], 즉 2025-26 시즌만
ALPHAS = {"1x2": 0.85, "btts": 0.30, "o25": 0.45, "handicap": 1.00}
MIN_MATCHES_PER_TEAM = 10
TOP_N = 5


def main():
    with open("optimization_results.json", "r", encoding="utf-8") as f:
        opt = json.load(f)
    best_params = opt["best_params"]
    print(f"고정 파라미터 사용: {best_params}")

    records = run_walk_forward_backtest(LEAGUE_ID_EPL, RANKING_SEASON, best_params, PoissonDixonColesModel())
    print(f"대상 시즌: {RANKING_SEASON} (2025-26 holdout만 - 파라미터 최적화에 안 쓰인 순수 검증구간)")
    print(f"전체 경기 수: {len(records)}")

    team_stats = {}

    for r in records:
        home, away = r["home_team"], r["away_team"]
        hg, ag = r["actual_home_goals"], r["actual_away_goals"]

        x1x2_shrunk = apply_shrinkage_1x2([r["market_1x2"]], ALPHAS["1x2"])[0]
        btts_raw = r["market_btts"]["yes"]
        btts_shrunk = apply_shrinkage_binary([btts_raw], ALPHAS["btts"])[0]
        o25_raw = r["market_over_under"].get(2.5, {}).get("over", 0.5)
        o25_shrunk = apply_shrinkage_binary([o25_raw], ALPHAS["o25"])[0]
        line = list(r["market_handicap"].keys())[0] if r["market_handicap"] else -1.5
        hcap_home_raw = r["market_handicap"].get(line, {}).get("home_cover_equity", 0.5)
        hcap_home_shrunk = apply_shrinkage_binary([hcap_home_raw], ALPHAS["handicap"])[0]

        actual_1x2 = "home_win" if hg > ag else ("away_win" if hg < ag else "draw")
        pred_1x2 = max(x1x2_shrunk, key=x1x2_shrunk.get)
        hit_1x2 = pred_1x2 == actual_1x2

        actual_btts = (hg >= 1 and ag >= 1)
        hit_btts = (btts_shrunk > 0.5) == actual_btts

        actual_o25 = (hg + ag) > 2.5
        hit_o25 = (o25_shrunk > 0.5) == actual_o25

        actual_hcap_home = (hg - ag + float(line)) > 0
        hit_hcap = (hcap_home_shrunk > 0.5) == actual_hcap_home

        for team in (home, away):
            s = team_stats.setdefault(team, {"1x2": [0, 0], "btts": [0, 0], "o25": [0, 0], "handicap": [0, 0]})
            s["1x2"][1] += 1
            s["1x2"][0] += int(hit_1x2)
            s["btts"][1] += 1
            s["btts"][0] += int(hit_btts)
            s["o25"][1] += 1
            s["o25"][0] += int(hit_o25)
            s["handicap"][1] += 1
            s["handicap"][0] += int(hit_hcap)

    rankings = {}
    for market in ["1x2", "btts", "o25", "handicap"]:
        eligible = [
            (team, s[market][0] / s[market][1], s[market][1])
            for team, s in team_stats.items() if s[market][1] >= MIN_MATCHES_PER_TEAM
        ]
        eligible.sort(key=lambda t: t[1], reverse=True)
        rankings[market] = [
            {"team": team, "hit_rate": round(rate * 100, 1), "matches": n}
            for team, rate, n in eligible[:TOP_N]
        ]

    with open("team_rankings.json", "w", encoding="utf-8") as f:
        json.dump(rankings, f, ensure_ascii=False, indent=2)

    print("\nteam_rankings.json 저장 완료. 각 시장별 TOP5:")
    for market, label in [("1x2", "1X2"), ("btts", "BTTS"), ("o25", "O/U 2.5"), ("handicap", "핸디캡")]:
        print(f"\n[{label}]")
        for row in rankings[market]:
            print(f"  {row['team']:<20} 적중률 {row['hit_rate']}%  (표본 {row['matches']}경기)")


if __name__ == "__main__":
    main()
