# 레짐 스위치 (인버스 ETF) 설계

작성일: 2026-09-03

## 목적

기존 롱온리 자동매매 봇에 **하락장 방어 + 하락에서 수익** 기능을 추가한다.
토스증권 오픈API는 공매도/옵션/마진을 지원하지 않으므로, 규칙 기반 시장
레짐 감지기가 "risk-off"로 판정하면 **보유 롱을 전량 매도하고 1x 인버스
ETF(PSQ)를 매수**한다. "risk-on"으로 복귀하면 인버스를 매도하고 기존
롱 흐름을 재개한다.

핵심 제약과 선택:
- 실제 공매도 불가 → **인버스 ETF 매수**로 합성 숏 (Q1)
- 판정 주체 → **규칙 기반 레짐 감지기**, LLM 미사용, 결정론적 (Q2)
- 기존 봇과의 관계 → **완전 스위치** (risk-off = 롱 전량 매도 후 인버스 전환) (Q3)
- 시세 데이터 → **yfinance** (Q4)
- 인버스 종목 → **PSQ (나스닥100 인버스, 1x 언레버리지)**. watchlist가
  나스닥 편중(MSFT/MU/KOID)이라 헷지 정합성이 높고, 1x라 변동성 소실이 작다 (Q5)
- 판정 규칙 → **2팩터 + 히스테리시스** (Q6)
- 스위치 규모 → **전액 스위치 + 별도 한도 `max_deploy_pct`**, 롱용 리스크
  한도는 스위치 주문에 적용하지 않음 (Q7)
- 서킷브레이커와 겹칠 때 → **스위치는 그대로 실행** (레짐 신호는 2일 확인이라
  하루 -3%로 뒤집히지 않고, 스위치 자체가 방어 행동) (Q8)

## 통합 방식

`orchestrator/main.py`의 `run()` 시퀀스에 **8.5단계**(계좌 조회 이후,
서킷브레이커 early return 이전)로 삽입한다. 별도
프로세스/스케줄을 만들지 않는다 — 토스 인증·주문·알림·SQLite·킬스위치·
승인모드·드라이런을 그대로 상속하고, 두 로직이 같은 계좌를 놓고 충돌하는
문제를 원천 차단하기 위해서다.

`regime_switch.enabled: false`(기본값)이면 6.5단계는 통째로 건너뛰며 기존
봇 동작과 100% 동일하다.

## 실행 흐름

배치 순서 (기존 단계 번호 기준):

```
7. 계좌 상태 조회 (기존)
8. 당일 손익 서킷브레이커 임계값 이하? → mark_circuit_breaker_triggered (기존)
8.5 레짐 판정 + 스위치 (신규)          ← 서킷브레이커 early return 이전
9. 서킷브레이커 발동 상태? → 알림 + early return (기존)
   ※ 8.5에서 risk_off로 스킵 처리됐으면 8.5 안에서 이미 종료되어 여기 도달 안 함
   ※ risk_on 상태이고 전이도 없으면 8.5는 아무것도 안 하고 9단계로 넘어가
     기존 서킷브레이커 로직이 그대로 동작
```

