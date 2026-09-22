"""
data_mapper.py - API-Football의 raw JSON 응답을 predictor.py의
Team / RecentMatch / H2HMatch / CorePlayer 객체로 변환한다.

API 응답 구조가 조금만 바뀌어도 여기만 고치면 되도록,
"API를 안다"는 지식은 이 파일에만 두고 predictor.py는 API를 전혀 모르게 분리했다.
"""

from typing import List, Optional
from predictor import RecentMatch, H2HMatch, CorePlayer, Team


def fixtures_to_recent_matches(fixtures: list, team_id: int) -> List[RecentMatch]:
    """
    get_recent_fixtures()의 결과를 RecentMatch 리스트로 변환.
    API는 보통 시간순(오래된 것 -> 최근 것)으로 반환하므로 뒤집어서
    index 0 = 가장 최근 경기가 되도록 정렬한다 (predictor.py의 DECAY 가중치 전제).
    """
    matches = []
    for fx in fixtures:
        home = fx["teams"]["home"]
        away = fx["teams"]["away"]
        goals = fx["goals"]
        is_home = home["id"] == team_id

        if goals["home"] is None or goals["away"] is None:
            continue  # 아직 안 끝난 경기는 스킵

        if is_home:
            goals_for, goals_against = goals["home"], goals["away"]
        else:
            goals_for, goals_against = goals["away"], goals["home"]

        matches.append(RecentMatch(goals_for=goals_for, goals_against=goals_against, is_home=is_home))

    matches.reverse()  # 최신 경기가 맨 앞에 오도록
    return matches


def h2h_to_h2h_matches(fixtures: list, team_a_id: int) -> List[H2HMatch]:
    """
    get_head_to_head()의 결과를 H2HMatch 리스트로 변환.
    team_a_id 관점에서 team_a_goals / team_b_goals / team_a_home을 채운다.
    """
    matches = []
    for fx in fixtures:
        home = fx["teams"]["home"]
        away = fx["teams"]["away"]
        goals = fx["goals"]

        if goals["home"] is None or goals["away"] is None:
            continue

        team_a_home = home["id"] == team_a_id
        if team_a_home:
            team_a_goals, team_b_goals = goals["home"], goals["away"]
        else:
            team_a_goals, team_b_goals = goals["away"], goals["home"]

        matches.append(H2HMatch(team_a_goals=team_a_goals, team_b_goals=team_b_goals, team_a_home=team_a_home))

    return matches


def find_player_stats(players_data: list, player_name: str) -> Optional[dict]:
    """
    get_team_players() 결과에서 이름이 일치하는 선수의 시즌 통계(득점, 출전시간)를 찾는다.
    이름은 부분일치(대소문자 무시)로 검색한다 (API 표기가 풀네임/약칭이 섞여있어서).
    """
    name_lower = player_name.lower()
    for entry in players_data:
        p_name = entry["player"]["name"].lower()
        if name_lower in p_name or p_name in name_lower:
            stats = entry["statistics"][0] if entry.get("statistics") else {}
            goals = (stats.get("goals") or {}).get("total") or 0
            minutes = (stats.get("games") or {}).get("minutes") or 0
            return {"goals": goals, "minutes": minutes}
    return None


def is_player_injured(injuries_data: list, player_name: str) -> bool:
    """해당 선수가 부상자 명단에 있는지 확인"""
    name_lower = player_name.lower()
    for entry in injuries_data:
        p_name = entry["player"]["name"].lower()
        if name_lower in p_name or p_name in name_lower:
            return True
    return False


