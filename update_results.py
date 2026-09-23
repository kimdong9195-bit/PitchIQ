"""
update_results.py - 끝난 경기의 실제 결과를 actual_results에 자동 기록

GitHub Actions(.github/workflows/update_results.yml)에서 매일 자동 실행된다.

원칙:
- prediction_log.get_unresolved_fixture_ids()가 이미 "아직 실제결과 없는
  fixture만" 걸러주므로, 이미 기록된 건 애초에 대상에 안 들어온다.
- 그중에서도 API로 확인해서 실제로 FT(종료)인 것만 기록하고, 진행중/예정
  경기는 건너뛴다.
- fixture 하나가 API 에러를 내도 except로 잡아서 건너뛰고 나머지는 계속
  처리한다 (하나 때문에 전체가 멈추지 않게).
- predictions 테이블은 이 스크립트에서 전혀 안 건드린다(조회도 안 함) -
  record_actual_result만 호출해서 actual_results에만 쓴다.
"""

import sys

sys.path.insert(0, ".")

import api_client
import prediction_log


def main():
    unresolved = prediction_log.get_unresolved_fixture_ids()
    print(f"미해결 fixture 수: {len(unresolved)}")

    updated, still_pending, errors = 0, 0, 0

    for item in unresolved:
        fixture_id = item.get("fixture_id")
        if fixture_id is None:
            continue

        try:
            result = api_client.get_fixture_result(fixture_id)
        except Exception as e:
            print(f"  [오류] fixture_id={fixture_id} 조회 실패: {e} - 건너뜀")
            errors += 1
            continue

        if not result["is_finished"]:
            still_pending += 1
            continue

        prediction_log.record_actual_result(fixture_id, result["home_goals"], result["away_goals"])
        print(f"  기록됨: fixture_id={fixture_id} {item.get('home_team')} "
              f"{result['home_goals']}-{result['away_goals']} {item.get('away_team')}")
        updated += 1

    print(f"\n완료 - 새로 기록: {updated}건, 아직 진행중/예정: {still_pending}건, 오류: {errors}건")


if __name__ == "__main__":
    main()