```
  8.5 레짐 판정 (regime_switch.enabled == true 일 때만):
      a. yfinance로 QQQ 260일 일봉 + ^VIX 최근값 조회 (재시도 3회)
         - 조회 실패 → prev_regime 유지, "레짐 판정 스킵" 알림,
           아래 롱 흐름(10~13)은 정상 진행 (fail-safe = 기존 동작)
         - 데이터 200일 미만 → 스킵 + 경고 로그
      b. SMA200 / SMA50 / 최근 종가 계산, VIX 최근값
      c. 오늘 하루치 원시 신호 계산 → regime_signals 테이블에 UPSERT
      d. 확인봉: 최근 confirm_days(2)일 연속 같은 원시 신호인지 카운트
      e. 히스테리시스로 새 레짐 확정 (아래 "판정 로직")
      f. 전이 판정: prev_regime vs new_regime

  레짐 = risk_off:
      - 전이 (risk_on → risk_off), 이 실행에서 최초:
          1. order-info 호출 → 종목별 매도가능수량 확보
          2. config.watchlist 각 티커 시장가 전량 매도 (per-order 격리:
             한 종목 실패해도 나머지 계속. 기존 main.py 패턴)
          3. set_regime(conn, "risk_off", run_date), pending_inverse_buy = 1
          4. 같은 실행에서 즉시 PSQ 매수 시도:
             order-info 재호출 → 매수가능금액 확인
             deploy_usd = min(매수가능금액, 방금_매도한_종목들의_직전_시장가치_합)
                          * max_deploy_pct / 100
             deploy_usd 가 최소 주문금액 이상이면 place_market_buy("PSQ", deploy_usd)
                → 성공 시 pending_inverse_buy = 0
             미결제 등으로 매수가능금액이 부족하면:
                → pending_inverse_buy 유지 + "PSQ 매수는 결제 후 다음 실행에서" 알림
          5. 10~13단계(TradingAgents 롱 분석/주문) 전체 스킵 → Gemini 호출 0
      - 이미 risk_off 상태였고 pending_inverse_buy == 1:
          위 4번만 재시도
      - 이미 risk_off 상태였고 pending_inverse_buy == 0:
          주문 없음 (PSQ 홀딩 유지), 10~13단계 스킵

  레짐 = risk_on:
      - 전이 (risk_off → risk_on):
          1. qty = toss.get_holding_quantity("PSQ"); qty > 0 이면 전량 시장가 매도
          2. set_regime(conn, "risk_on", run_date), pending_inverse_buy = 0
          3. 그 다음 기존 10~13단계 정상 진행
             (이전 롱 포트폴리오 복원 안 함 — TradingAgents가 처음부터 재구성)
             ※ PSQ 매도대금 미결제로 당일 롱 매수가 "매수여력 부족"이면,
               기존 per-order 격리로 해당 종목만 스킵되고 다음 실행에서
               TradingAgents가 다시 돌며 매수한다. risk_off 쪽의
               pending_inverse_buy 같은 명시적 플래그는 두지 않는다
               (자동 복구 경로가 이미 존재, 불필요한 복잡도).
      - 이미 risk_on 상태였음:
          기존 흐름 그대로

  14단계: 요약 텔레그램 + 헬스체크
      - 레짐 상태/전이/스위치 주문 결과를 summary_lines에 포함
```

### 서킷브레이커와의 관계

`is_circuit_breaker_triggered_today`가 참이어도 레짐 스위치는 실행한다.
서킷브레이커의 원래 용도는 "신규 롱 매수 차단"이며, 스위치는 방어 행동이므로
예외로 허용한다. 이 때문에 8.5단계는 반드시 서킷브레이커 early return(9단계)
**이전**에 배치한다 — 위 "통합 방식"의 배치 순서 표 참고. 서킷브레이커가
발동한 날 risk_off 전이가 확정되면, 8.5 안에서 스위치를 실행하고 종료하므로
9단계의 "신규 주문 없음" early return에는 도달하지 않는다.

## 판정 로직 (`orchestrator/regime.py`)

### 원시 신호 (오늘 하루치)

```
below_200    = qqq_close < sma200
death_cross  = sma50 < sma200
vix_high     = vix > vix_threshold           # 기본 25.0
raw_risk_off = below_200 and (death_cross or vix_high)
raw_risk_on  = qqq_close >= sma200
```

`raw_risk_off`와 `raw_risk_on`은 상호 배타가 아니다(둘 다 False인 중립
구간 존재). 각각 독립적으로 regime_signals에 기록한다.

### 확인봉 + 히스테리시스

```
off_confirmed = 최근 confirm_days 일 연속 raw_risk_off == True
on_confirmed  = 최근 confirm_days 일 연속 raw_risk_on  == True

if prev_regime == "risk_on":
    new_regime = "risk_off" if off_confirmed else "risk_on"
else:  # prev_regime == "risk_off"
    new_regime = "risk_on" if on_confirmed else "risk_off"
```

