# 주식 분석 자동매매 봇 (Phase 1)

TradingAgents(docker) 분석 결과를 토스증권 오픈API로 완전자동 실행하는 봇. 상세 설계는
`docs/superpowers/specs/2026-08-21-trading-bot-design.md` 참고.

## 최초 설정

1. ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
2. `git submodule update --init --recursive` (TradingAgents 코드 받기)
3. `cp .env.example .env` 후 값 채우기:
   - `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`: 토스증권 오픈API 발급 값
   - `GOOGLE_API_KEY`: Gemini 무료 API 키 (TradingAgents 컨테이너가 사용)
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: 알림용 텔레그램 봇
   - `HEALTHCHECK_PING_URL` (선택): 아래 "무응답 감지" 참고
4. `config.yaml`에서 감시 종목·리스크 한도 확인/조정
5. `docker compose build` 로 TradingAgents 이미지 빌드

## 드라이런 (실주문 전 필수 검증)

```
python -m orchestrator.main --dry-run
```

며칠간 텔레그램 알림으로 "이런 주문이 나갔을 것"을 확인한 뒤에만 실거래로 전환할 것.

## 실거래 시작

```
python -m orchestrator.main
```

## 킬스위치

`E:\MyProject\STOP` 파일을 만들면 다음 실행부터 즉시 분석·주문을 건너뛴다. 파일을 지우면 재개.

주의: 이미 체결된 주문이나 기존 보유 종목에는 영향이 없다 — 다음 실행부터의 신규 판단·주문만 막는다.

## 반자동 (승인) 모드

기본값은 완전자동이지만, `config.yaml`에서 켜면 리스크 엔진이 승인한 주문을 실제로 실행하기 전에 텔레그램
승인을 받는다:

```yaml
approval:
  enabled: true
  timeout_minutes: 20
```

동작 방식:
- 그날 승인된 주문이 하나라도 있으면, 실행 전 텔레그램으로 제안 주문 목록을 보내고 답장을 기다린다(드라이런
  중에는 대기하지 않는다 — 어차피 주문이 안 나가므로).
- `timeout_minutes` 안에 `"승인"`(또는 `approve`/`yes`/`y`)으로 답장하면 그대로 주문 실행, `"거부"`(또는
  `reject`/`no`/`n`)로 답장하거나 시간 안에 응답이 없으면 그날은 주문 없이 종료한다. 판단 근거 자체는 승인
  여부와 무관하게 항상 DB에 기록된다.
- 종목별 개별 승인이 아니라 그날 제안된 주문 전체를 한 번에 승인/거부한다.
- 폴링 시작 시점 이전에 쌓여 있던 오래된 텔레그램 메시지는 무시하므로, 어제 보낸 메시지가 오늘의 승인으로
  잘못 인식되지 않는다.

## 로그

`scripts/run_daily.ps1`은 하루치 로그를 `logs/run-YYYY-MM-DD.log`로 남기고, 실행할 때마다 30일보다 오래된
로그 파일을 자동으로 정리한다.

## Windows 작업 스케줄러 등록 (평일 KST 22:40 실행)

미국 증시는 주말에 휴장이므로 토·일에는 트리거 자체가 실행되지 않도록 등록한다(스크립트 안에서 요일을 걸러내는
게 아니라, 스케줄러 트리거 단에서 막는다). PC가 예약 시각에 절전/꺼짐 상태일 수 있으므로 "놓친 시작을 최대한
빨리 실행" 옵션(`StartWhenAvailable`)을 반드시 켠다. `schtasks /create`는 이 옵션을 노출하지 않으므로
PowerShell로 등록한다:

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-File E:\MyProject\scripts\run_daily.ps1"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 22:40
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "TradingBot Daily Run" -Action $action -Trigger $trigger -Settings $settings -Force
```

미국 공휴일(추수감사절 등 증시 휴장일)은 요일 트리거로 걸러지지 않는다 — 그런 날은 봇이 그냥 실행되고, 토스
API가 휴장 정보를 제공하면 주문 자체가 거부/무시될 뿐 별도 처리는 하지 않는다(Phase 1 범위 밖).

미국 서머타임(EDT/EST) 전환 시 `run_time_kst` 및 위 스케줄 시각을 수동으로 1시간 조정해야 한다 (Phase 1 범위 밖 자동화).

## 무응답 감지 (선택, 강력 권장)

봇 프로세스 안에서만 알림을 보내는 방식으로는 "PC가 예약 시각에 아예 깨어나지 않아 스크립트가 시작조차 못 한 경우"를
감지할 수 없다 — 프로세스가 없으면 알림을 보낼 주체도 없기 때문이다. 이를 잡으려면 PC 밖의 독립적인 감시 서비스가
필요하다.

1. [healthchecks.io](https://healthchecks.io)에 무료 가입 (신용카드 불필요)
2. Check를 하나 만들고, Period를 하루(24h)보다 넉넉하게(예: Grace Time 포함 26~27시간) 설정 — 예약 시각이 매일
   조금씩 밀릴 수 있는 여유를 둔다
3. 발급된 Ping URL을 `.env`의 `HEALTHCHECK_PING_URL`에 채워 넣는다
4. (선택) healthchecks.io의 Integrations에서 Telegram 연동을 추가하면, 놓친 체크인도 기존 텔레그램 채팅방으로
   알림이 온다

설정하면 봇이 실행 시작(`/start`), 정상 종료(성공), 처리되지 않은 예외로 실패(`/fail`) 시점마다 자동으로 핑을 보낸다.
`HEALTHCHECK_PING_URL`을 비워두면 이 기능은 조용히 비활성화된다(기존 동작에 영향 없음).
