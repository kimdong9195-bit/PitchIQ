"""
prediction_log.py - 예측 기록 / 모델버전 / 실제결과 저장 (Supabase PostgreSQL)

핵심 원칙:
1. predictions 테이블은 INSERT 전용이다. 같은 fixture를 몇 번을 다시 분석해도
   매번 새 행이 추가될 뿐, 기존 행을 절대 UPDATE하지 않는다 (예측 당시 값
   보존이 목적이므로).
2. actual_results는 fixture_id당 딱 1행만 존재한다 (실제 결과는 하나뿐이므로).
   여러 예측 스냅샷이 이 한 행을 공유해서 나중에 채점할 때 참조한다.
3. model_versions은 "이 버전이 어떤 파라미터/alpha를 썼는지"의 기록이다.

DB 연결 실패 시 predictor.py의 예측 표시 자체를 막으면 안 되므로, 이 모듈의
모든 함수는 실패해도 예외를 밖으로 던지지 않고 조용히 실패 처리한다
(기록이 하나 안 남는 것과, 사용자가 분석 결과 자체를 못 보는 것은
심각도가 다르다고 판단함).
"""

import json
import os
from contextlib import closing
from datetime import datetime

import psycopg2
import psycopg2.errors
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL")

# 현재 확정된 모델의 버전 식별자 - predictor.py의 파라미터/alpha가 바뀌면
# 반드시 이 값도 새 버전으로 올려야 한다 (과거 기록과 비교 가능하도록).
CURRENT_MODEL_VERSION = "v1.0.0"

# 이번에 확정된 파라미터/alpha 그대로 - predictor.py 파일을 다시 읽어와서
# 만드는 대신, 이 파일에 그대로 박아둔다 (기록용 메타데이터라 predictor.py를
# import해서 값을 끌어오지 않음 - 그러면 predictor.py 수정 여부와 무관하게
# 이 기록 모듈이 항상 "그 버전 당시의 값"을 정확히 남길 수 있음).
CURRENT_MODEL_PARAMS = {
    "half_life": 180, "season_blend_k": 15, "home_advantage": 1.0, "rho": -0.05,
    "squad_continuity_floor": 0.5, "manager_change_penalty": 0.7,
    "continuity_mode": "post_hoc", "opponent_strength_iterations": 0,
    "final_opponent_adjustment_mode": "venue_specific",
}
CURRENT_MODEL_ALPHAS = {"1x2": 0.85, "btts": 0.30, "o25": 0.45, "handicap": 1.00}


