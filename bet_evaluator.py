"""
bet_evaluator.py - 배당 합리성 평가 (사용자가 고른 베팅·폴드의 EV를 시장 공정 확률로 평가)

앱(app.py의 /bet-check)과 명령줄 도구(backtest/evaluate_bet.py)가 함께 쓴다.

- 확률: Pinnacle 최신 배당에서 마진을 뺀 시장 공정 확률. 7개 시즌 실험에서 우리 모델보다 시장 확률이
  더 정확했다 (docs/value_picks_design.md Part D). 모델 엣지를 주장하지 않는다.
- 가격: 사용자가 입력한 배당. 생략하면 최신 1xBet 배당.
- 판정(docs/value_picks_design.md §6 확정 기준): NO_VALUE p < BE / VALUE p ≥ BE+3%p / LEAN p > BE+1%p / FAIR.
  확률이 시장 자체이므로 MODEL_ERROR 검사는 해당 없음. 점수 = EV.
- 큰 수의 법칙: 같은 베팅을 1,000번 반복했을 때의 기대 손익, 95% 범위, 이익일 확률(정규 근사).

배당 출처는 두 가지다 (계산은 같다):
- DB: odds_snapshots.db 읽기 전용 + 경기 이름은 배당 수집 로그(logs/collect_odds.log). API 호출 없음. CLI가 쓴다.
- API: API-Football /fixtures, /odds를 직접 조회. 배당 DB가 없는 웹 서버(Render)용. 앱이 쓴다.
지원: 1X2, OU(.5 라인), AH(.5 라인, 고른 팀 기준 핸디캡). 미지원: 쿼터·정수 라인, BTTS(Pinnacle 미제공).
"""

import math
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from odds_store import MIN_ODD

ODDS_DB = os.environ.get("ODDS_DB_PATH", "odds_snapshots.db")
COLLECT_LOG = os.path.join("logs", "collect_odds.log")
PINNACLE, ONEXBET = 4, 11
BET_IDS = {"1X2": 1, "AH": 4, "OU": 5, "BTTS": 8}
VALUE_MARGIN, LEAN_MARGIN = 0.03, 0.01
REPEAT_N = 1000
VERDICTS = {
    "VALUE": "배당이 공정 확률보다 충분히 좋다 (합리적)",
    "LEAN": "공정 확률보다 약간 좋지만 여유가 작다",
    "FAIR": "공정 가격과 거의 같다 (장기적으로 본전 근처)",
    "NO_VALUE": "배당이 공정 가격보다 짧다 (장기적으로 손해)",
}


# ---------------- 데이터 읽기 ----------------
def connect():
    """읽기 전용 연결. 배당 DB가 없으면 None."""
    if not os.path.exists(ODDS_DB):
        return None
    return sqlite3.connect(f"file:{ODDS_DB}?mode=ro", uri=True)


