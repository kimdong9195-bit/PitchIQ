"""
migrate_sqlite_to_postgres.py - 기존 SQLite(footy_predictor.db)가 로컬에 남아있는 경우에만 사용

Render 무료 티어는 셸 접속이 안 돼서, 실제 운영서버에 있던 SQLite 파일은
직접 꺼내올 방법이 없다. 이 스크립트는 "혹시 로컬 컴퓨터에 옛날에 쓰던
footy_predictor.db 파일이 남아있는 경우"를 위한 것이다. 없으면 이 스크립트는
필요 없다 - db.py가 처음 실행될 때 Postgres에 빈 테이블을 새로 만들고,
관리자 계정은 ensure_admin_from_env()가 자동으로 다시 만들어준다.

사용법:
    export DATABASE_URL="postgresql://..."   # Supabase 연결문자열
    python migrate_sqlite_to_postgres.py footy_predictor.db
"""

import os
import sqlite3
import sys

import psycopg2


def main():
    if len(sys.argv) < 2:
        print("사용법: python migrate_sqlite_to_postgres.py <SQLite 파일 경로>")
        return

    sqlite_path = sys.argv[1]
    if not os.path.exists(sqlite_path):
        print(f"파일이 없습니다: {sqlite_path} - 마이그레이션할 게 없으니 그냥 새로 시작하시면 됩니다.")
        return

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL 환경변수를 먼저 설정하세요.")
        return

    sconn = sqlite3.connect(sqlite_path)
    sconn.row_factory = sqlite3.Row
    pconn = psycopg2.connect(database_url)
    pcur = pconn.cursor()

    users = sconn.execute("SELECT * FROM users").fetchall()
    for u in users:
        pcur.execute(
            "INSERT INTO users (email, password_hash, daily_limit, is_admin, created_at) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (email) DO NOTHING",
            (u["email"], u["password_hash"], u["daily_limit"], bool(u["is_admin"]), u["created_at"]),
        )
    print(f"users: {len(users)}건 이전 시도")

    logs = sconn.execute("SELECT * FROM usage_log").fetchall()
    for l in logs:
        pcur.execute(
            "INSERT INTO usage_log (user_id, usage_date, count) VALUES (%s,%s,%s) "
            "ON CONFLICT (user_id, usage_date) DO NOTHING",
            (l["user_id"], l["usage_date"], l["count"]),
        )
    print(f"usage_log: {len(logs)}건 이전 시도")

    pconn.commit()
    pcur.close()
    pconn.close()
    sconn.close()
    print("완료. Supabase 대시보드의 Table Editor에서 users 테이블 확인해보세요.")


if __name__ == "__main__":
    main()
