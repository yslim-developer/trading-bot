# 주식 분석 자동매매 봇 (Phase 1)

TradingAgents(docker) 분석 결과를 토스증권 오픈API로 완전자동 실행하는 봇. 상세 설계는
`docs/superpowers/specs/2026-08-21-trading-bot-design.md` 참고.

## 전체 실행 시퀀스

`orchestrator/main.py`의 `run()`이 매 실행마다(평일 22:40 스케줄 또는 수동 실행) 아래 순서로 동작한다.
각 단계는 조기 종료 지점을 갖고 있으며, 종료 시점과 무관하게 헬스체크 핑은 항상 나간다(`/start`는 STOP
체크 통과 직후, 성공은 각 종료 지점마다, 실패는 처리되지 않은 예외 발생 시).

```
1. data/ 디렉터리 생성 + SQLite 연결
2. STOP 파일 존재? ──yes──▶ 건너뜀 알림 + 헬스체크(성공) 핑 → 종료 (mark_run_started 전이라 기록 안 남음)
   │no
   ▼
3. 헬스체크(/start) 핑
4. 오늘 이미 완료됨? ──yes──▶ 건너뜀 알림 + 헬스체크(성공) 핑 → 종료
   │no
   ▼
5. mark_run_started 기록
6. 토스 API: 오늘 미국 정규장이 열리는지 확인 (휴장일이면 분석/주문 자체를 안 함)
   ──휴장──▶ 알림 + 헬스체크(성공) 핑 + mark_run_completed → 종료
   │개장
   ▼
7. 토스 API: 계좌 상태(보유종목/당일손익) 조회
8. 당일 손익이 서킷브레이커 임계값 이하? → circuit_breaker_triggered 기록
9. 오늘 서킷브레이커 발동 상태? ──yes──▶ 알림 + 헬스체크(성공) 핑 + mark_run_completed → 종료
   │no
   ▼
10. 종목별 TradingAgents 분석 (종목 하나 실패해도 나머지는 계속)
    ├─ Gemini 일일 한도 초과 → GROQ_API_KEY 설정 시 groq로 자동 폴백해 재시도, 그래도 없으면
      구분된 알림 + 해당 종목만 스킵
    └─ 그 외 실패 → 일반 알림, 해당 종목만 스킵
11. 리스크 엔진 평가 (서킷브레이커/종목당 1회 주문 한도/종목당 누적 보유 한도/일일 총 매수 한도/최대
    보유종목수 반영)
12. [승인모드 켜짐 + 드라이런 아님 + 승인된 주문 있음] ──yes──▶ 제안 주문 텔레그램 전송 →
    최대 timeout_minutes 동안 "승인"/"거부" 답장 대기 → 결과 알림
    │(승인모드 꺼짐이거나 드라이런이거나 승인된 주문 없음)
    ▼
13. 종목별로: 판단 근거 기록(record_order) → 알림 라인 추가
    ├─ 거부/드라이런/미승인 → 주문 스킵(기록은 남음. 미승인/타임아웃이면 완료 처리도 안 해서 당일 재시도 가능)
    ├─ 이미 주문 처리됨(재시도 감지) → 스킵 + 안내
    ├─ 토스 API 연결 실패 → 나머지 전부 중단(break), 구분된 알림
    ├─ 그 외 주문 실패 → 해당 건만 스킵
    └─ 주문 성공 → mark_order_placed 기록 (최대 3회 재시도. 그래도 실패하면 "체결됐으나 로컬 기록 실패" 별도 알림)
14. 전체 요약 텔레그램 전송 + 헬스체크(성공) 핑
    ├─ 승인 거부/타임아웃 → mark_run_completed 생략 (당일 재실행 시 재승인 가능)
    └─ 그 외 → mark_run_completed
   ※ 위 어디서든 처리되지 않은 예외 발생 시: 예외 알림 + 헬스체크(/fail) 핑 후 재발생(raise) → 프로세스
     비정상 종료로 Task Scheduler에 실패로 기록됨
```

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
   - `GROQ_API_KEY` (선택): 아래 "LLM 폴백" 참고
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
- 거부하거나 시간 안에 응답이 없으면 그날 실행이 "완료" 처리되지 않으므로, 같은 날 다시
  `python -m orchestrator.main`을 실행하면 재분석 후 다시 승인을 받을 수 있다.

## LLM 폴백 (Gemini 한도 초과 시 groq로 자동 전환)

Gemini 무료 티어는 하루 요청 한도가 있어(gemini-3.5-flash 기준 20회/일), 감시 종목이 늘어나면 일부 종목이
"Gemini 일일 한도 초과"로 스킵될 수 있다. `.env`에 `GROQ_API_KEY`를 채워두면, Gemini가 한도 초과로 실패한
종목만 자동으로 [groq](https://console.groq.com)의 무료 오픈웨이트 모델(`openai/gpt-oss-120b`/
`openai/gpt-oss-20b`)로 재시도한다.

- Gemini가 정상 응답하면 groq는 전혀 호출되지 않는다 — 순수 폴백이다.
- `GROQ_API_KEY`가 비어 있으면 기존과 동일하게 동작한다(해당 종목 스킵 + 구분된 알림).
- groq 계정을 여러 개 만들어 Gemini 자체를 우회하는 방식은 하지 않는다 — Google 무료 티어 약관 위반 소지가
  있어, 대신 별도 제공자로 자연스럽게 넘어가는 방식을 택했다.
- `docker/run_analysis.py`의 `PROVIDER_MODELS`에서 provider/모델 조합을 바꿀 수 있다.

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

미국 공휴일(추수감사절 등 증시 휴장일)은 요일 트리거로는 걸러지지 않지만, `main.py`가 매 실행 시 토스
오픈API의 `/api/v1/market-calendar/US`(해외 장 운영 정보)로 오늘 정규장이 열리는지 직접 확인한다. 휴장일이면
분석·주문 없이(Gemini 호출도 하지 않음) 알림만 보내고 종료한다.

**서머타임(DST) 자동 조정**: 위 등록 시각(22:40)은 미국 서머타임(EDT) 기준이다. `run_daily.ps1`은 매 실행
시작 시 .NET `TimeZoneInfo`로 오늘이 EDT인지 EST인지 확인해, 트리거 시각이 맞지 않으면(EDT면 22:40, EST면
23:40) 스스로 재등록한다 — 수동 조정이 필요 없다. 이 보정이 실패해도(예: 작업 스케줄러 접근 권한 문제) 그날
실행 자체는 막지 않고 계속 진행한다.

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