def _connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL 환경변수가 설정되어 있지 않습니다.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def init_prediction_tables():
    """앱 시작 시 한 번 호출. 테이블이 없으면 만든다."""
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS model_versions (
                        version TEXT PRIMARY KEY,
                        description TEXT,
                        params_json TEXT NOT NULL,
                        alphas_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS predictions (
                        id SERIAL PRIMARY KEY,
                        fixture_id INTEGER,
                        match_date TEXT,
                        home_team TEXT NOT NULL,
                        away_team TEXT NOT NULL,
                        prediction_timestamp TEXT NOT NULL,
                        model_version TEXT NOT NULL REFERENCES model_versions(version),
                        lambda_home REAL,
                        lambda_away REAL,
                        prob_home_win REAL,
                        prob_draw REAL,
                        prob_away_win REAL,
                        prob_btts_yes REAL,
                        prob_btts_no REAL,
                        prob_over_2_5 REAL,
                        prob_under_2_5 REAL,
                        handicap_line REAL,
                        prob_handicap_home REAL,
                        prob_handicap_push REAL,
                        prob_handicap_away REAL,
                        most_likely_scores_json TEXT
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS actual_results (
                        fixture_id INTEGER PRIMARY KEY,
                        actual_home_goals INTEGER NOT NULL,
                        actual_away_goals INTEGER NOT NULL,
                        resolved_at TEXT NOT NULL
                    )
                """)
            conn.commit()

        # 현재 확정 모델 버전을 등록 (이미 있으면 건드리지 않음 - 과거 버전 기록 보존)
        register_model_version_if_new(
            CURRENT_MODEL_VERSION,
            "shrinkage calibration 적용 첫 확정 버전 (2025-26 holdout 검증 통과)",
            CURRENT_MODEL_PARAMS, CURRENT_MODEL_ALPHAS,
        )
    except Exception as e:
        print(f"[prediction_log.init_prediction_tables] 경고: {e}")


def register_model_version_if_new(version: str, description: str, params: dict, alphas: dict):
    """이미 등록된 버전이면 아무것도 안 함 (과거 버전의 params_json을 덮어쓰지 않기 위함)."""
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM model_versions WHERE version = %s", (version,))
                if cur.fetchone():
                    return
                cur.execute(
                    "INSERT INTO model_versions (version, description, params_json, alphas_json, created_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (version, description, json.dumps(params), json.dumps(alphas), datetime.utcnow().isoformat()),
                )
            conn.commit()
    except Exception as e:
        print(f"[prediction_log.register_model_version_if_new] 경고: {e}")


def record_prediction(fixture_id, match_date, home_team, away_team, result: dict):
    """
    predictor.analyze_match()의 반환값(result)을 그대로 받아서 스냅샷 1행을
    새로 추가한다. 같은 fixture를 여러 번 분석해도 매번 새 행이 생긴다
    (기존 행을 절대 UPDATE하지 않음).

    DB 저장에 실패해도 예외를 던지지 않는다 - 예측 결과 화면 표시는
    이 함수의 성공 여부와 무관하게 이미 끝난 뒤이기 때문에, 여기서 실패해도
    사용자 경험에는 영향이 없어야 한다.
    """
    try:
        probs = result["probabilities"]
        hcap = result.get("handicap", {})
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO predictions (
                        fixture_id, match_date, home_team, away_team, prediction_timestamp, model_version,
                        lambda_home, lambda_away,
                        prob_home_win, prob_draw, prob_away_win,
                        prob_btts_yes, prob_btts_no,
                        prob_over_2_5, prob_under_2_5,
                        handicap_line, prob_handicap_home, prob_handicap_push, prob_handicap_away,
                        most_likely_scores_json
                    ) VALUES (%s,%s,%s,%s,%s,%s, %s,%s, %s,%s,%s, %s,%s, %s,%s, %s,%s,%s,%s, %s)
                    """,
                    (
                        fixture_id, match_date, home_team, away_team,
                        datetime.utcnow().isoformat(), CURRENT_MODEL_VERSION,
                        result.get("home_lambda"), result.get("away_lambda"),
                        probs.get("home_win"), probs.get("draw"), probs.get("away_win"),
                        probs.get("btts_yes"), probs.get("btts_no"),
                        probs.get("over_2_5"), probs.get("under_2_5"),
                        hcap.get("suggested_line"), hcap.get("home_cover"), hcap.get("push"), hcap.get("away_cover"),
                        json.dumps(result.get("most_likely_scores", [])),
                    ),
                )
            conn.commit()
    except Exception as e:
        print(f"[prediction_log.record_prediction] 경고 (기록 실패, 분석결과 표시는 정상 진행됨): {e}")


def record_actual_result(fixture_id: int, actual_home_goals: int, actual_away_goals: int):
    """
    fixture_id당 1행만 유지한다. 이미 그 fixture의 결과가 저장되어 있으면
    (경기 결과는 다시 안 바뀌는 확정 사실이므로) 아무것도 안 하고 넘어간다
    - 실수로 같은 결과를 여러 번 기록해도 안전하게 무시된다.
    """
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM actual_results WHERE fixture_id = %s", (fixture_id,))
                if cur.fetchone():
                    return
                cur.execute(
                    "INSERT INTO actual_results (fixture_id, actual_home_goals, actual_away_goals, resolved_at) "
                    "VALUES (%s, %s, %s, %s)",
                    (fixture_id, actual_home_goals, actual_away_goals, datetime.utcnow().isoformat()),
                )
            conn.commit()
    except Exception as e:
        print(f"[prediction_log.record_actual_result] 경고: {e}")


def get_predictions_with_results(model_version: str = None):
    """
    predictions와 actual_results를 fixture_id로 조인해서 반환한다.
    아직 결과가 안 나온 경기는 actual_* 필드가 None으로 나온다.
    평가 함수(Log Loss 등)는 이 결과 중 actual_*가 채워진 것만 걸러서 쓰면 된다.
    """
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                query = """
                    SELECT p.*, a.actual_home_goals, a.actual_away_goals, a.resolved_at
                    FROM predictions p
                    LEFT JOIN actual_results a ON p.fixture_id = a.fixture_id
                """
                params = ()
                if model_version:
                    query += " WHERE p.model_version = %s"
                    params = (model_version,)
                cur.execute(query, params)
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        print(f"[prediction_log.get_predictions_with_results] 경고: {e}")
        return []


def get_unresolved_fixture_ids():
    """아직 실제 결과가 안 붙은 예측들의 fixture_id 목록 (결과 업데이트 배치용)."""
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT p.fixture_id, p.match_date, p.home_team, p.away_team
                    FROM predictions p
                    LEFT JOIN actual_results a ON p.fixture_id = a.fixture_id
                    WHERE a.fixture_id IS NULL AND p.fixture_id IS NOT NULL
                """)
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        print(f"[prediction_log.get_unresolved_fixture_ids] 경고: {e}")
        return []
