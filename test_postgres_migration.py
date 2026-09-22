"""
test_postgres_migration.py - Supabase 연결 후 전체 기능 검증 (사용자 환경에서 실행)

Claude의 작업환경은 인터넷이 안 되어 실제 Supabase 연결 테스트를 직접
해볼 수 없었다. 이 스크립트를 DATABASE_URL 설정한 상태에서 실행해서
실제로 검증해주세요.

사용법:
    set DATABASE_URL=postgresql://... (Windows) 또는 export (Mac/Linux)
    python test_postgres_migration.py
"""

import os
import sys

sys.path.insert(0, ".")

import db
import prediction_log

TEST_EMAIL = "테스트용_삭제해도됨@example.com"


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL이 설정되어 있지 않습니다. 먼저 설정하세요.")
        return

    section("1) DB 연결 + 테이블 생성")
    db.init_db()
    prediction_log.init_prediction_tables()
    print("init_db(), init_prediction_tables() 호출 완료 - 에러 없이 지나갔으면 연결 성공")

    section("2) 로그인/사용량 CRUD 테스트")
    created = db.create_user(TEST_EMAIL, "test_password_123", daily_limit=3)
    print(f"계정 생성: {created} (처음 실행이면 True, 재실행이면 False - 정상)")

    user = db.get_user_by_email(TEST_EMAIL)
    print(f"조회 결과: {user}")
    assert user is not None, "방금 만든 계정이 조회되어야 함"

    ok = db.verify_password(user, "test_password_123")
    print(f"비밀번호 검증: {ok} (True여야 정상)")
    assert ok

    print("\n사용량 3번 연속 증가 시도 (daily_limit=3):")
    for i in range(4):
        allowed = db.check_and_increment_usage(user["id"], 3)
        print(f"  {i+1}번째 시도: {'허용' if allowed else '거부'}")
    usage = db.get_today_usage(user["id"])
    print(f"오늘 사용량: {usage} (3이어야 함 - 4번째는 거부됐어야 하므로)")

    section("3) 비밀번호 재설정 토큰 테스트")
    token = db.create_reset_token(user["id"])
    valid = db.get_valid_reset_token(token)
    print(f"토큰 발급: {token[:15]}...")
    print(f"유효성 확인: {'유효함' if valid else '유효하지 않음(문제!)'}")
    db.delete_reset_token(token)
    after_delete = db.get_valid_reset_token(token)
    print(f"삭제 후 재조회: {'None이어야 정상 -> ' + str(after_delete is None)}")

    section("4) 예측 기록 테스트 - 같은 fixture 여러 번 분석 시 스냅샷 분리되는지")
    fake_result_1 = {
        "home_lambda": 1.8, "away_lambda": 1.1,
        "probabilities": {"home_win": 0.55, "draw": 0.25, "away_win": 0.20,
                           "btts_yes": 0.5, "btts_no": 0.5, "over_2_5": 0.6, "under_2_5": 0.4},
        "handicap": {"suggested_line": -0.5, "home_cover": 0.6, "push": 0.0, "away_cover": 0.4},
        "most_likely_scores": [((2, 1), 0.12), ((1, 0), 0.10)],
    }
    fake_result_2 = dict(fake_result_1)
    fake_result_2["probabilities"] = dict(fake_result_1["probabilities"])
    fake_result_2["probabilities"]["home_win"] = 0.70

    prediction_log.record_prediction(999001, "2026-12-25", "TestHome", "TestAway", fake_result_1)
    prediction_log.record_prediction(999001, "2026-12-25", "TestHome", "TestAway", fake_result_2)

    rows = prediction_log.get_predictions_with_results()
    same_fixture_rows = [r for r in rows if r["fixture_id"] == 999001]
    print(f"같은 fixture_id(999001)로 저장된 스냅샷 수: {len(same_fixture_rows)} (2여야 함 - 덮어쓰기 안 됐다는 증거)")
    for r in same_fixture_rows:
        print(f"  id={r['id']}  home_win={r['prob_home_win']}  기록시각={r['prediction_timestamp']}")

    section("5) 실제결과 연결 테스트 - fixture당 1개만 유지되는지")
    prediction_log.record_actual_result(999001, 2, 1)
    prediction_log.record_actual_result(999001, 9, 9)
    rows_after = prediction_log.get_predictions_with_results()
    same_fixture_after = [r for r in rows_after if r["fixture_id"] == 999001]
    actual_values = set((r["actual_home_goals"], r["actual_away_goals"]) for r in same_fixture_after)
    print(f"결과 연결 후 actual 값들: {actual_values} (딱 하나, (2,1)이어야 함 - 중복기록이 무시됐다는 증거)")

    section("6) 아직 결과 없는 예측 조회")
    unresolved = prediction_log.get_unresolved_fixture_ids()
    print(f"미해결 fixture 수: {len(unresolved)}")

    print("\n" + "=" * 70)
    print("전체 테스트 완료. 위 결과들을 확인해서 예상과 다른 부분이 있는지 봐주세요.")
    print("=" * 70)


if __name__ == "__main__":
    main()
