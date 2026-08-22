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

## Windows 작업 스케줄러 등록 (매일 KST 22:40 실행)

PC가 예약 시각에 절전/꺼짐 상태일 수 있으므로 "놓친 시작을 최대한 빨리 실행" 옵션(`StartWhenAvailable`)을 반드시 켠다.
`schtasks /create`는 이 옵션을 노출하지 않으므로 PowerShell로 등록한다:

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-File E:\MyProject\scripts\run_daily.ps1"
$trigger = New-ScheduledTaskTrigger -Daily -At 22:40
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "TradingBot Daily Run" -Action $action -Trigger $trigger -Settings $settings -Force
```

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
