"""
backtest/data_quality.py - 실제 백테스트 실행 전 데이터 품질 검증

collect_data.py로 실제 데이터를 모은 직후, 최적화(STEP A/B)를 돌리기 전에
반드시 이 스크립트부터 실행해서 이상이 없는지 확인한다.

실행: python backtest/data_quality.py
"""

import sys
sys.path.insert(0, ".")

from collections import Counter
from datetime import datetime

from backtest import schema
from backtest.collect_data import LEAGUE_ID_EPL, SEASONS


def check_season_match_counts():
    print("=== 1. 시즌별 경기 수 ===")
    for season in SEASONS:
        matches = schema.get_all_matches(LEAGUE_ID_EPL, season)
        expected = 380  # EPL 정규시즌 기준 (20팀 x 19경기 x 2)
        flag = "" if abs(len(matches) - expected) <= 20 else "  ⚠ 예상(380)과 크게 다름"
        print(f"  {season}: {len(matches)}경기{flag}")


def check_team_match_counts():
    print("\n=== 2. 팀별 경기 수 (시즌당 정상이면 팀당 38경기 안팎) ===")
    for season in SEASONS:
        matches = schema.get_all_matches(LEAGUE_ID_EPL, season)
        counts = Counter()
        for m in matches:
            counts[m["home_team_name"]] += 1
            counts[m["away_team_name"]] += 1
        if not counts:
            print(f"  {season}: 데이터 없음")
            continue
        abnormal = {t: c for t, c in counts.items() if not (30 <= c <= 46)}
        print(f"  {season}: {len(counts)}개팀, 평균 {sum(counts.values())/len(counts):.1f}경기/팀"
              + (f"  ⚠ 비정상 팀: {abnormal}" if abnormal else ""))


def check_lineup_coverage():
    print("\n=== 3. 시즌별 라인업 커버리지 ===")
    for season in SEASONS:
        matches = schema.get_all_matches(LEAGUE_ID_EPL, season)
        if not matches:
            continue
        both = 0
        one_only = 0
        neither = 0
        for m in matches:
            h = schema.get_lineup(m["fixture_id"], m["home_team_id"])
            a = schema.get_lineup(m["fixture_id"], m["away_team_id"])
            if h and a:
                both += 1
            elif h or a:
                one_only += 1
            else:
                neither += 1
        total = len(matches)
        print(f"  {season}: 양팀 다 있음 {both}/{total} ({both/total*100:.1f}%), "
              f"한쪽만 {one_only}, 둘다 없음 {neither}")


def check_manager_coverage():
    print("\n=== 4. 감독 정보(coach_name) 커버리지 ===")
    for season in SEASONS:
        matches = schema.get_all_matches(LEAGUE_ID_EPL, season)
        if not matches:
            continue
        with_coach = 0
        total_lineups = 0
        for m in matches:
            for team_id in (m["home_team_id"], m["away_team_id"]):
                lineup = schema.get_lineup(m["fixture_id"], team_id)
                if lineup:
                    total_lineups += 1
                    if lineup.get("coach_name"):
                        with_coach += 1
        if total_lineups > 0:
            print(f"  {season}: {with_coach}/{total_lineups} ({with_coach/total_lineups*100:.1f}%) 라인업에 감독명 있음")
        else:
            print(f"  {season}: 라인업 데이터 없음")


def check_missing_values():
    print("\n=== 5. 결측치 현황 (스코어 null, 통계 null) ===")
    for season in SEASONS:
        matches = schema.get_all_matches(LEAGUE_ID_EPL, season)
        if not matches:
            continue
        null_goals = sum(1 for m in matches if m["home_goals"] is None or m["away_goals"] is None)
        null_stats = sum(1 for m in matches if m.get("home_shots") is None)
        print(f"  {season}: 스코어 null {null_goals}건, 슈팅통계 null {null_stats}/{len(matches)}건")


def check_duplicate_fixtures():
    print("\n=== 6. 중복 fixture_id 여부 ===")
    for season in SEASONS:
        matches = schema.get_all_matches(LEAGUE_ID_EPL, season)
        ids = [m["fixture_id"] for m in matches]
        dupes = [fid for fid, cnt in Counter(ids).items() if cnt > 1]
        print(f"  {season}: 중복 {len(dupes)}건" + (f" -> {dupes[:10]}" if dupes else ""))


def check_date_anomalies():
    print("\n=== 7. 날짜 이상 여부 ===")
    season_date_ranges = {2023: ("2023-07-01", "2024-06-30"), 2024: ("2024-07-01", "2025-06-30"),
                           2025: ("2025-07-01", "2026-06-30"), 2026: ("2026-07-01", "2027-06-30")}
    for season in SEASONS:
        matches = schema.get_all_matches(LEAGUE_ID_EPL, season)
        if not matches:
            continue
        lo, hi = season_date_ranges.get(season, (None, None))
        out_of_range = [m["fixture_id"] for m in matches if lo and not (lo <= m["match_date"] <= hi)]
        bad_format = []
        for m in matches:
            try:
                datetime.fromisoformat(m["match_date"])
            except (ValueError, TypeError):
                bad_format.append(m["fixture_id"])
        print(f"  {season}: 시즌범위 밖 날짜 {len(out_of_range)}건, 형식 이상 {len(bad_format)}건")


def check_season_presence():
    print("\n=== 8. 2023-24 / 2024-25 / 2025-26 정상 적재 여부 ===")
    from backtest.season_split import OPTIMIZATION_SEASONS, FINAL_VALIDATION_SEASON
    required = OPTIMIZATION_SEASONS + [FINAL_VALIDATION_SEASON]
    for season in required:
        matches = schema.get_all_matches(LEAGUE_ID_EPL, season)
        status = "OK" if len(matches) >= 300 else "⚠ 데이터 부족 또는 없음"
        role = "최적화" if season in OPTIMIZATION_SEASONS else "최종검증(holdout)"
        print(f"  {season} ({role}): {len(matches)}경기 - {status}")


def run_full_report():
    check_season_match_counts()
    check_team_match_counts()
    check_lineup_coverage()
    check_manager_coverage()
    check_missing_values()
    check_duplicate_fixtures()
    check_date_anomalies()
    check_season_presence()
    print("\n" + "=" * 60)
    print("위 결과에 ⚠ 표시가 없으면 STEP A/B 백테스트를 진행해도 안전합니다.")
    print("⚠가 있으면 그 시즌/항목을 먼저 사용자와 같이 확인해야 합니다.")


if __name__ == "__main__":
    run_full_report()
