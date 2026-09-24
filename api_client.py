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
from datetime import date, datetime, timedelta

import player_stats_cache

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


def _is_friendly(fixture: dict) -> bool:
    """
    친선경기 여부 판정. API-Football은 친선경기를 리그이름에
    'Friendlies'라고 표시한다 (국가대표 친선/클럽 친선 등 이름이 조금씩
    다를 수 있어서 'friend'가 들어가면 전부 친선으로 본다 - 이 정도로도
    실제 정식대회(리그/컵/챔스 등) 이름과 겹칠 일은 없다).
    """
    league_name = (fixture.get("league") or {}).get("name", "")
    return "friend" in league_name.lower()


def _fetch_season_finished(team_id: int, season_year: int, before_date: str = None) -> list:
    """
    특정 팀의 특정 시즌 "끝난 경기"를 가져오는 공통 로직. 친선경기는 항상
    제외한다 (실력 반영이 안 되는 경기라 최근폼/H2H/선수통계 어디에도
    섞이면 안 됨). 리그/컵/챔스 등 정식 대회는 전부 포함한다.
    """
    data = _get("fixtures", {"team": team_id, "season": season_year})
    fixtures = data.get("response", [])
    fixtures = [f for f in fixtures if not _is_friendly(f)]
    finished = [f for f in fixtures if f["fixture"]["status"]["short"] == "FT"]
    if before_date:
        finished = [f for f in finished if f["fixture"]["date"][:10] < before_date]
    return finished


