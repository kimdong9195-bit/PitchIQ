"""
compute_team_outlook.py - AI 팀 분석 전망 배치

이번 시즌 EPL 참가팀의 "남은 경기 전체"를 predictor.analyze_match()로
분석해서 predictions 테이블에 prediction_type='team_outlook'으로 저장하고,
팀별로 집계해서 team_outlook.json을 만든다.

predictor.py, backtest/ 전혀 안 건드림 - app.py가 쓰는 것과 동일한
api_client/data_mapper/predictor 조합을 그대로 재사용한다.

핵심 원칙:
- 라인업은 스킵 (한참 남은 미래경기라 발표 전이라 의미 없음)
- 팀별 최근폼/선수통계/부상정보는 팀당 딱 1번만 조회해서 캐싱
- 계산식은 임의 가중치 없이 단순 합산/평균만 사용:
    1X2 -> 기대승점(Expected Points) 합산
    BTTS/O-U/핸디캡 -> 남은 경기 확률 평균
    득점 -> venue별(홈경기만/원정경기만) λ 평균
- 집계 시 fixture당 "가장 최근 스냅샷"만 사용

실행: python compute_team_outlook.py
"""

import json
import sys

sys.path.insert(0, ".")

import api_client
import data_mapper
import predictor
import prediction_log

LEAGUE_ID_EPL = 39
CURRENT_SEASON = 2026


def build_team_object(team_id, team_name, season, cache):
    if team_id in cache:
        return cache[team_id]
    fixtures = api_client.get_recent_fixtures(team_id, season=season, count=5)
    players = api_client.get_team_players(team_id, season)
    injuries = api_client.get_injuries(team_id, season)
    team_obj = data_mapper.build_team(
        name=team_name, team_id=team_id, fixtures=fixtures,
        players_data=players, injuries_data=injuries, core_player_names=None,
    )
    cache[team_id] = team_obj
    return team_obj