진입(risk_off)과 청산(risk_on)이 서로 다른 조건을 쓰는 것이 히스테리시스다.
청산 조건(단순히 200일선 회복)이 진입 조건보다 느슨해 200일선 근처
휩쏘에서 빠르게 롱으로 복귀한다.

### 최초 기동

`regime_state` 행이 없으면 `get_current_regime`은 `"risk_on"`을 반환한다
(정상 롱 모드로 시작). confirm_days 만큼 regime_signals가 쌓이기 전에는
전이가 일어나지 않는다.

### 데이터 열화 처리

| 상황 | 처리 |
|---|---|
| yfinance 전체 조회 실패 (prev = risk_on) | prev_regime 유지, "레짐 판정 스킵" 알림, 롱 흐름 정상 진행 (`_handle_regime` → False) |
| yfinance 전체 조회 실패 (prev = risk_off) | prev_regime 유지, "레짐 판정 스킵" 알림, **롱 흐름 재개하지 않음** — risk_off dispatch로 빠져 pending 인버스 매수만 재시도 (`_handle_regime` → True). 데이터가 없다고 방어 태세를 풀지 않는다 |
| 데이터 200일 미만 | `fetch_regime_data` 가 None 반환 → 위 "전체 조회 실패"와 동일 처리 |
| VIX만 조회 실패 | `vix_high = False`로 두고 SMA 조건만으로 판정 (`compute_raw_signals` 가 `vix is None` 처리) |
| `regime_switch.enabled: false` | 8.5단계 통째로 건너뜀 |

## 설정 (`config.yaml` 신규 블록)

```yaml
regime_switch:
  enabled: false          # 기본 false → 기존 봇과 동일 동작
  proxy_ticker: QQQ
  inverse_ticker: PSQ
  sma_long: 200
  sma_short: 50
  vix_threshold: 25.0
  confirm_days: 2
  max_deploy_pct: 90      # min(매수가능금액, 방금 매도한 종목들의 직전 시장가치 합) 의 90%까지 인버스 매수
```

`orchestrator/config.py`의 `AppConfig`에 `regime_switch` 필드(dataclass 또는
dict)를 추가하고, `load_config`가 `raw.get("regime_switch", {"enabled": False})`로
파싱한다.

## 데이터 모델 (`orchestrator/state.py`)

`init_db()`에 테이블 2개 추가:

```sql
CREATE TABLE IF NOT EXISTS regime_signals (
    signal_date TEXT PRIMARY KEY,
    raw_risk_off INTEGER NOT NULL,
    raw_risk_on  INTEGER NOT NULL,
    qqq_close REAL,
    sma200 REAL,
    sma50 REAL,
    vix REAL
);

CREATE TABLE IF NOT EXISTS regime_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    regime TEXT NOT NULL,                 -- 'risk_on' | 'risk_off'
    since_date TEXT NOT NULL,
    pending_inverse_buy INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
```

신규 함수:

| 함수 | 역할 |
|---|---|
| `get_current_regime(conn)` | `regime_state` 조회. 행 없으면 `("risk_on", 0)` 반환 |
| `record_regime_signal(conn, signal_date, raw_off, raw_on, metrics)` | 오늘 원시 신호 UPSERT (PK 충돌 시 REPLACE) |
| `count_consecutive_signal(conn, signal_date, kind)` | `signal_date` 이전(포함) 최근 행들에서 `raw_{kind}`가 연속 1인 일수 |
| `set_regime(conn, regime, since_date)` | `regime_state` UPSERT (id=1 고정) |
| `set_pending_inverse_buy(conn, value)` | 플래그만 갱신 |

**멱등성:**
- `signal_date` PK → 같은 날 재실행해도 신호 중복 안 쌓임
- `regime_state` 단일 행 → 같은 전이를 두 번 확정하지 않음 (이미 risk_off면
  `set_regime("risk_off")`는 no-op에 가까움; `since_date`만 첫 전이일로 유지)
