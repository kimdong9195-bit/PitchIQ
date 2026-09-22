"""
player_stats_cache.py - 과거 시점 분석용 캐시 (끝난 경기의 개인기록/부상정보)

끝난 경기의 선수별 통계나 그 경기의 부상자 명단은 나중에 다시 바뀌지 않는
"확정된 과거 사실"이라, 한 번 API로 받으면 영구히 캐싱해도 안전하다.
같은 fixture_id를 여러 사용자가, 또는 같은 사용자가 여러 번 분석 요청해도
API를 다시 안 부르게 해서 요청량을 크게 줄인다.

live predictor의 "오늘/미래 경기 분석"(캐시 대상 아님)과는 무관하다 -
이 캐시는 오직 "과거 시점 재현 분석"에서만 쓰인다.
"""

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime

CACHE_DB_PATH = os.environ.get("PLAYER_STATS_CACHE_PATH", "player_stats_cache.db")


def _connect():
    conn = sqlite3.connect(CACHE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_cache():
    with closing(_connect()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fixture_player_stats (
                fixture_id INTEGER PRIMARY KEY,
                data_json TEXT NOT NULL,
                cached_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fixture_injuries (
                fixture_id INTEGER PRIMARY KEY,
                data_json TEXT NOT NULL,
                cached_at TEXT NOT NULL
            )
        """)
        conn.commit()


def get_cached_player_stats(fixture_id: int):
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT data_json FROM fixture_player_stats WHERE fixture_id=?", (fixture_id,)
        ).fetchone()
        return json.loads(row["data_json"]) if row else None


def set_cached_player_stats(fixture_id: int, data: list):
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fixture_player_stats (fixture_id, data_json, cached_at) VALUES (?, ?, ?)",
            (fixture_id, json.dumps(data), datetime.utcnow().isoformat()),
        )
        conn.commit()


def get_cached_injuries(fixture_id: int):
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT data_json FROM fixture_injuries WHERE fixture_id=?", (fixture_id,)
        ).fetchone()
        return json.loads(row["data_json"]) if row else None


def set_cached_injuries(fixture_id: int, data: list):
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fixture_injuries (fixture_id, data_json, cached_at) VALUES (?, ?, ?)",
            (fixture_id, json.dumps(data), datetime.utcnow().isoformat()),
        )
        conn.commit()


init_cache()
