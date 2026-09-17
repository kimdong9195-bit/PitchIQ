"""
mailer.py - 비밀번호 재설정 이메일 발송.

Gmail SMTP를 사용한다. 필요한 환경변수:
    SMTP_EMAIL      - 보내는 사람 Gmail 주소
    SMTP_PASSWORD   - Gmail "앱 비밀번호" (일반 로그인 비밀번호 아님! 아래 참고)
    SMTP_SERVER     - 기본값 smtp.gmail.com (다른 메일 서비스 쓰면 바꾸면 됨)
    SMTP_PORT       - 기본값 587

Gmail 앱 비밀번호 만드는 법:
    1. Google 계정 → 보안 → 2단계 인증 켜기 (필수)
    2. 같은 페이지에서 "앱 비밀번호" 검색 → 생성
    3. 생성된 16자리 문자열을 SMTP_PASSWORD에 넣기 (일반 Gmail 비밀번호 아님)

SMTP_EMAIL / SMTP_PASSWORD를 설정 안 해두면, 이메일을 실제로 보내는 대신
터미널(서버 로그)에 재설정 링크를 그대로 출력한다. 로컬 개발/테스트 때
이메일 설정 없이도 흐름을 확인할 수 있게 하기 위한 것 - 실제 서비스에서는
반드시 두 값을 설정해야 진짜 이메일이 나간다.
"""

import os
import smtplib
from email.mime.text import MIMEText

SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))


def send_password_reset_email(to_email: str, reset_link: str) -> None:
    subject = "[PitchIQ] 비밀번호 재설정 안내"
    body = (
        f"비밀번호 재설정을 요청하셨습니다.\n\n"
        f"아래 링크를 눌러 새 비밀번호를 설정해주세요 (1시간 동안만 유효합니다):\n"
        f"{reset_link}\n\n"
        f"본인이 요청한 게 아니라면 이 메일을 무시하셔도 됩니다."
    )

    if not SMTP_EMAIL or not SMTP_PASSWORD:
        # 이메일 설정이 안 되어 있으면, 실제로 보내는 대신 로그에 링크를 남긴다.
        print(f"[mailer] SMTP 설정이 없어 이메일을 실제로 보내지 않았습니다.")
        print(f"[mailer] {to_email} 앞으로 보낼 재설정 링크: {reset_link}")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_EMAIL
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, [to_email], msg.as_string())
