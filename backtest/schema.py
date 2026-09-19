"""
backtest/schema.py - STEP 1: 백테스트용 과거 데이터 저장소 (원본 API 응답 캐시)

설계 원칙
---------
1. 이 DB(historical_data.db)는 실서비스 DB(footy_predictor.db)와 완전히 분리한다.
   백테스트는 "그 시점까지의 데이터만" 봐야 하므로, 매번 라이브 API를 부르지 않고
   미리 모아둔 이 로컬 캐시를 읽어서 도는 구조로 만든다 (API 요청량/속도 문제 해결).

2. "데이터가 없으면 채우지 않는다" (스펙 12번 원칙)
   - xG/xGA: API-Football은 기본 제공하지 않음 -> 컬럼은 만들되 항상 NULL,
     나중에 다른 데이터소스를 붙이면 채워지도록 자리만 마련해둔다.
   - 과거 시점의 부상자 명단: API가 "현재" 부상자만 알려줘서 소급 조회 불가.
     대신 lineups 테이블 자체가 "그 경기에 실제로 누가 선발로 뛰었는지"를 담고
     있어서, 선수단 연속성 계산(스펙 4번)엔 이걸로 충분하다 - 부상 여부와
     상관없이 "그 선수가 그날 안 뛰었다"는 사실 자체가 중요한 신호이기 때문.
   - 감독 정보: 별도 테이블 없이 lineups.coach_name으로 추적한다 (매 경기
     라인업 응답에 감독 이름이 포함되어 있음 -> 감독 교체 시점을 자연히 알 수 있음).

3. 모든 원본 데이터를 그대로 캐시하고, "장기전력/현재컨디션 분리", "시간감쇠",
   "상대팀 강도 보정" 같은 가공은 이 스키마 위에서 나중 단계(STEP 2 이후)가
   계산한다. 즉 이 파일은 순수하게 "원본 저장" 역할만 한다.
"""

import os
import sqlite3
from contextlib import closing

HIST_DB_PATH = os.environ.get("HIST_DB_PATH", "historical_data.db")


