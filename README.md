# 축구 픽 예측기 (MVP)

EPL 팀 두 개를 입력하면 API-Football에서 자동으로 최근 폼/상대전적/부상정보를
가져와서 승/무/패, 오버언더, BTTS, 핸디캡 예측을 보여주는 웹 앱입니다.

## 파일 구성

- `predictor.py` — 예측 계산 로직 (포아송 모델, 핵심 선수 보정 등). API를 전혀 모름.
- `api_client.py` — API-Football(RapidAPI) 호출 함수 모음.
- `data_mapper.py` — API 응답(JSON)을 predictor.py가 쓰는 형태로 변환.
- `app.py` — Flask 웹 서버. 위 세 모듈을 엮어서 웹 화면으로 보여줌.
- `templates/index.html` — 입력 폼 + 결과 화면.

## 1. 로컬에서 실행하기

```bash
# 1) 이 폴더로 이동
cd footy_predictor

# 2) 가상환경 만들기 (선택이지만 권장)
python -m venv venv
source venv/bin/activate        # Windows는 venv\Scripts\activate

# 3) 패키지 설치
pip install -r requirements.txt

# 4) API 키 설정 (dashboard.api-football.com에서 발급받은 키)
export API_FOOTBALL_KEY="여기에_발급받은_키_붙여넣기"     # Windows는 set API_FOOTBALL_KEY=키

# 5) 서버 실행
python app.py
```

브라우저에서 http://localhost:5000 접속하면 화면이 뜹니다.

## 2. API 키 발급받는 법

1. https://dashboard.api-football.com/register 접속
2. 구글 계정으로 가입 (또는 이메일로 가입 후 인증메일 확인)
3. 가입하면 자동으로 Free 플랜 활성화됨
4. 왼쪽 메뉴에서 "Account" → "My Access" 클릭
5. 블러 처리된 문자열에 마우스를 올리면 API 키가 보임 → 복사해서 위 4번 단계에 붙여넣기

(주의: RapidAPI를 통해 구독하는 방법도 있지만, 그 경우 base URL과 헤더 이름이
 다릅니다. 이 코드는 dashboard.api-football.com 직접 가입 기준으로 작성되었습니다.)

무료 플랜은 하루 100회 요청 제한이 있습니다. 경기 하나 분석할 때마다
팀 검색 2회 + 최근경기 2회 + 상대전적 1회 + 선수통계 2회 + 부상정보 2회 = 약 9회를
쓰므로, 하루에 10번 정도 분석 가능합니다.

## 3. 무료로 인터넷에 배포하기 (Render.com 기준)

1. 이 폴더를 GitHub 저장소로 만들어서 업로드
2. https://render.com 가입 (GitHub 계정으로 가능)
3. "New +" → "Web Service" → 방금 만든 GitHub 저장소 선택
4. 설정값:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
5. "Environment" 탭에서 환경변수 추가:
   - Key: `API_FOOTBALL_KEY`, Value: 발급받은 키
   - Key: `ADMIN_KEY`, Value: 아무 비밀 문자열 (예: 직접 정한 긴 랜덤 문자열) - 관리자 무제한 모드용
   - Key: `DAILY_LIMIT_PER_IP`, Value: `3` / `10` / `30` 중 원하는 값 (기본은 10)
6. Deploy 누르면 몇 분 후 `https://your-app-name.onrender.com` 같은 주소가 생성됨
   → 이 주소를 다른 사람과 공유하면 누구나 접속해서 쓸 수 있습니다
7. 본인은 `https://your-app-name.onrender.com/admin?key=위에서-정한-ADMIN_KEY` 로 한 번
   접속해두면, 그 브라우저는 하루 한도 제한 없이 계속 쓸 수 있습니다 (쿠키로 기억됨).
   이 링크는 본인만 알고 있어야 합니다 - 남한테 공유하면 그 사람도 무제한이 됩니다.

(Render 무료 플랜은 일정 시간 트래픽이 없으면 서버가 잠들어서
 첫 접속 시 로딩이 좀 걸릴 수 있습니다. 이건 무료 티어의 일반적인 특징입니다.)

## 4. 알려진 한계 (지금 버전의 정직한 한계)

- **팀 이름은 영문으로 정확히 입력**해야 검색이 잘 됩니다 (API가 영문 기준).
- **핵심 선수 결장 여부는 "부상자 명단"에만 의존**합니다. 로테이션으로 인한
  결장(감독 재량으로 쉬는 것)은 부상자 명단에 안 뜨기 때문에 못 잡아냅니다.
  → 더 정확히 하려면 킥오프 임박 시 `get_lineup()`으로 실제 라인업을 확인해서
  덮어쓰는 로직을 추가해야 합니다 (현재 버전엔 미포함, 다음 개선 과제).
- **On/Off 스플릿 데이터(그 선수 출전 시 vs 결장 시 팀 평균 득점)는 API-Football
  무료 플랜에 없습니다.** 그래서 보완계수는 대부분 "기본값(0.65)"으로 계산됩니다.
  더 정확히 하려면 유료 API(Opta, StatsBomb 등)나 자체 데이터 축적이 필요합니다.
- EPL 팀명이 아니어도 검색은 되지만, 다른 리그는 테스트되지 않았습니다.