def main():
    teams = api_client.get_league_teams(LEAGUE_ID_EPL, CURRENT_SEASON)
    print(f"참가팀 수: {len(teams)}")
    team_name_by_id = {t["id"]: t["name"] for t in teams}
    team_logo_by_name = {t["name"]: t.get("logo") for t in teams}

    unique_fixtures = {}
    for t in teams:
        for fx in api_client.get_remaining_fixtures(t["id"], CURRENT_SEASON):
            unique_fixtures[fx["fixture"]["id"]] = fx
    print(f"남은 고유 경기 수: {len(unique_fixtures)}")

    team_cache = {}
    saved, errors = 0, 0

    for fid, fx in unique_fixtures.items():
        home_id = fx["teams"]["home"]["id"]
        away_id = fx["teams"]["away"]["id"]
        home_name = team_name_by_id.get(home_id, fx["teams"]["home"]["name"])
        away_name = team_name_by_id.get(away_id, fx["teams"]["away"]["name"])
        match_date = fx["fixture"]["date"][:10]

        try:
            home_obj = build_team_object(home_id, home_name, CURRENT_SEASON, team_cache)
            away_obj = build_team_object(away_id, away_name, CURRENT_SEASON, team_cache)
            h2h_raw = api_client.get_head_to_head(home_id, away_id, count=6)
            h2h_matches = data_mapper.h2h_to_h2h_matches(h2h_raw, home_id)

            result = predictor.analyze_match(home_obj, away_obj, h2h_matches)
            result["lineups"] = {}

            prediction_log.record_prediction(
                fixture_id=fid, match_date=match_date,
                home_team=home_name, away_team=away_name,
                result=result, prediction_type="team_outlook",
                kickoff_time=fx["fixture"]["date"],
            )
            saved += 1
        except Exception as e:
            print(f"  [오류] fixture_id={fid} ({home_name} vs {away_name}) 처리 실패: {e} - 건너뜀")
            errors += 1
            continue

    print(f"예측 저장 완료: {saved}건 성공, {errors}건 오류\n")

    snapshots = prediction_log.get_latest_snapshot_per_fixture(prediction_type="team_outlook", unresolved_only=True)
    print(f"집계 대상 스냅샷 수: {len(snapshots)}")

    team_agg = {}

    for s in snapshots:
        home, away = s["home_team"], s["away_team"]
        for team, is_home in [(home, True), (away, False)]:
            agg = team_agg.setdefault(team, {
                "xp": 0.0, "btts": [], "o25": [], "hcap": [],
                "lambda_home": [], "lambda_away": [], "remaining": 0,
            })
            agg["remaining"] += 1
            agg["btts"].append(s["prob_btts_yes"])
            agg["o25"].append(s["prob_over_2_5"])
            if is_home:
                agg["xp"] += 3 * s["prob_home_win"] + 1 * s["prob_draw"]
                agg["lambda_home"].append(s["lambda_home"])
                agg["hcap"].append(s["prob_handicap_home"])
            else:
                agg["xp"] += 3 * s["prob_away_win"] + 1 * s["prob_draw"]
                agg["lambda_away"].append(s["lambda_away"])
                agg["hcap"].append(s["prob_handicap_away"])

    def avg(lst):
        return sum(lst) / len(lst) if lst else None

    outlook = {
        "disclaimer": (
            "이 순위는 실제 EPL 순위가 아니라, 남은 EPL 일정에 대한 AI 분석 전망입니다. "
            "BTTS/오버언더/핸디캡은 팀 자체의 절대적인 능력치가 아니라, 잔여 일정의 "
            "상대팀 전력과 홈/원정 조건에 따라 달라지는 상대적 지표입니다."
        ),
        # 20팀 x 38경기 기준으로 "팀당 평균 몇 경기 치렀는지" 역산 - 시즌이
        # 얼마나 진행됐는지 화면에 보여주기 위한 값 (표본 크기 체감용).
        "avg_matches_played": round(38 - (len(unique_fixtures) * 2 / max(len(teams), 1)), 1),
        # 리그 전체 기준 "지금까지 몇 경기 치렀는지 / 앞으로 몇 경기 남았는지"
        # (팀별 평균이 아니라 리그 전체 숫자 - 화면에 "현재까지 50경기 진행 /
        # 잔여 330경기" 형태로 그대로 표시하기 위함)
        "matches_remaining_total": len(unique_fixtures),
        "matches_played_total": 380 - len(unique_fixtures),
        "1x2": [], "btts": [], "o25": [], "handicap": [], "score_home": [], "score_away": [],
    }

    for team, agg in team_agg.items():
        remaining = agg["remaining"]
        logo = team_logo_by_name.get(team)
        outlook["1x2"].append({"team": team, "logo": logo, "value": round(agg["xp"] / remaining, 2), "remaining": remaining})
        if agg["btts"]:
            outlook["btts"].append({"team": team, "logo": logo, "value": round(avg(agg["btts"]) * 100, 1), "remaining": remaining})
        if agg["o25"]:
            outlook["o25"].append({"team": team, "logo": logo, "value": round(avg(agg["o25"]) * 100, 1), "remaining": remaining})
        if agg["hcap"]:
            outlook["handicap"].append({"team": team, "logo": logo, "value": round(avg(agg["hcap"]) * 100, 1), "remaining": remaining})
        if agg["lambda_home"]:
            outlook["score_home"].append({"team": team, "logo": logo, "value": round(avg(agg["lambda_home"]), 2), "remaining": len(agg["lambda_home"])})
        if agg["lambda_away"]:
            outlook["score_away"].append({"team": team, "logo": logo, "value": round(avg(agg["lambda_away"]), 2), "remaining": len(agg["lambda_away"])})

    for key in ["1x2", "btts", "o25", "handicap", "score_home", "score_away"]:
        outlook[key].sort(key=lambda r: r["value"], reverse=True)

    with open("team_outlook.json", "w", encoding="utf-8") as f:
        json.dump(outlook, f, ensure_ascii=False, indent=2)

    print("team_outlook.json 저장 완료.")
    for key, label in [("1x2", "승무패(기대승점)"), ("btts", "BTTS%"), ("o25", "O/U%"),
                        ("handicap", "핸디캡%"), ("score_home", "홈득점 λ"), ("score_away", "원정득점 λ")]:
        print(f"\n[{label}] TOP5")
        for row in outlook[key][:5]:
            print(f"  {row['team']:<20} {row['value']}  (잔여 {row['remaining']}경기)")


if __name__ == "__main__":
    main()