def _connect():
    conn = sqlite3.connect(HIST_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema():
    """테이블이 없으면 만든다. 있으면 아무 일도 안 한다 (반복 실행 안전)."""
    with closing(_connect()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                fixture_id INTEGER PRIMARY KEY,
                league_id INTEGER NOT NULL,
                season INTEGER NOT NULL,
                match_date TEXT NOT NULL,          -- ISO 형식, walk-forward의 기준 시점
                home_team_id INTEGER NOT NULL,
                away_team_id INTEGER NOT NULL,
                home_team_name TEXT,
                away_team_name TEXT,
                status TEXT NOT NULL,              -- 'FT'만 백테스트에 사용
                home_goals INTEGER,
                away_goals INTEGER,
                home_shots INTEGER,
                home_shots_on_target INTEGER,
                away_shots INTEGER,
                away_shots_on_target INTEGER,
                home_possession REAL,
                away_possession REAL,
                home_corners INTEGER,
                away_corners INTEGER,
                home_xg REAL,                      -- API-Football 미제공 -> 항상 NULL (의도된 것)
                away_xg REAL,                       -- 위와 동일
                referee TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lineups (
                fixture_id INTEGER NOT NULL,
                team_id INTEGER NOT NULL,
                coach_name TEXT,
                formation TEXT,
                starting_player_ids TEXT,          -- JSON 배열을 문자열로 저장
                starting_player_names TEXT,        -- JSON 배열을 문자열로 저장
                PRIMARY KEY (fixture_id, team_id)
            )
        """)
        # 수집 진행 상황 기록 - 중간에 끊겨도 어디까지 했는지 알아야 이어서 할 수 있음
        conn.execute("""
            CREATE TABLE IF NOT EXISTS collection_log (
                league_id INTEGER NOT NULL,
                season INTEGER NOT NULL,
                stage TEXT NOT NULL,               -- 'fixtures' | 'lineups_and_stats'
                completed_at TEXT NOT NULL,
                PRIMARY KEY (league_id, season, stage)
            )
        """)
        conn.commit()


def is_stage_done(league_id: int, season: int, stage: str) -> bool:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT 1 FROM collection_log WHERE league_id=? AND season=? AND stage=?",
            (league_id, season, stage),
        ).fetchone()
        return row is not None


def mark_stage_done(league_id: int, season: int, stage: str):
    from datetime import datetime
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO collection_log (league_id, season, stage, completed_at) VALUES (?, ?, ?, ?)",
            (league_id, season, stage, datetime.utcnow().isoformat()),
        )
        conn.commit()


def upsert_match(match: dict):
    with closing(_connect()) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO matches (
                fixture_id, league_id, season, match_date, home_team_id, away_team_id,
                home_team_name, away_team_name, status, home_goals, away_goals,
                home_shots, home_shots_on_target, away_shots, away_shots_on_target,
                home_possession, away_possession, home_corners, away_corners,
                home_xg, away_xg, referee
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            match["fixture_id"], match["league_id"], match["season"], match["match_date"],
            match["home_team_id"], match["away_team_id"], match.get("home_team_name"),
            match.get("away_team_name"), match["status"], match.get("home_goals"),
            match.get("away_goals"), match.get("home_shots"), match.get("home_shots_on_target"),
            match.get("away_shots"), match.get("away_shots_on_target"), match.get("home_possession"),
            match.get("away_possession"), match.get("home_corners"), match.get("away_corners"),
            match.get("home_xg"), match.get("away_xg"), match.get("referee"),
        ))
        conn.commit()


def upsert_lineup(fixture_id: int, team_id: int, coach_name, formation, player_ids: list, player_names: list):
    import json
    with closing(_connect()) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO lineups
            (fixture_id, team_id, coach_name, formation, starting_player_ids, starting_player_names)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            fixture_id, team_id, coach_name, formation,
            json.dumps(player_ids), json.dumps(player_names),
        ))
        conn.commit()


def get_matches_before(league_id: int, season: int, before_date: str) -> list:
    """walk-forward 백테스트의 핵심 조회: 특정 시점 이전에 끝난 경기만 가져온다."""
    with closing(_connect()) as conn:
        rows = conn.execute("""
            SELECT * FROM matches
            WHERE league_id=? AND season=? AND status='FT' AND match_date < ?
            ORDER BY match_date ASC
        """, (league_id, season, before_date)).fetchall()
        return [dict(r) for r in rows]


def get_all_matches(league_id: int, season: int) -> list:
    with closing(_connect()) as conn:
        rows = conn.execute("""
            SELECT * FROM matches
            WHERE league_id=? AND season=? AND status='FT'
            ORDER BY match_date ASC
        """, (league_id, season)).fetchall()
        return [dict(r) for r in rows]


def get_lineup(fixture_id: int, team_id: int):
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM lineups WHERE fixture_id=? AND team_id=?", (fixture_id, team_id)
        ).fetchone()
        return dict(row) if row else None


def get_most_recent_lineup_before(team_id: int, before_date: str):
    """
    STEP 10 백테스트에서 continuity 계산의 'current_lineups' 값으로 반드시
    이 함수를 써야 한다 - 예측 대상 경기 자체의 라인업을 절대 쓰면 안 된다
    (그 경기가 열리기 전에는 알 수 없는 정보이므로 명백한 leakage).

    실제로 과거 시점에 "발표된 예상 라인업" 데이터는 갖고 있지 않으므로,
    그 대체재로 "그 팀이 가장 최근에(before_date 이전에) 실제로 뛴 경기의
    선발 라인업"을 쓴다 - 이건 실제로 그 시점에 이미 일어난 일이라
    안전하게 알 수 있는 정보다.

    반환: {"player_ids": [...], "coach_name": str} 또는 데이터 없으면 None
    (호출하는 쪽에서 continuity를 중립(1.0)으로 처리하면 됨).
    """
    with closing(_connect()) as conn:
        row = conn.execute("""
            SELECT m.fixture_id, m.home_team_id, m.away_team_id
            FROM matches m
            WHERE (m.home_team_id = ? OR m.away_team_id = ?)
              AND m.status = 'FT' AND m.match_date < ?
            ORDER BY m.match_date DESC
            LIMIT 1
        """, (team_id, team_id, before_date)).fetchone()

        if not row:
            return None

        lineup = get_lineup(row["fixture_id"], team_id)
        if not lineup:
            return None

        import json
        return {
            "player_ids": json.loads(lineup["starting_player_ids"]),
            "coach_name": lineup["coach_name"],
        }
