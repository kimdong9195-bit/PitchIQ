"""
backtest/collect_data.py - STEP 1: EPL 과거 4시즌 데이터 수집

한 번 실행해서 historical_data.db를 채워두면, 이후 백테스트는 API를 다시
부르지 않고 이 로컬 데이터만 읽어서 빠르게 반복 실행할 수 있다.

실행 방법:
    export API_FOOTBALL_KEY="발급받은키"
    python backtest/collect_data.py

중간에 끊겨도 다시 실행하면 이어서 진행된다 (이미 받은 시즌의 fixtures는
건너뛰고, lineups/statistics는 이미 저장된 fixture는 건너뛴다).

주의: API 요청을 많이 쓴다. EPL 4시즌 기준 대략:
    - fixtures 조회: 시즌당 1회 = 4회
    - 경기당 lineups 1회 + statistics 1회, 시즌당 약 380경기 = 시즌당 760회
    - 총 대략 4 x 760 + 4 = 3,044회
Pro 플랜 하루 7,500회 한도 안에서 한 번에 끝낼 수 있는 양이지만,
혹시 중간에 한도 초과 에러가 나면 그냥 다음 날 다시 실행하면 이어서 된다.
"""

import sys
import time

sys.path.insert(0, ".")  # 상위 폴더의 api_client를 import하기 위함

import api_client
from backtest import schema

LEAGUE_ID_EPL = 39
SEASONS = [2023, 2024, 2025, 2026]
REQUEST_DELAY_SECONDS = 0.3  # API에 너무 빠르게 몰아치지 않도록 살짝 텀을 둔다


def _extract_stat_value(statistics: list, stat_type: str):
    for s in statistics:
        if s.get("type") == stat_type:
            value = s.get("value")
            if isinstance(value, str) and value.endswith("%"):
                try:
                    return float(value.rstrip("%"))
                except ValueError:
                    return None
            return value
    return None


def collect_fixtures_for_season(season: int):
    if schema.is_stage_done(LEAGUE_ID_EPL, season, "fixtures"):
        print(f"[{season}] fixtures: 이미 수집됨, 건너뜀")
        return

    print(f"[{season}] fixtures 수집 중...")
    fixtures = api_client.get_league_fixtures(LEAGUE_ID_EPL, season)
    finished_count = 0

    for fx in fixtures:
        if fx["fixture"]["status"]["short"] != "FT":
            continue
        schema.upsert_match({
            "fixture_id": fx["fixture"]["id"],
            "league_id": LEAGUE_ID_EPL,
            "season": season,
            "match_date": fx["fixture"]["date"][:10],
            "home_team_id": fx["teams"]["home"]["id"],
            "away_team_id": fx["teams"]["away"]["id"],
            "home_team_name": fx["teams"]["home"]["name"],
            "away_team_name": fx["teams"]["away"]["name"],
            "status": "FT",
            "home_goals": fx["goals"]["home"],
            "away_goals": fx["goals"]["away"],
            "referee": fx["fixture"].get("referee"),
        })
        finished_count += 1

    schema.mark_stage_done(LEAGUE_ID_EPL, season, "fixtures")
    print(f"[{season}] fixtures 완료: {finished_count}경기 저장")


