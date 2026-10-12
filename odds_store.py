"""
odds_store.py - Odds Snapshot 저장소

*** db.py(운영 Postgres), backtest/schema.py(historical_data.db), predictor.py,
app.py 전혀 수정하지 않는다. 완전히 새로운 독립 SQLite 파일. ***

왜 별도 파일/DB로 분리했는지:
  - db.py는 Supabase Postgres(계정/사용량) - odds는 그 도메인과 무관해서
    섞으면 안 됨 (운영 DB 스키마 변경 위험을 전혀 안 지는 게 목적)
  - backtest/schema.py의 historical_data.db는 팀 경기결과 전용 - odds는
    성격이 다른 데이터(시간에 따라 바뀌는 시계열)라 섞지 않음

핵심 설계: captured_at(우리가 실제로 API를 호출한 시각) vs odds_update(API
응답 자체의 "update" 필드)를 반드시 분리해서 저장한다 - 절대 하나로 합치지 않음.

중복 방지: (fixture_id, bookmaker_id, bet_id, selection, line, odd, odds_update)
전부 똑같은 경우만 중복으로 보고 건너뛴다. 배당값이 바뀌거나, 같은 값이어도
bookmaker가 odds_update를 새로 찍었으면(재계산 사이클) 별도 행으로 남긴다 -
"무조건 insert 금지, 정확히 동일한 것만 dedup"이라는 요구사항 그대로 구현.

Asian Handicap(bet 4) line 규칙 (2026-10-04 실데이터로 확인 - 1557417/1557422,
Bet365/Pinnacle/Betfair에서 Home X <-> Away X 짝의 내재확률 합이 104~108%):
  - value의 숫자는 "홈 기준 라인"이다. "Home -1.25"와 "Away -1.25"는 같은 라인의 양쪽.
  - "Away X"의 실제 원정 핸디캡은 -X다 (예: "Away -1.25" = 원정 +1.25).
  - 따라서 line 컬럼은 홈 기준 라인이다 - Home X와 Away X가 같은 line 값을 갖는다.
"""

import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

DB_PATH = os.environ.get("ODDS_DB_PATH", "odds_snapshots.db")
# 1.10 이하 배당은 저장·표시·분석 모두 하지 않는다 (2026-10-13 사용자 결정) - 저장 시 잘못된값스킵으로 센다
MIN_ODD = 1.1


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """테이블이 없으면 만든다 (있으면 아무 일도 안 함) - db.py의 init_db() 패턴과 동일한 철학."""
    with closing(_connect()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS odds_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fixture_id INTEGER NOT NULL,
                captured_at TEXT NOT NULL,
                odds_update TEXT,
                bookmaker_id INTEGER NOT NULL,
                bookmaker_name TEXT,
                bet_id INTEGER NOT NULL,
                bet_name TEXT,
                selection TEXT NOT NULL,
                line TEXT,
                odd REAL NOT NULL,
                raw_json TEXT,
                UNIQUE(fixture_id, bookmaker_id, bet_id, selection, line, odd, odds_update)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_odds_fixture
            ON odds_snapshots(fixture_id, bet_id, bookmaker_id)
        """)
        conn.commit()


def _parse_odd_value(raw_odd):
    """
    API 응답의 odd는 문자열("1.65")일 수 있음 - 안전하게 float로 변환.
    실패하면 None을 반환(0으로 바꾸지 않음 - 0은 "배당 0배"라는 잘못된 의미가
    되므로). 호출부에서 None이면 그 row는 skip하고 로그만 남긴다.
    """
    try:
        return float(raw_odd)
    except (TypeError, ValueError):
        return None


def _extract_line(selection_text):
    """
    'Over 2.5' / 'Home -1.5' 같은 문자열에서 끝에 붙은 숫자(라인)를
    best-effort로 뽑아낸다. 못 뽑으면 빈 문자열("")을 반환한다 - NULL이
    아니라 ""를 쓰는 이유: SQLite UNIQUE 제약은 NULL끼리는 "다르다"고
    취급해서(NULL != NULL), line이 없는 시장(1X2/BTTS)의 동일한 snapshot이
    중복으로 안 걸러지고 계속 새로 쌓이는 버그가 있었다 - ""로 통일해서
    UNIQUE(...,line,...)가 실제로 작동하게 만든다.
    (Asian Handicap의 실제 value 포맷은 아직 실데이터로 확인 전이라, 이
    함수가 못 뽑아내는 포맷이 나오면 raw_json으로 나중에 직접 확인한다.)
    Asian Handicap(bet 4): 뽑힌 숫자는 홈 기준 라인이다. "Away X"에서 뽑은
    X는 원정 핸디캡이 아니라 홈 기준 라인이며, 실제 원정 핸디캡은 -X다
    (모듈 docstring의 AH line 규칙 참고). 부호는 바꾸지 않고 그대로 저장한다.
    """
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*$", selection_text or "")
    return match.group(1) if match else ""


def parse_and_store_odds(fixture_id, odds_response, bet_id_filter, bookmaker_id_filter=None):
    """
    get_odds(fixture_id) 응답(이미 bookmakers 묶음 리스트)을 순회하며
    bookmakers -> bets -> values 구조를 한 selection당 1행으로 정규화해서 저장한다.
    bet_id_filter: 저장할 bet id 목록(예: [1,4,5,8]) - 그 외 시장은 무시.
    bookmaker_id_filter: None이면 전체, 리스트면 그 북메이커만.
    반환: {"inserted": N, "skipped_duplicate": N, "skipped_invalid_odd": N}
    """
    init_db()
    captured_at = datetime.now(timezone.utc).isoformat()
    stats = {"inserted": 0, "skipped_duplicate": 0, "skipped_invalid_odd": 0}

    with closing(_connect()) as conn:
        for entry in odds_response:
            # odds_update도 같은 이유(SQLite UNIQUE의 NULL!=NULL)로 ""에 통일
            odds_update = entry.get("update") or ""
            for bm in entry.get("bookmakers", []):
                bm_id, bm_name = bm.get("id"), bm.get("name")
                if bookmaker_id_filter is not None and bm_id not in bookmaker_id_filter:
                    continue
                for bet in bm.get("bets", []):
                    bet_id, bet_name = bet.get("id"), bet.get("name")
                    if bet_id not in bet_id_filter:
                        continue
                    for v in bet.get("values", []):
                        selection = v.get("value")
                        odd_val = _parse_odd_value(v.get("odd"))
                        if odd_val is None or odd_val <= MIN_ODD:
                            stats["skipped_invalid_odd"] += 1
                            continue
                        line = _extract_line(selection)
                        try:
                            conn.execute("""
                                INSERT INTO odds_snapshots
                                (fixture_id, captured_at, odds_update, bookmaker_id, bookmaker_name,
                                 bet_id, bet_name, selection, line, odd, raw_json)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                            """, (fixture_id, captured_at, odds_update, bm_id, bm_name,
                                  bet_id, bet_name, selection, line, odd_val, json.dumps(v, ensure_ascii=False)))
                            stats["inserted"] += 1
                        except sqlite3.IntegrityError:
                            stats["skipped_duplicate"] += 1
        conn.commit()
    return stats


def get_snapshots_for_fixture(fixture_id):
    """저장된 snapshot 조회 (테스트/검증용)."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM odds_snapshots WHERE fixture_id=? ORDER BY captured_at", (fixture_id,)
        ).fetchall()
        return [dict(r) for r in rows]
