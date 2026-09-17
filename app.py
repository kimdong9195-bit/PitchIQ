"""
app.py - 축구 픽 예측 웹 서비스 (MVP, EPL 중심)

로컬 실행:
    export API_FOOTBALL_KEY="발급받은키"
    export SECRET_KEY="아무-랜덤-문자열"          # 로그인 세션 암호화용, 필수
    export ADMIN_EMAIL="본인이메일"                # 선택 - 자동으로 무제한 관리자 계정 생성
    export ADMIN_PASSWORD="본인비밀번호"           # 선택
    pip install -r requirements.txt
    python app.py
    -> http://localhost:5000 접속 (계정 없으면 /signup 에서 먼저 가입)

배포는 README.md 참고 (Render.com 무료 티어 기준 안내 포함)
"""

import os
import traceback
from functools import wraps

from flask import Flask, redirect, render_template, request, session, url_for

import api_client
import data_mapper
import db
import predictor

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-this-in-production")

CURRENT_SEASON = 2026  # 2026-27 시즌 (2026년 9월 기준 현재 진행 중인 시즌)

# 새로 가입하는 계정에 기본으로 주는 하루 분석 한도. 배포본마다 다르게 주고 싶으면
# 환경변수 DEFAULT_DAILY_LIMIT로 조절 (예: 저가형 배포는 3, 고가형은 30).
DEFAULT_DAILY_LIMIT = int(os.environ.get("DEFAULT_DAILY_LIMIT", 10))

db.init_db()
db.ensure_admin_from_env()


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.get_user_by_id(user_id)


@app.route("/", methods=["GET"])
def index():
    user = current_user()
    today_usage = db.get_today_usage(user["id"]) if user else None
    return render_template("index.html", result=None, error=None, form_data={}, user=user, today_usage=today_usage)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_template("signup.html", error=None)

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()
    if not email or not password:
        return render_template("signup.html", error="이메일과 비밀번호를 모두 입력해주세요.")
    if len(password) < 6:
        return render_template("signup.html", error="비밀번호는 6자 이상이어야 합니다.")

    created = db.create_user(email, password, daily_limit=DEFAULT_DAILY_LIMIT, is_admin=False)
    if not created:
        return render_template("signup.html", error="이미 가입된 이메일입니다.")

    user = db.get_user_by_email(email)
    session["user_id"] = user["id"]
    return redirect(url_for("index"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", error=None)

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()
    user = db.get_user_by_email(email)
    if not user or not db.verify_password(user, password):
        return render_template("login.html", error="이메일 또는 비밀번호가 올바르지 않습니다.")

    session["user_id"] = user["id"]
    return redirect(url_for("index"))


@app.route("/logout", methods=["GET"])
def logout():
    session.pop("user_id", None)
    return redirect(url_for("index"))


@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    user = current_user()
    team_a_name = request.form.get("team_a", "").strip()
    team_b_name = request.form.get("team_b", "").strip()
    core_player_a = request.form.get("core_player_a", "").strip()
    core_player_b = request.form.get("core_player_b", "").strip()
    match_date = request.form.get("match_date", "").strip()  # "YYYY-MM-DD", 없으면 시즌 최신 기준
    title_race = request.form.get("title_race") == "on"
    local_derby = request.form.get("local_derby") == "on"
    relegation_battle = request.form.get("relegation_battle") == "on"
    form_data = {
        "team_a": team_a_name, "team_b": team_b_name,
        "core_player_a": core_player_a, "core_player_b": core_player_b,
        "match_date": match_date,
        "title_race": title_race, "local_derby": local_derby, "relegation_battle": relegation_battle,
    }
    today_usage = db.get_today_usage(user["id"])

    if not team_a_name or not team_b_name:
        return render_template("index.html", result=None, error="두 팀 이름을 모두 입력해주세요.", form_data=form_data, user=user, today_usage=today_usage)

    if not db.check_and_increment_usage(user["id"], user["daily_limit"]):
        return render_template(
            "index.html", result=None,
            error=f"하루 분석 한도({user['daily_limit']}회)를 다 쓰셨습니다. 내일 다시 시도해주세요.",
            form_data=form_data, user=user, today_usage=today_usage,
        )
    today_usage = db.get_today_usage(user["id"])  # 방금 카운트 올라간 걸 반영

    before_date = match_date if match_date else None

    try:
        # 1) 팀 검색
        team_a_info = api_client.search_team(team_a_name)
        team_b_info = api_client.search_team(team_b_name)

        # 2) 최근 5경기, 상대전적, 부상정보, 선수 통계 수집
        #    match_date를 입력하면 그 날짜 "이전"에 끝난 경기만 대상으로 계산한다.
        #    (과거 경기를 백테스트할 때, 시즌 끝 무렵 폼이 아니라 그 경기 시점의
        #     진짜 최근 폼을 보기 위해 반드시 필요함)
        fixtures_a = api_client.get_recent_fixtures(team_a_info["id"], season=CURRENT_SEASON, count=5, before_date=before_date)
        fixtures_b = api_client.get_recent_fixtures(team_b_info["id"], season=CURRENT_SEASON, count=5, before_date=before_date)
        h2h_raw = api_client.get_head_to_head(team_a_info["id"], team_b_info["id"], count=6, before_date=before_date)

        players_a = api_client.get_team_players(team_a_info["id"], CURRENT_SEASON)
        players_b = api_client.get_team_players(team_b_info["id"], CURRENT_SEASON)
        injuries_a = api_client.get_injuries(team_a_info["id"], CURRENT_SEASON)
        injuries_b = api_client.get_injuries(team_b_info["id"], CURRENT_SEASON)

        # 3) 우리 데이터 구조로 변환
        home = data_mapper.build_team(
            name=team_a_info["name"], team_id=team_a_info["id"],
            fixtures=fixtures_a, players_data=players_a, injuries_data=injuries_a,
            core_player_names=[core_player_a] if core_player_a else None,
        )
        away = data_mapper.build_team(
            name=team_b_info["name"], team_id=team_b_info["id"],
            fixtures=fixtures_b, players_data=players_b, injuries_data=injuries_b,
            core_player_names=[core_player_b] if core_player_b else None,
        )
        h2h_matches = data_mapper.h2h_to_h2h_matches(h2h_raw, team_a_info["id"])

        # 3-1) 라인업 조회 (있으면). 실전 경기는 킥오프 1시간 전은 돼야 채워짐 -
        #      그 전이면 lineups가 빈 dict로 남고, 화면에서 "아직 미발표"로 처리한다.
        lineups = {}
        try:
            fixture = api_client.find_fixture(team_a_info["id"], team_b_info["id"], target_date=before_date)
            if fixture:
                lineup_raw = api_client.get_lineup(fixture["fixture"]["id"])
                lineups = data_mapper.parse_lineups(lineup_raw)
        except Exception:
            # 라인업 조회 실패는 전체 분석을 막을 이유가 없으므로 조용히 넘어간다
            traceback.print_exc()
            lineups = {}

        # 4) 예측 실행
        result = predictor.analyze_match(
            home, away, h2h_matches,
            title_race=title_race, local_derby=local_derby, relegation_battle=relegation_battle,
        )
        result["lineups"] = lineups

        return render_template("index.html", result=result, error=None, form_data=form_data, user=user, today_usage=today_usage)

    except Exception as e:
        traceback.print_exc()
        return render_template(
            "index.html", result=None,
            error=f"분석 중 오류가 발생했습니다: {e}",
            form_data=form_data, user=user, today_usage=today_usage,
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
