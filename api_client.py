"""
api_client.py - API-Football 공식(direct) API 호출 래퍼

dashboard.api-football.com에서 직접 가입한 경우 사용하는 버전입니다.
(RapidAPI를 통해 구독한 경우라면 base URL과 헤더가 다르니 주의)

환경변수 API_FOOTBALL_KEY에 API 키를 넣어두면 자동으로 인식합니다.
(코드에 키를 직접 적지 마세요 - 배포 시 노출됩니다)

사용 예:
    export API_FOOTBALL_KEY="발급받은키"
    python -c "from api_client import search_team; print(search_team('Manchester City'))"
"""

import os
import requests
from datetime import date

BASE_URL = "https://v3.football.api-sports.io"


def _headers():
    key = os.environ.get("API_FOOTBALL_KEY")
    if not key:
        raise RuntimeError(
            "환경변수 API_FOOTBALL_KEY가 설정되지 않았습니다. "
            "export API_FOOTBALL_KEY='발급받은키' 로 설정하세요."
        )
    return {
        "x-apisports-key": key,
    }


def _get(endpoint: str, params: dict) -> dict:
    resp = requests.get(f"{BASE_URL}/{endpoint}", headers=_headers(), params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"API-Football 에러: {data['errors']}")
    return data


def search_team(name: str) -> dict:
    """팀 이름으로 검색해서 첫 번째 매칭 결과(id 포함)를 반환"""
    data = _get("teams", {"search": name})
    results = data.get("response", [])
    if not results:
        raise ValueError(f"'{name}' 팀을 찾을 수 없습니다.")
    return results[0]["team"]  # {"id":..., "name":..., "country":..., ...}


def get_recent_fixtures(team_id: int, season: int, count: int = 5, before_date: str = None) -> list:
    """
    해당 팀의 최근 N경기 결과를 반환 (모든 대회 포함).

    무료 플랜은 'last' 파라미터를 못 쓰기 때문에, 시즌 전체 경기를 받아온 뒤
    끝난 경기(FT)만 걸러서 날짜순으로 정렬, 최근 N개만 잘라내는 방식으로 우회한다.

    before_date: "YYYY-MM-DD" 형식을 주면, 그 날짜 이전에 끝난 경기만 대상으로 한다.
    (분석하려는 경기 시점 기준으로 "그때의 진짜 최근 폼"을 보려면 반드시 필요함 -
     안 주면 시즌 전체에서 가장 최근 경기를 가져오므로, 과거 시즌을 분석할 때
     시즌 끝 무렵 폼으로 계산되어 실제 경기 시점과 안 맞을 수 있다)

    시즌 초반이라 해당 시즌 경기가 count개보다 적으면, 모자란 만큼 직전 시즌
    막판 경기로 채운다 (표본이 2~3경기뿐이면 폼 계산이 통계적으로 불안정해지므로).

    이 보충 로직은 "과거 경기를 백테스트하는 경우"(before_date가 실제 과거 날짜)에는
    적용하지 않는다 - 그때는 그 시점 데이터만으로 정확히 재현하는 게 목적이라
    다른 기간 데이터를 섞으면 안 되기 때문이다. 반대로 before_date가 오늘이거나
    미래 날짜라면(=아직 안 열린 경기를 예측하려는 것) 날짜를 안 준 것과 똑같이
    취급해서 보충을 적용한다.
    """
    def _fetch_season_finished(season_year):
        data = _get("fixtures", {"team": team_id, "season": season_year})
        fixtures = data.get("response", [])
        finished = [f for f in fixtures if f["fixture"]["status"]["short"] == "FT"]
        if before_date:
            finished = [f for f in finished if f["fixture"]["date"][:10] < before_date]
        finished.sort(key=lambda f: f["fixture"]["date"])
        return finished

    finished = _fetch_season_finished(season)

    is_backtest = bool(before_date) and before_date <= date.today().isoformat()

    if not is_backtest and len(finished) < count:
        previous_season = _fetch_season_finished(season - 1)
        needed = count - len(finished)
        finished = previous_season[-needed:] + finished

    return finished[-count:]


def get_head_to_head(team_a_id: int, team_b_id: int, count: int = 10, before_date: str = None) -> list:
    """
    두 팀의 상대전적 최근 N경기.
    'last' 파라미터가 무료 플랜에서 막혀있을 수 있어 전체를 받아 직접 자른다.

    before_date: 지정하면 그 날짜 이전 맞대결만 카운트 (분석 시점 기준 진짜 상대전적).
    """
    data = _get("fixtures/headtohead", {"h2h": f"{team_a_id}-{team_b_id}"})
    fixtures = data.get("response", [])
    finished = [f for f in fixtures if f["fixture"]["status"]["short"] == "FT"]
    if before_date:
        finished = [f for f in finished if f["fixture"]["date"][:10] < before_date]
    finished.sort(key=lambda f: f["fixture"]["date"], reverse=True)  # 최근 것 먼저
    return finished[:count]


def get_injuries(team_id: int, season: int) -> list:
    """현재 부상/결장 선수 목록"""
    data = _get("injuries", {"team": team_id, "season": season})
    return data.get("response", [])


def find_fixture(team_a_id: int, team_b_id: int, target_date: str = None) -> dict:
    """
    두 팀의 특정 경기(fixture) 하나를 찾는다. 라인업 조회에 fixture id가 필요해서 쓴다.

    target_date("YYYY-MM-DD")를 주면 그 날짜에 열린 경기를 찾는다 (과거 경기 백테스트용).
    안 주면 아직 시작 안 한(NS) 경기 중 가장 가까운 걸 찾는다 (실전 분석용).
    못 찾으면 None을 반환한다 (호출 쪽에서 "라인업 없음"으로 처리).
    """
    data = _get("fixtures/headtohead", {"h2h": f"{team_a_id}-{team_b_id}"})
    fixtures = data.get("response", [])

    if target_date:
        for f in fixtures:
            if f["fixture"]["date"][:10] == target_date:
                return f
        return None

    upcoming = [f for f in fixtures if f["fixture"]["status"]["short"] == "NS"]
    upcoming.sort(key=lambda f: f["fixture"]["date"])
    return upcoming[0] if upcoming else None


def get_lineup(fixture_id: int) -> list:
    """
    확정 라인업. 실전 경기는 보통 킥오프 1시간 전쯤 돼야 채워진다.
    그 전에 호출하면 빈 리스트가 돌아올 수 있다 (에러 아님 - "아직 미발표" 상태).
    """
    data = _get("fixtures/lineups", {"fixture": fixture_id})
    return data.get("response", [])


def get_team_players(team_id: int, season: int) -> list:
    """시즌 선수별 통계 (득점, 출전시간 등) - 핵심 선수 goal_share 계산용"""
    data = _get("players", {"team": team_id, "season": season})
    return data.get("response", [])