def build_core_player(
    player_name: str,
    players_data: list,
    injuries_data: list,
    backup_player_name: Optional[str] = None,
) -> Optional[CorePlayer]:
    """
    선수 이름 하나로 CorePlayer 객체를 자동 구성.
    - 시즌 득점/출전시간: get_team_players() 결과에서 조회
    - 결장 여부: get_injuries() 결과에 이름이 있으면 결장으로 처리
      (주의: 이 방법으로는 '로테이션 결장'은 못 잡는다 - 부상자 명단에만 의존하는 한계가 있음.
       실제 결장 여부의 최종 확인은 get_lineup()으로 킥오프 임박 시 확정하는 게 정확함)
    - backup_player_name을 주면 그 선수의 per-90도 같이 조회해서 백업 비교 방식에 활용
    """
    stats = find_player_stats(players_data, player_name)
    if stats is None:
        return None

    is_available = not is_player_injured(injuries_data, player_name)

    backup_goals = backup_minutes = None
    if backup_player_name:
        backup_stats = find_player_stats(players_data, backup_player_name)
        if backup_stats:
            backup_goals = backup_stats["goals"]
            backup_minutes = backup_stats["minutes"]

    return CorePlayer(
        name=player_name,
        season_goals=stats["goals"],
        season_minutes=stats["minutes"],
        is_available=is_available,
        backup_goals=backup_goals,
        backup_minutes=backup_minutes,
    )


def build_team(
    name: str,
    team_id: int,
    fixtures: list,
    players_data: list,
    injuries_data: list,
    core_player_names: Optional[List[str]] = None,
) -> Team:
    """전체 데이터를 조합해서 predictor.py가 바로 쓸 수 있는 Team 객체를 만든다"""
    recent_matches = fixtures_to_recent_matches(fixtures, team_id)

    core_players = []
    for player_name in (core_player_names or []):
        cp = build_core_player(player_name, players_data, injuries_data)
        if cp:
            core_players.append(cp)

    return Team(name=name, recent_matches=recent_matches, core_players=core_players)


def _compute_pitch_positions(starters: list) -> list:
    """
    grid("행:열") 정보로 각 선수의 화면 상 위치(top%, left%)를 계산한다.
    골키퍼(행=1)는 화면 아래쪽(자기 진영), 숫자가 큰 행(공격수)일수록
    위쪽(상대 진영)에 배치한다. grid 정보가 없는 선수는 좌표 계산에서
    제외한다 (그런 선수는 화면에 그리는 쪽에서 건너뛴다).
    """
    rows = {}
    for p in starters:
        grid = p.get("grid")
        if not grid or ":" not in grid:
            continue
        try:
            row, col = (int(x) for x in grid.split(":"))
        except ValueError:
            continue
        rows.setdefault(row, []).append((col, p))

    if not rows:
        return starters

    max_row = max(rows.keys())
    positioned = []
    for row, players in rows.items():
        players.sort(key=lambda t: t[0])  # 실제 col 숫자 기준으로 순서만 맞추고, 배치는 균등 간격으로
        n = len(players)
        top_pct = 100 - (row / (max_row + 1)) * 100
        for i, (_, p) in enumerate(players):
            left_pct = (i + 1) / (n + 1) * 100
            p = dict(p)
            p["top_pct"] = round(top_pct, 1)
            p["left_pct"] = round(left_pct, 1)
            positioned.append(p)
    return positioned


def parse_lineups(lineup_data: list) -> dict:
    """
    api_client.get_lineup()의 결과를 화면에 보여줄 형태로 정리.
    반환: {팀이름: {"formation": "4-2-3-1", "starters": [...], "substitutes": [이름,...]}}

    starters의 각 원소는 {"name":..., "number":..., "pos":"G/D/M/F", "grid":"행:열",
    "top_pct":.., "left_pct":..} 형태 - top_pct/left_pct는 축구장 다이어그램에
    바로 쓸 수 있도록 미리 계산해둔 화면상 위치(%)다.
    아직 라인업이 발표 안 됐으면 lineup_data가 빈 리스트이고, 이 함수도 빈 dict를 반환한다.
    """
    result = {}
    for entry in lineup_data:
        team_name = entry["team"]["name"]
        formation = entry.get("formation") or "포메이션 미공개"
        starters = [
            {
                "name": p["player"]["name"],
                "number": p["player"].get("number"),
                "pos": p["player"].get("pos"),
                "grid": p["player"].get("grid"),
            }
            for p in entry.get("startXI", [])
        ]
        substitutes = [p["player"]["name"] for p in entry.get("substitutes", [])]
        result[team_name] = {
            "formation": formation,
            "starters": _compute_pitch_positions(starters),
            "substitutes": substitutes,
        }
    return result