def get_recent_fixtures(team_id: int, season: int, count: int = 5, before_date: str = None) -> list:
    """
    해당 팀의 최근 N경기 결과를 반환 (친선경기 제외, 그 외 모든 정식대회 포함).

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
    def _fetch(season_year):
        finished = _fetch_season_finished(team_id, season_year, before_date)
        finished.sort(key=lambda f: f["fixture"]["date"])
        return finished

    finished = _fetch(season)

    is_backtest = bool(before_date) and before_date <= date.today().isoformat()

    if not is_backtest and len(finished) < count:
        previous_season = _fetch(season - 1)
        needed = count - len(finished)
        finished = previous_season[-needed:] + finished

    return finished[-count:]


def get_head_to_head(team_a_id: int, team_b_id: int, count: int = 10, before_date: str = None) -> list:
    """
    두 팀의 상대전적 최근 N경기 (친선경기 제외).
    'last' 파라미터가 무료 플랜에서 막혀있을 수 있어 전체를 받아 직접 자른다.

    before_date: 지정하면 그 날짜 이전 맞대결만 카운트 (분석 시점 기준 진짜 상대전적).
    """
    data = _get("fixtures/headtohead", {"h2h": f"{team_a_id}-{team_b_id}"})
    fixtures = data.get("response", [])
    fixtures = [f for f in fixtures if not _is_friendly(f)]
    finished = [f for f in fixtures if f["fixture"]["status"]["short"] == "FT"]
    if before_date:
        finished = [f for f in finished if f["fixture"]["date"][:10] < before_date]
    finished.sort(key=lambda f: f["fixture"]["date"], reverse=True)  # 최근 것 먼저
    return finished[:count]


def get_injuries(team_id: int, season: int) -> list:
    """현재 부상/결장 선수 목록 (실시간/미래 분석 전용 - '지금' 기준 부상자)"""
    data = _get("injuries", {"team": team_id, "season": season})
    return data.get("response", [])


def is_historical_date(before_date: str) -> bool:
    """
    before_date가 실제 과거(오늘 포함) 날짜인지 판정한다. get_recent_fixtures의
    is_backtest 판정과 동일한 기준을 쓴다 - 이 판정 하나로 "과거 시점 재현
    분석"인지 "오늘/미래 실시간 분석"인지를 앱 전체에서 일관되게 나눈다.
    """
    return bool(before_date) and before_date <= date.today().isoformat()


def get_all_season_fixtures_before(team_id: int, season: int, before_date: str) -> list:
    """
    get_recent_fixtures와 달리 개수 제한(count) 없이, 그 시즌에 그 팀이 치른
    경기 중 before_date 이전에 끝난 경기를 전부 반환한다 (친선경기 제외).
    선수 시즌누적치를 과거 시점 기준으로 재구성할 때 "몇 경기까지"가 아니라
    "그때까지의 전부"가 필요하므로 별도로 만들었다.
    """
    finished = _fetch_season_finished(team_id, season, before_date)
    finished.sort(key=lambda f: f["fixture"]["date"])
    return finished


def get_fixture_player_stats(fixture_id: int, team_id: int = None) -> list:
    """
    /fixtures/players로 경기 하나의 선수별 통계(득점, 출전시간 등)를 가져온다.
    끝난 경기의 개인기록은 다시 안 바뀌므로 영구 캐시를 먼저 확인한다 -
    같은 fixture를 여러 번 요청해도 API를 다시 안 부른다.
    team_id를 주면 그 팀 선수들의 블록만 걸러서 반환한다.
    """
    cached = player_stats_cache.get_cached_player_stats(fixture_id)
    if cached is None:
        data = _get("fixtures/players", {"fixture": fixture_id})
        cached = data.get("response", [])
        player_stats_cache.set_cached_player_stats(fixture_id, cached)

    if team_id is None:
        return cached
    return [block for block in cached if block.get("team", {}).get("id") == team_id]


def get_team_players_asof(team_id: int, season: int, before_date: str) -> list:
    """
    과거 시점 재현 전용: before_date 이전에 그 팀이 치른 모든 경기를 fixtures/players로
    하나씩 모아 선수별로 직접 누적 합산해서, get_team_players()와 완전히 동일한
    응답 형태(list of {"player":{...}, "statistics":[{"goals":{"total":N}, "games":{"minutes":M}}]})로
    재구성한다. 이 형태를 그대로 유지해야 data_mapper.find_player_stats()를
    수정 없이 재사용할 수 있다.

    주의: 그 시즌 지금까지 치른 경기 수만큼 API 요청이 나간다 (fixture당 1회,
    캐시로 재요청은 방지됨). 실시간/미래 분석에는 이 함수를 쓰지 않는다
    (get_team_players()를 그대로 씀 - 요청량이 훨씬 적음).
    """
    fixtures = get_all_season_fixtures_before(team_id, season, before_date)

    aggregated = {}  # player_id -> {"name":..., "goals":0, "minutes":0}
    for fx in fixtures:
        fixture_id = fx["fixture"]["id"]
        team_blocks = get_fixture_player_stats(fixture_id, team_id=team_id)
        for block in team_blocks:
            for p in block.get("players", []):
                stats_list = p.get("statistics") or []
                if not stats_list:
                    continue
                stats = stats_list[0]
                goals = (stats.get("goals") or {}).get("total") or 0
                minutes = (stats.get("games") or {}).get("minutes") or 0
                pid = p["player"]["id"]
                name = p["player"]["name"]
                if pid not in aggregated:
                    aggregated[pid] = {"name": name, "goals": 0, "minutes": 0}
                aggregated[pid]["goals"] += goals
                aggregated[pid]["minutes"] += minutes

    return [
        {
            "player": {"id": pid, "name": agg["name"]},
            "statistics": [{"goals": {"total": agg["goals"]}, "games": {"minutes": agg["minutes"]}}],
        }
        for pid, agg in aggregated.items()
    ]


def get_injuries_for_fixture(fixture_id: int) -> list:
    """
    과거 시점 재현 전용: /injuries?fixture={id}로 '그 경기' 기준 결장자 명단을
    가져온다 (현재 시점 전체 부상자 목록이 아니라, 그 경기 자체에 묶인 스냅샷).
    양팀 선수가 섞여서 반환되는데, is_player_injured()는 이름으로만 찾으므로
    양팀 어느 쪽에 써도 문제없다. 끝난 경기 기준이라 영구 캐시한다.
    """
    cached = player_stats_cache.get_cached_injuries(fixture_id)
    if cached is None:
        data = _get("injuries", {"fixture": fixture_id})
        cached = data.get("response", [])
        player_stats_cache.set_cached_injuries(fixture_id, cached)
    return cached


def get_fixture_result(fixture_id: int) -> dict:
    """
    성능 모니터링 전용: 특정 fixture가 끝났는지, 끝났으면 최종 스코어가
    몇 대 몇인지 조회한다. 진행중이거나 예정인 경기는 is_finished=False로
    돌려준다 (호출부가 "아직 안 끝났으니 다음에 다시 확인" 처리하도록).
    """
    data = _get("fixtures", {"id": fixture_id})
    response = data.get("response", [])
    if not response:
        return {"is_finished": False, "home_goals": None, "away_goals": None}

    fixture = response[0]
    status = fixture["fixture"]["status"]["short"]
    is_finished = status == "FT"
    goals = fixture.get("goals", {})
    return {
        "is_finished": is_finished,
        "home_goals": goals.get("home") if is_finished else None,
        "away_goals": goals.get("away") if is_finished else None,
    }


def _kst_date_to_utc_range(kst_date_str: str):
    """
    'YYYY-MM-DD' 형식의 한국시간(KST, UTC+9) 날짜를, 그 하루 전체가 걸치는
    UTC 시간 범위로 변환한다. 사용자가 입력하는 날짜는 항상 한국 날짜
    기준이라고 가정한다 (웹 화면에서 보는 날짜 그대로).

    예: '2026-09-22' (한국시간 하루 전체)
        -> UTC로는 2026-09-21 15:00:00 ~ 2026-09-22 14:59:59
        (한국 자정 = UTC 전날 15시이므로)
    """
    kst_midnight = datetime.strptime(kst_date_str, "%Y-%m-%d")
    utc_start = kst_midnight - timedelta(hours=9)
    utc_end = utc_start + timedelta(days=1)
    return utc_start, utc_end


def find_fixture(team_a_id: int, team_b_id: int, target_date: str = None) -> dict:
    """
    두 팀의 특정 경기(fixture) 하나를 찾는다. 라인업 조회에 fixture id가 필요해서 쓴다.

    target_date("YYYY-MM-DD", 한국시간 기준)를 주면 그 날짜에 열린 경기를 찾는다.
    API가 주는 경기 시각은 UTC라서, 단순 날짜 문자열 비교(예: "2026-09-22" == "2026-09-22")를
    쓰면 한국시간 새벽~오전 킥오프 경기(UTC로는 전날 저녁~밤)를 못 찾는 문제가 있었다.
    이제 한국 날짜를 UTC 시간 범위로 변환해서, 그 범위 안에 들어오는 실제 킥오프
    시각을 가진 경기를 찾는 방식으로 고쳤다.

    안 주면 아직 시작 안 한(NS) 경기 중 가장 가까운 걸 찾는다 (실전 분석용).
    못 찾으면 None을 반환한다 (호출 쪽에서 "라인업 없음"으로 처리).
    """
    data = _get("fixtures/headtohead", {"h2h": f"{team_a_id}-{team_b_id}"})
    fixtures = data.get("response", [])

    if target_date:
        utc_start, utc_end = _kst_date_to_utc_range(target_date)
        for f in fixtures:
            raw_date = f["fixture"]["date"]  # 예: "2026-09-21T19:00:00+00:00"
            try:
                fixture_dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, TypeError):
                continue
            if utc_start <= fixture_dt < utc_end:
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


def get_league_fixtures(league_id: int, season: int) -> list:
    """
    특정 리그의 특정 시즌 전체 경기를 한 번에 가져온다 (백테스트용 대량 수집).
    팀별로 나눠 부르는 것보다 훨씬 적은 요청으로 시즌 전체를 커버할 수 있다.
    """
    data = _get("fixtures", {"league": league_id, "season": season})
    return data.get("response", [])


def get_fixture_statistics(fixture_id: int) -> list:
    """
    한 경기의 팀별 통계(슈팅, 유효슈팅, 점유율, 코너킥 등).
    경기가 안 끝났거나 이 리그/시즌에 통계 데이터가 없으면 빈 리스트가 올 수 있다.
    """
    data = _get("fixtures/statistics", {"fixture": fixture_id})
    return data.get("response", [])


def get_remaining_fixtures(team_id: int, season: int, league_id: int = 39) -> list:
    """
    그 팀의 이번 시즌 'EPL 정규리그' 경기 중 아직 안 열린 것만 (친선/컵대회/
    챔스·유로파 등 다른 대회는 전부 제외, 날짜순).

    league_id를 API 파라미터로 직접 넘겨서 서버 쪽에서부터 EPL만 걸러받는다
    (친선경기처럼 응답을 받은 뒤 골라내는 방식이 아니라, 애초에 다른 대회
    데이터 자체를 안 받아옴 - 더 정확하고 API 응답도 가벼워짐).

    AI 팀 전망(compute_team_outlook.py)이 "앞으로 남은 EPL 일정"만 모을 때 쓴다 -
    컵대회/유럽대항전까지 섞이면 그 대회에 많이 남아있는 팀이 부당하게
    유리해지므로(잔여경기 수 자체가 달라짐) 반드시 EPL만으로 좁혀야 한다.
    """
    data = _get("fixtures", {"team": team_id, "season": season, "league": league_id})
    fixtures = data.get("response", [])
    upcoming = [f for f in fixtures if f["fixture"]["status"]["short"] == "NS"]
    upcoming.sort(key=lambda f: f["fixture"]["date"])
    return upcoming


def get_league_teams(league_id: int, season: int) -> list:
    """
    그 리그의 그 시즌 참가팀 전체 (id, name, logo). AI 팀 전망을 만들 20개팀
    목록을 하드코딩 안 하고 API로 정확히 가져오기 위함 (승격/강등 자동 반영).
    logo도 이 응답에 이미 포함돼서 따로 호출 안 해도 된다.
    """
    data = _get("teams", {"league": league_id, "season": season})
    response = data.get("response", [])
    return [{"id": t["team"]["id"], "name": t["team"]["name"], "logo": t["team"].get("logo")} for t in response]