- 스위치 주문도 기존 `orders` 테이블에 기록 (`ticker`='PSQ' 또는 watchlist
  종목, `reason`='regime_switch:on->off' 등). 기존 `record_order` /
  `mark_order_placed` / `has_order_been_placed` 재사용 → 재시도 감지·멱등성
  자동 상속. `client_order_id`는 기존 규칙 `{run_date}-{ticker}-{action}` 유지.

## 토스 클라이언트 (`orchestrator/toss_client.py`)

신규 메서드 `get_order_info()`:

```
GET /api/v1/order-info  (Bearer + X-Tossinvest-Account)
→ 반환: {
    "purchasing_power_usd": float,        # 현금 기반 매수 가능 금액
    "sellable_quantity": {ticker: str},   # 종목별 매도가능수량
  }
```

응답 필드 정확한 이름은 구현 시 `openapi.json`에서 확정한다. 이 메서드로
기존 `get_holding_quantity` 반복 호출(스위치 시 N콜)을 1콜로 대체할 수 있다.

## 알림

기존 `summary_lines`에 통합:

- 매 실행: `레짐: risk_on (2026-08-15부터) | QQQ 21500 / 200일선 20800 / VIX 18.2`
- 전이: `⚠️ 레짐 전환 risk_on → risk_off` + 매도 종목별 결과 + PSQ 매수 결과
  (또는 `PSQ 매수 대기 — 결제 후 다음 실행`)
- 데이터 실패: `레짐 판정 스킵 (yfinance 조회 실패) — 이전 레짐(risk_on) 유지`
- 승인 모드: 제안 메시지에 `레짐 전환: 롱 4종 매도 + PSQ $X 매수` 라인 추가

## 드라이런

- 실제 주문을 내지 않는다. "이런 스위치가 일어났을 것" 텔레그램만 보낸다.
- `set_regime` / `set_pending_inverse_buy`를 **호출하지 않는다** (드라이런은
  상태 불변, 며칠 관찰용).
- 결과: 드라이런 중에는 전이가 매일 재감지되어 알림이 반복된다 — 의도된
  동작이다 (관찰 목적). 실거래 전환 시 첫 실행이 실제 전이를 확정한다.

## 테스트

| 파일 | 커버 |
|---|---|
| `tests/test_regime.py` | 원시신호 각 팩터(below_200 / death_cross / vix_high 조합), 확인봉 카운트, 히스테리시스 비대칭(risk_on↔risk_off 전이 조건), 최초 기동, 데이터 부족/전체 실패/VIX만 실패 fail-safe |
| `tests/test_regime_switch.py` | 전이 시 매도→매수 순서, per-order 격리, pending 플래그 세팅/해제, pending 재시도 경로, 드라이런 상태 불변, 승인 거부 시 스킵, 서킷브레이커 발동일에도 스위치 실행 |
| `tests/test_state.py` (기존 확장) | `regime_signals` / `regime_state` CRUD, id=1 단일행 제약, 같은 날 재실행 멱등성 |

yfinance는 목킹한다 (고정 OHLCV 시리즈 픽스처). 프레임워크·픽스처 추가
없이 기존 pytest 스타일 유지.

## 백테스트 (`scripts/backtest_regime_switch.py`)

기존 `scripts/backtest_risk_engine.py` 패턴을 따른다. 프로덕션 봇 실행에는
불필요하며 `requirements-backtest.txt`(이미 yfinance/vectorbt 포함) 기반.

- yfinance로 QQQ / PSQ / ^VIX 과거 약 10년 일봉
- 레짐 규칙을 매일 적용 → risk_off 구간엔 PSQ 보유, risk_on 구간엔 QQQ 보유로 시뮬
- 출력:
  - 총수익률 vs 바이앤홀드(QQQ)
  - 전이 횟수, 휩쏘(7일 내 재전환) 횟수
  - 최대낙폭 비교
  - 2020 코로나 폭락 / 2022 약세장 구간 성과