def load_fixture_names():
    """수집 로그의 '[fixture_id] 홈 vs 원정 (킥오프)' 줄에서 경기 이름을 읽는다 (가장 최근 값)."""
    names = {}
    pat = re.compile(r"^\[(\d+)\] (.+?) vs (.+?) \((\S+)\)\s*$")
    try:
        with open(COLLECT_LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.match(line.strip())
                if m:
                    names[int(m.group(1))] = {"home": m.group(2), "away": m.group(3), "kickoff": m.group(4)}
    except FileNotFoundError:
        pass
    return names


def latest_book(con, fixture_id, bookmaker_id, bet_id):
    row = con.execute("SELECT MAX(captured_at) FROM odds_snapshots WHERE fixture_id=? AND bookmaker_id=? AND bet_id=?",
                      (fixture_id, bookmaker_id, bet_id)).fetchone()
    if not row or not row[0]:
        return None
    cap = row[0]
    # 예전에 저장된 MIN_ODD 이하 행은 표시·분석에서 빠진다 (없는 배당으로 취급)
    rows = con.execute("SELECT selection, odd, odds_update FROM odds_snapshots "
                       "WHERE fixture_id=? AND bookmaker_id=? AND bet_id=? AND captured_at=? AND odd > ?",
                       (fixture_id, bookmaker_id, bet_id, cap, MIN_ODD)).fetchall()
    return {"captured_at": cap, "odds_update": rows[0][2] if rows else "", "odds": {s: o for s, o, _ in rows}}


def db_book(con):
    """evaluate_*가 쓰는 배당 조회 함수 (DB 출처)."""
    return lambda fixture_id, bookmaker_id, bet_id: latest_book(con, fixture_id, bookmaker_id, bet_id)


# ---------------- API 출처 (웹 서버용) ----------------
# shortcut: 프로세스 메모리 캐시라 서버 재시작·워커마다 따로 비워진다. 호출량이 문제되면 DB 캐시로 바꾼다.
_API_CACHE = {}
API_TTL_FIXTURES = 3600  # 경기 목록 1시간
API_TTL_ODDS = 1800      # 경기별 배당 30분
API_DAYS_AHEAD = 10      # 배당은 킥오프 약 7일 전부터 갱신된다 (odds_design.md)


def _cached(key, ttl, load):
    hit = _API_CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = load()
    _API_CACHE[key] = (time.time(), value)
    return value


def api_fixtures(season, league_id=39):
    """킥오프 전이고 API_DAYS_AHEAD일 안의 경기 {fixture_id: {home, away, kickoff}}. API 1회, 1시간 캐시."""
    import api_client

    def load():
        now = datetime.now(timezone.utc)
        out = {}
        for f in api_client.get_league_fixtures(league_id, season):
            kickoff = datetime.fromisoformat(f["fixture"]["date"])
            if f["fixture"]["status"]["short"] == "NS" and now < kickoff < now + timedelta(days=API_DAYS_AHEAD):
                out[f["fixture"]["id"]] = {"home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"],
                                           "kickoff": f["fixture"]["date"]}
        return out
    return _cached(("fixtures", league_id, season), API_TTL_FIXTURES, load)


