"""
db.py - 계정과 사용량을 저장하는 SQLite 데이터베이스.

별도 DB 서버 없이 파일 하나(기본 footy_predictor.db)에 전부 저장된다.
Render 같은 곳에 배포할 때는 이 파일이 재배포마다 초기화될 수 있다는 점을
알아둬야 한다 (디스크가 영구적이지 않은 플랜에서는). 사용자가 늘어나서
데이터를 진짜 안전하게 보관해야 하면, 이 파일 방식에서 Postgres 같은
관리형 DB로 옮기는 걸 고려해야 한다 - 지금 단계에서는 이 정도로 충분하다.
"""

import os
import secrets
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.environ.get("DB_PATH", "footy_predictor.db")
RESET_TOKEN_VALID_MINUTES = 60  # 재설정 링크 유효 시간


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """앱 시작할 때 한 번 호출. 테이블이 없으면 만든다 (있으면 아무 일도 안 함)."""
    with closing(_connect()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                daily_limit INTEGER,      -- NULL이면 무제한 (관리자용)
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usage_log (
                user_id INTEGER NOT NULL,
                usage_date TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, usage_date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS password_resets (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        conn.commit()


def create_user(email: str, password: str, daily_limit=10, is_admin: bool = False) -> bool:
    """
    새 계정 생성. 이미 있는 이메일이면 False를 반환하고 아무것도 안 만든다.
    daily_limit=None 이면 무제한 계정 (관리자용).
    """
    with closing(_connect()) as conn:
        try:
            conn.execute(
                "INSERT INTO users (email, password_hash, daily_limit, is_admin, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    email.lower().strip(),
                    generate_password_hash(password),
                    daily_limit,
                    int(is_admin),
                    date.today().isoformat(),
                ),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def get_user_by_email(email: str):
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int):
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def verify_password(user: dict, password: str) -> bool:
    return check_password_hash(user["password_hash"], password)


def check_and_increment_usage(user_id: int, daily_limit) -> bool:
    """
    daily_limit이 None이면 무제한이라 항상 True.
    아니면 오늘 사용량을 확인해서, 한도 안 넘었으면 카운트 올리고 True,
    이미 다 썼으면 카운트는 그대로 두고 False.
    """
    if daily_limit is None:
        return True

    today = date.today().isoformat()
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT count FROM usage_log WHERE user_id = ? AND usage_date = ?",
            (user_id, today),
        ).fetchone()
        current = row["count"] if row else 0

        if current >= daily_limit:
            return False

        if row:
            conn.execute(
                "UPDATE usage_log SET count = count + 1 WHERE user_id = ? AND usage_date = ?",
                (user_id, today),
            )
        else:
            conn.execute(
                "INSERT INTO usage_log (user_id, usage_date, count) VALUES (?, ?, 1)",
                (user_id, today),
            )
        conn.commit()
        return True


def get_today_usage(user_id: int) -> int:
    today = date.today().isoformat()
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT count FROM usage_log WHERE user_id = ? AND usage_date = ?",
            (user_id, today),
        ).fetchone()
        return row["count"] if row else 0


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
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO password_resets (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at),
        )
        conn.commit()
    return token


def get_valid_reset_token(token: str):
    """
    토큰이 존재하고 아직 안 만료됐으면 {"user_id":...} 형태로 반환, 아니면 None.
    """
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM password_resets WHERE token = ?", (token,)
        ).fetchone()
        if not row:
            return None
        if datetime.utcnow() > datetime.fromisoformat(row["expires_at"]):
            return None
        return dict(row)


def delete_reset_token(token: str):
    with closing(_connect()) as conn:
        conn.execute("DELETE FROM password_resets WHERE token = ?", (token,))
        conn.commit()


def update_password(user_id: int, new_password: str):
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), user_id),
        )
        conn.commit()
