"""
db.py - 계정과 사용량을 저장하는 데이터베이스 (PostgreSQL / Supabase 기반).

기존에는 SQLite 파일 하나로 저장했는데, Render 무료 플랜엔 영구 디스크가
없어서 재배포/재시작마다 데이터가 사라질 위험이 있었다. 그래서 외부
관리형 PostgreSQL(Supabase Free)로 옮겼다 - 연결문자열만 환경변수
DATABASE_URL로 주면 되고, 나머지 함수들은 이름/반환값 형태를 그대로
유지해서 app.py 쪽 호출부는 거의 안 바뀌어도 되게 만들었다.

DATABASE_URL 예시(Supabase 대시보드 Settings->Database에서 그대로 복사):
    postgresql://postgres.xxxxx:비밀번호@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres

DB 연결 자체가 실패해도(예: Supabase가 7일 비활성으로 일시정지된 경우)
앱 전체가 죽지 않도록, 연결/쿼리 실패는 이 파일 안에서 잡아서 호출부에
안전한 기본값을 돌려준다 (로그인 실패 처리로 이어지되 500 에러로 앱이
멈추지는 않는다).
"""

import os
import secrets
from contextlib import closing
from datetime import date, datetime, timedelta

import psycopg2
import psycopg2.errors
import psycopg2.extras
from werkzeug.security import check_password_hash, generate_password_hash

DATABASE_URL = os.environ.get("DATABASE_URL")
RESET_TOKEN_VALID_MINUTES = 60


class DatabaseUnavailableError(Exception):
    """DB 연결/쿼리가 실패했을 때 - 호출부가 이걸 잡아서 안전하게 처리한다."""


def _connect():
    if not DATABASE_URL:
        raise DatabaseUnavailableError("DATABASE_URL 환경변수가 설정되어 있지 않습니다.")
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return conn
    except psycopg2.OperationalError as e:
        raise DatabaseUnavailableError(f"DB 연결 실패: {e}")


def init_db():
    """앱 시작할 때 한 번 호출. 테이블이 없으면 만든다 (있으면 아무 일도 안 함)."""
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        daily_limit INTEGER,
                        is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TEXT NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS usage_log (
                        user_id INTEGER NOT NULL,
                        usage_date TEXT NOT NULL,
                        count INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (user_id, usage_date)
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS password_resets (
                        token TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        expires_at TEXT NOT NULL
                    )
                """)
            conn.commit()
    except DatabaseUnavailableError as e:
        print(f"[db.init_db] 경고: {e}")


def create_user(email: str, password: str, daily_limit=10, is_admin: bool = False) -> bool:
    """
    새 계정 생성. 이미 있는 이메일이거나 DB 연결이 안 되면 False를 반환한다.
    daily_limit=None 이면 무제한 계정 (관리자용).
    """
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        "INSERT INTO users (email, password_hash, daily_limit, is_admin, created_at) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (
                            email.lower().strip(),
                            generate_password_hash(password),
                            daily_limit,
                            bool(is_admin),
                            date.today().isoformat(),
                        ),
                    )
                    conn.commit()
                    return True
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    return False
    except DatabaseUnavailableError as e:
        print(f"[db.create_user] 경고: {e}")
        return False


def get_user_by_email(email: str):
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE email = %s", (email.lower().strip(),))
                row = cur.fetchone()
                return dict(row) if row else None
    except DatabaseUnavailableError as e:
        print(f"[db.get_user_by_email] 경고: {e}")
        return None


def get_user_by_id(user_id: int):
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
                return dict(row) if row else None
    except DatabaseUnavailableError as e:
        print(f"[db.get_user_by_id] 경고: {e}")
        return None


def verify_password(user: dict, password: str) -> bool:
    return check_password_hash(user["password_hash"], password)


def check_and_increment_usage(user_id: int, daily_limit) -> bool:
    """
    daily_limit이 None이면 무제한이라 항상 True.
    DB 연결이 안 되면, 사용량 확인을 못 하니 안전한 쪽(False = 거부)으로 처리한다
    - 무제한 계정(daily_limit=None)은 이 확인 자체가 필요 없어서 영향 없음.
    """
    if daily_limit is None:
        return True

    today = date.today().isoformat()
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count FROM usage_log WHERE user_id = %s AND usage_date = %s",
                    (user_id, today),
                )
                row = cur.fetchone()
                current = row["count"] if row else 0

                if current >= daily_limit:
                    return False

                if row:
                    cur.execute(
                        "UPDATE usage_log SET count = count + 1 WHERE user_id = %s AND usage_date = %s",
                        (user_id, today),
                    )
                else:
                    cur.execute(
                        "INSERT INTO usage_log (user_id, usage_date, count) VALUES (%s, %s, 1)",
                        (user_id, today),
                    )
            conn.commit()
            return True
    except DatabaseUnavailableError as e:
        print(f"[db.check_and_increment_usage] 경고: {e}")
        return False


def get_today_usage(user_id: int) -> int:
    today = date.today().isoformat()
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count FROM usage_log WHERE user_id = %s AND usage_date = %s",
                    (user_id, today),
                )
                row = cur.fetchone()
                return row["count"] if row else 0
    except DatabaseUnavailableError as e:
        print(f"[db.get_today_usage] 경고: {e}")
        return 0


def ensure_admin_from_env():
    """
    환경변수 ADMIN_EMAIL / ADMIN_PASSWORD가 설정돼 있고 그 계정이 아직 없으면
    무제한 관리자 계정을 자동으로 만든다. 앱 시작할 때 한 번 호출하면 된다.
    """
    email = os.environ.get("ADMIN_EMAIL")
    password = os.environ.get("ADMIN_PASSWORD")
    if not email or not password:
        return
    if get_user_by_email(email):
        return
    create_user(email, password, daily_limit=None, is_admin=True)


def create_reset_token(user_id: int) -> str:
    """비밀번호 재설정용 토큰을 하나 만들어서 저장하고 반환한다."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.utcnow() + timedelta(minutes=RESET_TOKEN_VALID_MINUTES)).isoformat()
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO password_resets (token, user_id, expires_at) VALUES (%s, %s, %s)",
                    (token, user_id, expires_at),
                )
            conn.commit()
        return token
    except DatabaseUnavailableError as e:
        print(f"[db.create_reset_token] 경고: {e}")
        return token


def get_valid_reset_token(token: str):
    """토큰이 존재하고 아직 안 만료됐으면 dict, 아니면 None."""
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM password_resets WHERE token = %s", (token,))
                row = cur.fetchone()
                if not row:
                    return None
                if datetime.utcnow() > datetime.fromisoformat(row["expires_at"]):
                    return None
                return dict(row)
    except DatabaseUnavailableError as e:
        print(f"[db.get_valid_reset_token] 경고: {e}")
        return None


def delete_reset_token(token: str):
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM password_resets WHERE token = %s", (token,))
            conn.commit()
    except DatabaseUnavailableError as e:
        print(f"[db.delete_reset_token] 경고: {e}")


def update_password(user_id: int, new_password: str):
    try:
        with closing(_connect()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET password_hash = %s WHERE id = %s",
                    (generate_password_hash(new_password), user_id),
                )
            conn.commit()
    except DatabaseUnavailableError as e:
        print(f"[db.update_password] 경고: {e}")