def book_from_odds_response(odds_response, bookmaker_id, bet_id, fetched_at):
    """/odds 응답에서 latest_book과 같은 모양의 결과를 만든다. MIN_ODD 이하·숫자 아닌 배당은 뺀다."""
    for entry in odds_response:
        for bm in entry.get("bookmakers", []):
            if bm.get("id") != bookmaker_id:
                continue
            for bet in bm.get("bets", []):
                if bet.get("id") != bet_id:
                    continue
                odds = {}
                for v in bet.get("values", []):
                    try:
                        o = float(v.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    if o > MIN_ODD:
                        odds[v.get("value")] = o
                return {"captured_at": fetched_at, "odds_update": entry.get("update") or "", "odds": odds}
    return None


def api_book():
    """evaluate_*가 쓰는 배당 조회 함수 (API 출처). 경기당 /odds 1회, 30분 캐시."""
    import api_client

    def get(fixture_id, bookmaker_id, bet_id):
        fetched_at, resp = _cached(("odds", fixture_id), API_TTL_ODDS,
                                   lambda: (datetime.now(timezone.utc).isoformat(), api_client.get_odds(fixture_id)))
        return book_from_odds_response(resp, bookmaker_id, bet_id, fetched_at)
    return get


def upcoming_fixtures(con, names):
    """킥오프 전이고 Pinnacle 1X2 배당이 있는 경기 목록 (킥오프 순)."""
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for fid, n in sorted(names.items(), key=lambda x: x[1]["kickoff"]):
        if n["kickoff"] < now:
            continue
        pin = latest_book(con, fid, PINNACLE, 1)
        if pin:
            out.append({"fixture_id": fid, **n, "pinnacle_1x2": pin["odds"]})
    return out


def find_fixture(names, home_q, away_q):
    hq, aq = home_q.lower().strip(), away_q.lower().strip()
    hits = [(fid, n) for fid, n in names.items() if hq in n["home"].lower() and aq in n["away"].lower()]
    if not hits:
        return None
    hits.sort(key=lambda x: x[1]["kickoff"], reverse=True)
    return hits[0][0]


# ---------------- 선택 해석 ----------------
def parse_selection(market, sel_text, entry):
    """반환: ((selection, 짝 selection 목록, 표시 이름), None) 또는 (None, 오류 메시지)."""
    s = (sel_text or "").strip().lower()
    if market == "1X2":
        key = {"home": "Home", "draw": "Draw", "away": "Away", "홈": "Home", "무": "Draw", "원정": "Away"}.get(s)
        if not key:
            return None, "1X2 선택은 home/draw/away"
        label = {"Home": f"{entry['home']} 승", "Draw": "무승부", "Away": f"{entry['away']} 승"}[key]
        return (key, ["Home", "Draw", "Away"], label), None
    if market == "OU":
        m = re.match(r"(over|under)\s*([0-9.]+)$", s)
        if not m:
            return None, "OU 선택은 'over 2.5' 또는 'under 2.5' 형식"
        line = float(m.group(2))
        if abs(line * 2) % 2 != 1:
            return None, f"OU {line:g} 라인은 미지원 (.5 라인만. 쿼터·정수 라인은 환불·반승 분포가 필요)"
        side, ls = m.group(1).capitalize(), f"{line:g}"
        return (f"{side} {ls}", [f"Over {ls}", f"Under {ls}"], f"{'오버' if side == 'Over' else '언더'} {ls}"), None
    if market == "AH":
        m = re.match(r"(home|away)\s*([+-]?[0-9.]+)$", s)
        if not m:
            return None, "AH 선택은 'home -0.5' 또는 'away +0.5' 형식 (고른 팀 기준 핸디캡)"
        side, h = m.group(1), float(m.group(2))
        if abs(h * 2) % 2 != 1:
            return None, f"AH {h:+g} 라인은 미지원 (.5 라인만. 쿼터·정수 라인은 환불·반승 분포가 필요)"
        home_line = h if side == "home" else -h  # 저장 표기는 홈 기준 라인 ("Home -0.5", "Away -0.5")
        ls = f"{home_line:g}" if home_line < 0 else f"+{home_line:g}"
        team = entry["home"] if side == "home" else entry["away"]
        return (f"{side.capitalize()} {ls}", [f"Home {ls}", f"Away {ls}"], f"{team} {h:+g}"), None
    if market == "BTTS":
        return None, "BTTS는 Pinnacle 배당이 없어 미평가 (다른 북메이커로 대체하지 않음)"
    return None, f"알 수 없는 시장: {market}"


# ---------------- 계산 ----------------
def grade(p, be):
    if p < be:
        return "NO_VALUE"
    if p >= be + VALUE_MARGIN:
        return "VALUE"
    if p > be + LEAN_MARGIN:
        return "LEAN"
    return "FAIR"


def repeat_stats(p, o, n=REPEAT_N):
    mean = n * (p * o - 1)
    sd = math.sqrt(n * p * (1 - p)) * o
    ahead = 0.5 * (1 + math.erf((mean / sd) / math.sqrt(2))) if sd > 0 else (1.0 if mean > 0 else 0.0)
    # 단위 손익은 정수로 반올림해 둔다 ("-0" 같은 표시를 막기 위해)
    return {"mean": round(mean), "lo": round(mean - 1.96 * sd), "hi": round(mean + 1.96 * sd), "prob_ahead": ahead}


def evaluate_leg(book, names, fixture_id, market, sel_text, odds_text=""):
    """book: db_book(con) 또는 api_book() - (fixture_id, bookmaker_id, bet_id) -> 배당."""
    entry = names.get(fixture_id)
    if entry is None:
        return {"error": "경기를 찾지 못함", "label": sel_text}
    market = (market or "").upper()
    res = {"fixture_id": fixture_id, "match": f"{entry['home']} vs {entry['away']}", "kickoff": entry["kickoff"],
           "market": market, "label": sel_text}
    parsed, err = parse_selection(market, sel_text, entry)
    if err:
        return {**res, "error": err}
    sel, pair, label = parsed
    res["label"] = label
    pin = book(fixture_id, PINNACLE, BET_IDS[market])
    x1 = book(fixture_id, ONEXBET, BET_IDS[market])
    res["x1_odds"] = (x1 or {}).get("odds", {}).get(sel)
    if odds_text and str(odds_text).strip():
        try:
            o = float(str(odds_text).strip())
        except ValueError:
            return {**res, "error": f"배당 숫자 오류: {odds_text}"}
        res["odds_source"] = "입력"
    else:
        o = res["x1_odds"]
        res["odds_source"] = "1xBet 최신"
    res["odds"] = o
    if o is None or o <= MIN_ODD:
        reason = f"배당 {MIN_ODD:.2f} 이하 (평가하지 않음)" if o else "1xBet에 이 선택의 배당이 없음 (배당을 직접 입력하면 평가 가능)"
        if o is None and market in ("AH", "OU") and pin and x1:
            both = sorted({s.split()[-1] for s in x1["odds"] if s in pin["odds"]}, key=float)
            halves = [v for v in both if abs(float(v) * 2) % 2 == 1]
            if halves:
                reason += f". 1xBet·Pinnacle 둘 다 있는 .5 라인({'홈 기준' if market == 'AH' else ''}): {', '.join(halves)}"
        return {**res, "grade": "INVALID", "reason": reason}
    if not pin or not all(k in pin["odds"] and pin["odds"][k] > 1.0 for k in pair):
        return {**res, "grade": "INVALID",
                "reason": "Pinnacle 배당 없음 → 시장 공정 확률 미확인 (다른 북메이커로 대체하지 않음)"}
    inv = {k: 1 / pin["odds"][k] for k in pair}
    p = inv[sel] / sum(inv.values())
    be = 1 / o
    res.update({"p": p, "be": be, "ev": p * o - 1, "fair_odds": 1 / p, "pinnacle_margin": sum(inv.values()) - 1,
                "pinnacle_captured_at": pin["captured_at"], "pinnacle_odds_update": pin["odds_update"],
                "grade": grade(p, be), "repeat": repeat_stats(p, o)})
    res["verdict"] = VERDICTS[res["grade"]]
    return res


def evaluate_bets(book, names, leg_specs):
    """leg_specs: [{"fixture_id", "market", "selection", "odds"}]. 레그별 결과와 폴드·결론을 돌려준다."""
    legs = [evaluate_leg(book, names, s["fixture_id"], s["market"], s["selection"], s.get("odds", ""))
            for s in leg_specs]
    ok = [l for l in legs if "p" in l]
    complete = len(ok) == len(legs) and ok
    out = {"legs": legs, "fold": None, "conclusion": None}
    if len(legs) == 1:
        out["conclusion"] = (f"{ok[0]['grade']} - {ok[0]['verdict']}" if ok
                             else "평가할 수 없는 베팅 - 아래 사유 확인")
        return out
    if not complete:
        out["conclusion"] = "평가할 수 없는 레그가 있어 폴드 판단 보류"
        return out
    if len({l["fixture_id"] for l in ok}) < len(ok):
        out["conclusion"] = "같은 경기의 레그가 있어 폴드를 평가하지 않음 (상관관계 때문에 경기당 1레그만)"
        return out
    odds = math.prod(l["odds"] for l in ok)
    p = math.prod(l["p"] for l in ok)
    ev = p * odds - 1
    weakest = min(ok, key=lambda l: l["ev"])
    out["fold"] = {"n": len(ok), "odds": odds, "p": p, "be": 1 / odds, "ev": ev, "weakest": weakest,
                   "repeat": repeat_stats(p, odds)}
    out["conclusion"] = f"폴드 EV {ev * 100:+.1f}% - {'합리적' if ev > 0 else '합리적이지 않음 (장기적으로 손해)'}"
    return out


def age_hours(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600
    except (TypeError, ValueError):
        return None
