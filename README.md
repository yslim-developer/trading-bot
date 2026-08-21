# 주식 분석 자동매매 봇 (Phase 1)

TradingAgents(docker) 분석 결과를 토스증권 오픈API로 완전자동 실행하는 봇. 상세 설계는
`docs/superpowers/specs/2026-08-21-trading-bot-design.md` 참고.

## 최초 설정

1. `python -m venv venv && source venv/Scripts/activate && pip install -r requirements.txt`
2. `git submodule update --init --recursive` (TradingAgents 코드 받기)
3. `cp .env.example .env` 후 값 채우기:
   - `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`: 토스증권 오픈API 발급 값
   - `GOOGLE_API_KEY`: Gemini 무료 API 키 (TradingAgents 컨테이너가 사용)
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: 알림용 텔레그램 봇
4. `config.yaml`에서 감시 종목·리스크 한도 확인/조정
5. `docker compose build` 로 TradingAgents 이미지 빌드

## 드라이런 (실주문 전 필수 검증)

```
python orchestrator/main.py --dry-run
```

며칠간 텔레그램 알림으로 "이런 주문이 나갔을 것"을 확인한 뒤에만 실거래로 전환할 것.

## 실거래 시작

```
python orchestrator/main.py
```

## 킬스위치

`E:\MyProject\STOP` 파일을 만들면 다음 실행부터 즉시 분석·주문을 건너뛴다. 파일을 지우면 재개.

## Windows 작업 스케줄러 등록 (매일 KST 22:40 실행)

```bash
schtasks /create /tn "TradingBot Daily Run" /tr "powershell.exe -File E:\MyProject\scripts\run_daily.ps1" /sc daily /st 22:40 /f
```

미국 서머타임(EDT/EST) 전환 시 `run_time_kst` 및 위 스케줄 시각을 수동으로 1시간 조정해야 한다 (Phase 1 범위 밖 자동화).
