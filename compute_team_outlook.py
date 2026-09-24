"""
compute_team_outlook.py - AI 팀 분석 전망 배치

이번 시즌 EPL 참가팀의 "남은 경기 전체"를 predictor.analyze_match()로
분석해서 predictions 테이블에 prediction_type='team_outlook'으로 저장하고,
팀별로 집계해서 team_outlook.json을 만든다.

predictor.py, backtest/ 전혀 안 건드림 - app.py가 쓰는 것과 동일한
api_client/data_mapper/predictor 조합을 그대로 재사용한다. 핸디캡 라인별
재계산도 predictor.py의 기존 함수(match_outcome_probs, handicap_prob)를
그대로 호출만 할 뿐, 새 계산식을 만들지 않는다.

핵심 원칙:
- 라인업은 스킵 (한참 남은 미래경기라 발표 전이라 의미 없음)
- 팀별 최근폼/선수통계/부상정보는 팀당 딱 1번만 조회해서 캐싱
- 계산식은 임의 가중치 없이 단순 합산/평균만 사용
- 집계 시 fixture당 "가장 최근 스냅샷"만 사용
- 1X2/득점은 홈/원정을 분리해서 함께 보여줌 (기존 전체값도 그대로 유지 -
  기존 화면이 안 깨지게)
- 핸디캡은 "그 경기에서 실제로 쓰인 라인"을 평균내는 기존 방식(하위호환용
  으로 유지) 외에, 실제로 이번 배치에서 등장한 라인들 각각에 대해 모든
  팀의 남은 경기에 그 라인을 동일하게 적용했을 때의 커버확률로 별도 순위를
  만든다 (handicap_by_line). 라인 자체는 임의로 만들지 않고, 이번 배치가
  실제로 만들어낸 라인 집합만 사용한다.

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


def avg(lst):
    return sum(lst) / len(lst) if lst else None


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
    fixture_predictions = {}  # fixture_id -> 집계/핸디캡 재계산에 필요한 값들

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

            fixture_predictions[fid] = {
                "home_name": home_name, "away_name": away_name,
                "lambda_home": result["home_lambda"], "lambda_away": result["away_lambda"],
                "prob_home_win": result["probabilities"]["home_win"],
                "prob_draw": result["probabilities"]["draw"],
                "prob_away_win": result["probabilities"]["away_win"],
                "btts_yes": result["probabilities"]["btts_yes"],
                "btts_no": result["probabilities"]["btts_no"],
                "over_2_5": result["probabilities"]["over_2_5"],
                "under_2_5": result["probabilities"]["under_2_5"],
                "suggested_line": result["handicap"]["suggested_line"],
                "home_cover": result["handicap"]["home_cover"],
                "push": result["handicap"]["push"],
                "away_cover": result["handicap"]["away_cover"],
            }
        except Exception as e:
            print(f"  [오류] fixture_id={fid} ({home_name} vs {away_name}) 처리 실패: {e} - 건너뜀")
            errors += 1
            continue

    print(f"예측 저장 완료: {saved}건 성공, {errors}건 오류\n")

    # ------------------------------------------------------------------
    # 1) 1X2 / BTTS / O-U / 득점 집계 (홈/원정 분리 + 기존 전체값 유지)
    # ------------------------------------------------------------------
    team_agg = {}
    for fid, m in fixture_predictions.items():
        home, away = m["home_name"], m["away_name"]

        agg_h = team_agg.setdefault(home, {
            "xp_total": 0.0, "xp_home": [], "xp_away": [],
            "btts": [], "btts_no": [], "o25": [], "u25": [], "hcap_hit": [], "hcap_push": [], "hcap_miss": [],
            "lambda_home": [], "lambda_away": [], "remaining": 0,
        })
        home_xp = 3 * m["prob_home_win"] + 1 * m["prob_draw"]
        agg_h["remaining"] += 1
        agg_h["xp_total"] += home_xp
        agg_h["xp_home"].append(home_xp)
        agg_h["btts"].append(m["btts_yes"])
        agg_h["btts_no"].append(m["btts_no"])
        agg_h["o25"].append(m["over_2_5"])
        agg_h["u25"].append(m["under_2_5"])
        agg_h["lambda_home"].append(m["lambda_home"])
        agg_h["hcap_hit"].append(m["home_cover"])
        agg_h["hcap_push"].append(m["push"])
        agg_h["hcap_miss"].append(m["away_cover"])

        agg_a = team_agg.setdefault(away, {
            "xp_total": 0.0, "xp_home": [], "xp_away": [],
            "btts": [], "btts_no": [], "o25": [], "u25": [], "hcap_hit": [], "hcap_push": [], "hcap_miss": [],
            "lambda_home": [], "lambda_away": [], "remaining": 0,
        })
        away_xp = 3 * m["prob_away_win"] + 1 * m["prob_draw"]
        agg_a["remaining"] += 1
        agg_a["xp_total"] += away_xp
        agg_a["xp_away"].append(away_xp)
        agg_a["btts"].append(m["btts_yes"])
        agg_a["btts_no"].append(m["btts_no"])
        agg_a["o25"].append(m["over_2_5"])
        agg_a["u25"].append(m["under_2_5"])
        agg_a["lambda_away"].append(m["lambda_away"])
        agg_a["hcap_hit"].append(m["away_cover"])
        agg_a["hcap_push"].append(m["push"])
        agg_a["hcap_miss"].append(m["home_cover"])

    outlook = {
        "disclaimer": (
            "이 순위는 실제 EPL 순위가 아니라, 남은 EPL 일정에 대한 AI 분석 전망입니다. "
            "BTTS/오버언더/핸디캡은 팀 자체의 절대적인 능력치가 아니라, 잔여 일정의 "
            "상대팀 전력과 홈/원정 조건에 따라 달라지는 상대적 지표입니다."
        ),
        "avg_matches_played": round(38 - (len(unique_fixtures) * 2 / max(len(teams), 1)), 1),
        "matches_remaining_total": len(unique_fixtures),
        "matches_played_total": 380 - len(unique_fixtures),
        "1x2": [], "btts": [], "o25": [], "handicap": [],
        "score": [], "score_home": [], "score_away": [],
        "handicap_by_line": {},
    }

    for team, agg in team_agg.items():
        remaining = agg["remaining"]
        logo = team_logo_by_name.get(team)

        outlook["1x2"].append({
            "team": team, "logo": logo,
            "value": round(agg["xp_total"] / remaining, 2),
            "home_value": round(avg(agg["xp_home"]), 2) if agg["xp_home"] else None,
            "away_value": round(avg(agg["xp_away"]), 2) if agg["xp_away"] else None,
            "remaining": remaining,
        })
        if agg["btts"]:
            outlook["btts"].append({
                "team": team, "logo": logo,
                "value": round(avg(agg["btts"]) * 100, 1),
                "no_value": round(avg(agg["btts_no"]) * 100, 1) if agg["btts_no"] else None,
                "remaining": remaining,
            })
        if agg["o25"]:
            outlook["o25"].append({
                "team": team, "logo": logo,
                "value": round(avg(agg["o25"]) * 100, 1),
                "under_value": round(avg(agg["u25"]) * 100, 1) if agg["u25"] else None,
                "remaining": remaining,
            })
        if agg["hcap_hit"]:
            hit = avg(agg["hcap_hit"]) * 100
            push = avg(agg["hcap_push"]) * 100
            miss = avg(agg["hcap_miss"]) * 100
            equity = round(hit + push * 0.5, 1)
            outlook["handicap"].append({
                "team": team, "logo": logo, "value": equity,
                "hit": round(hit, 1), "push": round(push, 1), "miss": round(miss, 1),
                "remaining": remaining,
            })

        h_val = round(avg(agg["lambda_home"]), 2) if agg["lambda_home"] else None
        a_val = round(avg(agg["lambda_away"]), 2) if agg["lambda_away"] else None
        if agg["lambda_home"]:
            outlook["score_home"].append({"team": team, "logo": logo, "value": h_val, "remaining": len(agg["lambda_home"])})
        if agg["lambda_away"]:
            outlook["score_away"].append({"team": team, "logo": logo, "value": a_val, "remaining": len(agg["lambda_away"])})
        outlook["score"].append({
            "team": team, "logo": logo, "home_value": h_val, "away_value": a_val, "remaining": remaining,
        })

    for key in ["1x2", "btts", "o25", "handicap", "score_home", "score_away"]:
        outlook[key].sort(key=lambda r: r["value"], reverse=True)
    outlook["score"].sort(key=lambda r: (r["home_value"] or 0) + (r["away_value"] or 0), reverse=True)

    # ------------------------------------------------------------------
    # 2) 핸디캡 라인별 순위 - 이번 배치가 실제로 만들어낸 라인만 사용.
    #    predictor.match_outcome_probs / handicap_prob (기존 함수 그대로)로
    #    "모든 팀의 모든 남은경기에 그 라인을 동일 적용"해서 재계산한다.
    # ------------------------------------------------------------------
    distinct_lines_all = sorted({m["suggested_line"] for m in fixture_predictions.values() if m["suggested_line"] is not None})
    # 현실적인 범위만 탭으로 노출 (-2.5 ~ +2.5). 극단값(예: -6.0)은 시즌 초반
    # 표본부족으로 인한 일시적 λ 격차일 가능성이 높아, 화면에는 안 보여준다.
    # 계산 자체를 안 하는 것뿐이지 원본 예측/저장 데이터에는 영향 없음.
    HANDICAP_LINE_DISPLAY_RANGE = (-2.5, 2.5)
    distinct_lines = [l for l in distinct_lines_all if HANDICAP_LINE_DISPLAY_RANGE[0] <= l <= HANDICAP_LINE_DISPLAY_RANGE[1]]
    hidden_count = len(distinct_lines_all) - len(distinct_lines)
    print(f"이번 배치에서 실제로 사용된 핸디라인 전체: {distinct_lines_all}")
    print(f"화면에 표시할 라인({HANDICAP_LINE_DISPLAY_RANGE[0]}~{HANDICAP_LINE_DISPLAY_RANGE[1]}): {distinct_lines}")
    if hidden_count:
        print(f"범위 밖이라 숨긴 라인 수: {hidden_count}건 (시즌 초반 표본부족으로 인한 극단값으로 추정)")

    handicap_by_line = {}
    for line in distinct_lines:
        team_line_agg = {}
        for fid, m in fixture_predictions.items():
            sm_result = predictor.match_outcome_probs(m["lambda_home"], m["lambda_away"])
            h_cover, push, a_cover = predictor.handicap_prob(sm_result["score_matrix"], line)
            for team, hit, miss in [(m["home_name"], h_cover, a_cover), (m["away_name"], a_cover, h_cover)]:
                agg = team_line_agg.setdefault(team, {"hits": [], "pushes": [], "misses": [], "remaining": 0})
                agg["hits"].append(hit)
                agg["pushes"].append(push)
                agg["misses"].append(miss)
                agg["remaining"] += 1

        rows = []
        for t, a in team_line_agg.items():
            hit_pct = avg(a["hits"]) * 100
            push_pct = avg(a["pushes"]) * 100
            miss_pct = avg(a["misses"]) * 100
            rows.append({
                "team": t, "logo": team_logo_by_name.get(t),
                # 커버 기대값 = 적중 100% + 적특(push) 50% + 미적중 0% 로 계산.
                # (predictor.handicap_prob 자체는 그대로 두고, 그 결과를 이렇게
                # 집계만 다르게 한 것 - 백테스트에서 쓰던 것과 동일한 기준)
                "value": round(hit_pct + push_pct * 0.5, 1),
                "hit": round(hit_pct, 1), "push": round(push_pct, 1), "miss": round(miss_pct, 1),
                "remaining": a["remaining"],
            })
        rows.sort(key=lambda r: r["value"], reverse=True)
        handicap_by_line[f"{line:+.1f}"] = rows

    outlook["handicap_by_line"] = handicap_by_line
    outlook["handicap_lines"] = [f"{l:+.1f}" for l in distinct_lines]
    outlook["handicap_lines_hidden_count"] = hidden_count

    with open("team_outlook.json", "w", encoding="utf-8") as f:
        json.dump(outlook, f, ensure_ascii=False, indent=2)

    print("team_outlook.json 저장 완료.")
    print("\n[승무패] TOP5 (팀 | 홈 | 원정 | 기대승점 | 잔여)")
    for row in outlook["1x2"][:5]:
        print(f"  {row['team']:<20} {row['home_value']} | {row['away_value']} | {row['value']}  (잔여 {row['remaining']})")

    print("\n[득점] TOP5 (팀 | 홈 | 원정 | 잔여)")
    for row in outlook["score"][:5]:
        print(f"  {row['team']:<20} {row['home_value']} | {row['away_value']}  (잔여 {row['remaining']})")

    for line_str, rows in handicap_by_line.items():
        print(f"\n[핸디 {line_str}] TOP3")
        for row in rows[:3]:
            print(f"  {row['team']:<20} {row['value']}%  (잔여 {row['remaining']})")


if __name__ == "__main__":
    main()
