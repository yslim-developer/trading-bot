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

## 알려진 제한사항

- **기동/생존 확인 알림 미구현**: 설계 문서는 봇이 죽었는지(예: PC가 예약 시각에 아예 깨어나지 않는 경우) 감지할 수 있도록
  실행 시작/종료 시 텔레그램 하트비트를 보내는 것을 권장하지만, Phase 1에는 아직 구현되어 있지 않다. 의도적으로 다음
  세션으로 미룬 항목이며, 현재는 예약된 실행이 아예 발생하지 않아도 알림이 오지 않는다.