- **목적**: `vix_threshold` / `confirm_days` / `sma_long` 기본값이 휩쏘로
  수익을 잠식하지 않는지 실측 검증. 기본값은 이 백테스트 결과를 보고 확정한다.

### 백테스트 결과 (2026-09-04 실행)

파라미터: `vix_threshold=25.0`, `confirm_days=2`, `sma_long=200`, `sma_short=50`.

```
기간: 2015-10-16 ~ 2026-09-02  (2735일)
전략 총수익률:      +228.5%
QQQ 바이앤홀드:     +609.1%
전이 횟수:          18
휩쏘(7일내 재전환): 1
전략 최대낙폭:      -37.5%
```

- 전이 18회 중 휩쏘 1회 → 확인봉+히스테리시스가 휩쏘를 억제함은 확인.
- 다만 전 구간 성과는 바이앤홀드에 크게 뒤짐(+228% vs +609%): 1x 인버스(PSQ)
  보유 구간의 기회비용과 200일선 재진입 지연 때문. 레짐 스위치는 수익 극대화가
  아니라 낙폭 방어 장치라는 설계 의도와 일치하나, 최대낙폭도 -37.5%로 크게
  줄지 않아 PSQ 대신 현금/단기채 옵션 검토 여지 있음.
- 참고: `_load()` 는 티커별 상이한 tz(^VIX=America/Chicago)를 날짜 단위로
  정규화한 뒤 join 한다(안 하면 조인 결과가 빈 DataFrame).

## 파일 변경 요약

**신규:**
- `orchestrator/regime.py`
- `scripts/backtest_regime_switch.py`
- `tests/test_regime.py`, `tests/test_regime_switch.py`
- `docs/superpowers/specs/2026-09-03-regime-switch-design.md` (이 문서)

**수정:**
- `orchestrator/main.py` — 8.5단계 삽입, pending 재시도
- `orchestrator/state.py` — 테이블 2개 + 함수 5개
- `orchestrator/toss_client.py` — `get_order_info()`
- `orchestrator/config.py` — `regime_switch` 파싱
- `config.yaml` — `regime_switch` 블록 (`enabled: false`)
- `requirements.txt` — `yfinance` 추가
- `README.md` — 레짐 스위치 섹션

## 남는 리스크 (설계로 제거 불가, 운영이 감수)

1. **휩쏘** — 2팩터 + 히스테리시스 + 2일 확인으로 완화하나 완전 제거 불가.
   백테스트로 기본값 검증 필수.
2. **PSQ 변동성 소실** — 1x라 작지만 장기 횡보 risk_off 구간에서 손실.
3. **yfinance 의존** — 무료 소스라 스키마 변경/차단 리스크. fail-safe(이전
   레짐 유지)로 방어하나 장기 장애 시 스위치 먹통.
4. **미국장 결제 타이밍** — risk_off 쪽은 pending_inverse_buy 플래그로,
   risk_on 쪽은 기존 per-order 격리 + 다음날 재시도로 대응하나, 최악의
   경우 각각 하루 노출 공백.
5. **비-나스닥 종목(JPM)도 QQQ 신호로 매도** — "완전 스위치" 선택의 트레이드오프.

## 미해결 / 구현 시 확정할 것

- ~~`/api/v1/order-info` 응답 필드~~ → **확정 (Task 5)**: 해당 경로는 없음.
  실제 엔드포인트는 `GET /api/v1/buying-power?currency=USD` (X-Tossinvest-Account 헤더 필요),
  응답 `result.cashBuyingPower` (decimal 문자열, "미수 미발생 기준"). `TossClient.get_purchasing_power_usd()` 가 이걸 float 로 반환.
- 토스 US 시장가 주문 최소 금액 (deploy_usd 하한) — 미확정, `regime.MIN_ORDER_USD = 1.0` 로 잠정 설정
- `since_date` 의미: 전이일 vs 마지막 갱신일 → 전이일로 고정 (`set_regime` 이 레짐 변경 시에만 갱신, Task 2)
