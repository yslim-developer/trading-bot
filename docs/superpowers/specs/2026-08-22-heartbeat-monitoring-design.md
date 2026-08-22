# 하트비트 / 무응답 감지 — 설계

## 목표

자동매매 봇이 PC 전원/스케줄러 문제로 아예 실행되지 않아도 이를 감지할 수 있도록, 외부 데드맨스위치
서비스(healthchecks.io)에 핑을 보낸다. 봇 프로세스 내부에서만 알림을 보내는 방식은 "프로세스가 아예 시작조차
안 된 경우"를 원천적으로 감지할 수 없으므로, 정해진 시간 안에 핑이 없으면 서비스가 자체적으로 알림을 보내는
외부 감시 방식을 사용한다.

## 설계

- `orchestrator/notifier.py`에 `ping_healthcheck(config, suffix="")` 추가. `config.healthcheck_ping_url`이
  비어 있으면 아무 것도 하지 않는다(설정 전엔 완전히 무해). 있으면 `GET {url}{suffix}`를 보내고, 실패해도
  `send_telegram_message`와 동일하게 예외를 삼키고 타입명만 출력한다.
- `orchestrator/config.py`의 `AppConfig`에 `healthcheck_ping_url: str` 필드 추가. 필수 시크릿과 달리
  `os.environ.get("HEALTHCHECK_PING_URL", "")`로 읽어 미설정 시에도 `load_config()`가 실패하지 않는다.
- `orchestrator/main.py`에 3개 지점 추가:
  - STOP 파일 체크를 통과한 직후 → `ping_healthcheck(config, "/start")`
  - 정상적으로 종료되는 모든 경로(STOP 스킵, 이미 완료 스킵, 서킷브레이커 발동, 정상 완료) → 각 경로의 기존
    `send_telegram_message` 호출 옆에 `ping_healthcheck(config)` 추가
  - 최상위 `except Exception` 블록(재발생 전) → `ping_healthcheck(config, "/fail")`
- `.env.example`에 `HEALTHCHECK_PING_URL=` 추가.
- `README.md`에 healthchecks.io 가입/설정 안내 추가.

## 테스트

`tests/test_notifier.py`에 기존 텔레그램 테스트와 같은 패턴으로: URL 미설정 시 `requests.get`이 호출되지
않음, URL 설정 시 올바른 엔드포인트로 요청, 요청 실패 시 예외가 밖으로 전파되지 않음.

## 범위 밖

healthchecks.io 계정 생성과 실제 핑 URL 발급은 사용자가 나중에 직접 진행한다. 코드는 환경변수가 없어도 안전하게
동작해야 한다.