def collect_lineups_and_stats_for_season(season: int):
    if schema.is_stage_done(LEAGUE_ID_EPL, season, "lineups_and_stats"):
        print(f"[{season}] lineups/statistics: 이미 수집됨, 건너뜀")
        return

    matches = schema.get_all_matches(LEAGUE_ID_EPL, season)
    print(f"[{season}] lineups/statistics 수집 중... (총 {len(matches)}경기)")

    for i, m in enumerate(matches, 1):
        fixture_id = m["fixture_id"]

        # 이미 라인업이 저장되어 있으면 이 경기는 건너뜀 (재실행 시 이어하기)
        if schema.get_lineup(fixture_id, m["home_team_id"]) is not None:
            continue

        # 통계 (슈팅/점유율/코너)
        try:
            stats = api_client.get_fixture_statistics(fixture_id)
            time.sleep(REQUEST_DELAY_SECONDS)
        except Exception as e:
            print(f"  경기 {fixture_id} 통계 조회 실패: {e}")
            stats = []

        home_stats = away_stats = None
        for team_stats in stats:
            if team_stats["team"]["id"] == m["home_team_id"]:
                home_stats = team_stats["statistics"]
            elif team_stats["team"]["id"] == m["away_team_id"]:
                away_stats = team_stats["statistics"]

        if home_stats or away_stats:
            m["home_shots"] = _extract_stat_value(home_stats or [], "Total Shots")
            m["home_shots_on_target"] = _extract_stat_value(home_stats or [], "Shots on Goal")
            m["home_possession"] = _extract_stat_value(home_stats or [], "Ball Possession")
            m["home_corners"] = _extract_stat_value(home_stats or [], "Corner Kicks")
            m["away_shots"] = _extract_stat_value(away_stats or [], "Total Shots")
            m["away_shots_on_target"] = _extract_stat_value(away_stats or [], "Shots on Goal")
            m["away_possession"] = _extract_stat_value(away_stats or [], "Ball Possession")
            m["away_corners"] = _extract_stat_value(away_stats or [], "Corner Kicks")
            schema.upsert_match(m)

        # 라인업 (양팀)
        try:
            lineup_data = api_client.get_lineup(fixture_id)
            time.sleep(REQUEST_DELAY_SECONDS)
        except Exception as e:
            print(f"  경기 {fixture_id} 라인업 조회 실패: {e}")
            lineup_data = []

        for entry in lineup_data:
            team_id = entry["team"]["id"]
            coach_name = (entry.get("coach") or {}).get("name")
            formation = entry.get("formation")
            player_ids = [p["player"]["id"] for p in entry.get("startXI", [])]
            player_names = [p["player"]["name"] for p in entry.get("startXI", [])]
            schema.upsert_lineup(fixture_id, team_id, coach_name, formation, player_ids, player_names)

        if i % 20 == 0:
            print(f"  진행: {i}/{len(matches)}")

    schema.mark_stage_done(LEAGUE_ID_EPL, season, "lineups_and_stats")
    print(f"[{season}] lineups/statistics 완료")


def main():
    schema.init_schema()
    for season in SEASONS:
        collect_fixtures_for_season(season)
        collect_lineups_and_stats_for_season(season)
    print("전체 수집 완료.")
    print()
    print_coverage_report()


def print_coverage_report():
    """
    수집된 데이터의 실태를 시즌별로 보여준다. mock 테스트가 아니라 실제 API로
    수집했을 때, 라인업/통계 데이터가 실제로 얼마나 채워지는지 확인하는 용도.
    이 함수 출력 결과를 그대로 복사해서 공유하면 커버리지를 검토할 수 있다.
    """
    print("=" * 60)
    print("데이터 커버리지 리포트 (실제 수집 결과 검증용)")
    print("=" * 60)
    for season in SEASONS:
        matches = schema.get_all_matches(LEAGUE_ID_EPL, season)
        total = len(matches)
        if total == 0:
            print(f"[{season}] 경기 0건 - 수집 안 됐거나 이 시즌 데이터 없음")
            continue

        with_stats = sum(1 for m in matches if m.get("home_shots") is not None)
        with_lineup = 0
        for m in matches:
            home_lineup = schema.get_lineup(m["fixture_id"], m["home_team_id"])
            away_lineup = schema.get_lineup(m["fixture_id"], m["away_team_id"])
            if home_lineup and away_lineup:
                with_lineup += 1

        print(f"[{season}] 총 {total}경기")
        print(f"  - 통계(슈팅 등) 있음: {with_stats}건 ({with_stats/total*100:.1f}%)")
        print(f"  - 라인업(양팀 다) 있음: {with_lineup}건 ({with_lineup/total*100:.1f}%)")
        print(f"  - 라인업 누락: {total - with_lineup}건 ({(total-with_lineup)/total*100:.1f}%)")
    print("=" * 60)


if __name__ == "__main__":
    main()
