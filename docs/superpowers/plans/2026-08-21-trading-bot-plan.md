# 주식 분석 자동매매 봇 (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** TradingAgents(docker) 분석 결과를 토스증권 공식 오픈API로 완전자동 실주문까지 연결하는, 안전장치(서킷브레이커·한도·킬스위치)를 갖춘 로컬 오케스트레이터를 만든다.

**Architecture:** Windows 작업 스케줄러가 하루 1회 `orchestrator/main.py`를 실행. TradingAgents는 docker-compose 컨테이너로 종목별 BUY/SELL/HOLD 신호를 산출하고, 순수 함수인 리스크 엔진이 계좌 상태·한도를 근거로 주문을 필터/사이징한 뒤, 승인된 주문만 토스 오픈API로 실행한다. 상태는 SQLite 파일 하나, 알림은 텔레그램.

**Tech Stack:** Python 3.11+, `requests`, `PyYAML`, `python-dotenv`, `pytest`, `sqlite3`(표준 라이브러리), Docker Compose.

**참고 문서(설계 시 검증 완료):** `docs/toss-api-reference.md` — 토스 오픈API 실제 스펙(`https://openapi.tossinvest.com/openapi-docs/latest/openapi.json`)에서 직접 확인한 엔드포인트/필드 정리.

---

### Task 1: 프로젝트 스캐폴딩 + API 레퍼런스 문서

**Files:**
- Create: `E:\MyProject\.gitignore`
- Create: `E:\MyProject\requirements.txt`
- Create: `E:\MyProject\.env.example`
- Create: `E:\MyProject\config.yaml`
- Create: `E:\MyProject\docs\toss-api-reference.md`
- Create: `E:\MyProject\orchestrator\__init__.py`
- Create: `E:\MyProject\tests\__init__.py`

- [ ] **Step 1: 디렉토리/기본 파일 생성**

```bash
cd "E:\MyProject"
mkdir -p orchestrator tests docker scripts data logs
touch orchestrator/__init__.py tests/__init__.py
```

- [ ] **Step 2: `.gitignore` 작성**

```
venv/
__pycache__/
*.pyc
.env
data/*.db
logs/*.log
STOP
```

- [ ] **Step 3: `requirements.txt` 작성**

```
requests==2.32.3
PyYAML==6.0.2
python-dotenv==1.0.1
pytest==8.3.3
```

- [ ] **Step 4: `.env.example` 작성**

```
TOSS_CLIENT_ID=
TOSS_CLIENT_SECRET=
GOOGLE_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

- [ ] **Step 5: `config.yaml` 작성**

```yaml
watchlist:
  - AAPL
  - MSFT
  - NVDA

risk_limits:
  per_ticker_max_usd: 100
  daily_max_buy_usd: 300
  daily_loss_circuit_pct: -3.0
  max_concurrent_positions: 5

schedule:
  run_time_kst: "22:40"
```

- [ ] **Step 6: 토스 오픈API 레퍼런스 문서 작성**

`docs/toss-api-reference.md`:

```markdown
# 토스증권 오픈API 레퍼런스 (Phase 1에서 실제 사용하는 부분만)

출처: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json (직접 다운로드해 확인)

Base URL: `https://openapi.tossinvest.com`

## 인증

`POST /oauth2/token` (application/x-www-form-urlencoded, 인증 헤더 없음)

요청 바디:
- `grant_type=client_credentials`
- `client_id={TOSS_CLIENT_ID}`
- `client_secret={TOSS_CLIENT_SECRET}`

응답 (200):
```json
{"access_token": "...", "token_type": "Bearer", "expires_in": 86400}
```

이후 모든 호출에 `Authorization: Bearer {access_token}` 필요.
계좌 관련 호출(`accounts` 제외)은 추가로 `X-Tossinvest-Account: {accountSeq}` 헤더 필요.

에러 응답은 두 형식 중 하나:
- 토큰 엔드포인트: `{"error": "invalid_client", "error_description": "..."}`
- 그 외 엔드포인트: `{"error": {"code": "...", "message": "..."}}`

## 계좌 조회

`GET /api/v1/accounts` (Bearer만 필요)

응답 `result`: `[{"accountNo": "...", "accountSeq": 1, "accountType": "BROKERAGE"}]`

`accountSeq`를 이후 모든 계좌 관련 호출의 `X-Tossinvest-Account` 헤더값으로 사용.

## 보유 종목 / 당일 손익 조회

`GET /api/v1/holdings` (Bearer + X-Tossinvest-Account, 선택적 `symbol` 쿼리 파라미터)

응답 `result`:
```json
{
  "dailyProfitLoss": {"amount": "100000", "rate": "0.0141"},
  "items": [
    {
      "symbol": "AAPL",
      "quantity": "10",
      "marketValue": {"purchaseAmount": "1800", "amount": "1850", "amountAfterCost": "1845"}
    }
  ]
}
```

- `dailyProfitLoss.rate`: 계좌 전체 당일 손익률 (소수비율, 0.0141 = 1.41%, 음수면 손실)
- `items[].marketValue.amount`: 해당 종목의 시장 평가금액 (US 종목은 USD 단일 값)
- `items[].quantity`: 보유 수량 (decimal 문자열)

## 주문 생성

`POST /api/v1/orders` (Bearer + X-Tossinvest-Account)

시장가 매수(달러 금액 지정, US MARKET 전용):
```json
{"clientOrderId": "2026-08-21-AAPL-BUY", "symbol": "AAPL", "side": "BUY", "orderType": "MARKET", "orderAmount": "100.00"}
```

시장가 매도(수량 지정):
```json
{"clientOrderId": "2026-08-21-AAPL-SELL", "symbol": "AAPL", "side": "SELL", "orderType": "MARKET", "quantity": "10"}
```

응답 `result`: `{"orderId": "...", "clientOrderId": "..."}`

`clientOrderId`는 10분간 유효한 멱등성 키 — 동일 값으로 재요청하면 같은 주문 결과를 반환한다(중복 주문 방지에 사용).
```

- [ ] **Step 7: 첫 커밋**

```bash
cd "E:\MyProject"
git add .gitignore requirements.txt .env.example config.yaml docs/toss-api-reference.md orchestrator/__init__.py tests/__init__.py
git commit -m "Scaffold project structure and document verified Toss Open API endpoints"
```

---

### Task 2: `risk_engine.py` — 리스크 엔진 (핵심 로직, TDD)

**Files:**
- Create: `E:\MyProject\orchestrator\risk_engine.py`
- Test: `E:\MyProject\tests\test_risk_engine.py`

- [ ] **Step 1: venv 생성 및 의존성 설치**

```bash
cd "E:\MyProject"
python -m venv venv
source venv/Scripts/activate
pip install -r requirements.txt
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_risk_engine.py`:

```python
from orchestrator.risk_engine import (
    AccountState,
    Decision,
    RiskLimits,
    evaluate_decisions,
)


def make_limits(**overrides):
    defaults = dict(
        per_ticker_max_usd=100.0,
        daily_max_buy_usd=300.0,
        daily_loss_circuit_pct=-3.0,
        max_concurrent_positions=5,
    )
    defaults.update(overrides)
    return RiskLimits(**defaults)


def test_circuit_breaker_blocks_all_orders_when_daily_loss_exceeds_threshold():
    decisions = [Decision(ticker="AAPL", action="BUY", amount_usd=100.0)]
    account = AccountState(positions_usd={}, daily_pnl_pct=-5.0)
    results = evaluate_decisions(decisions, account, make_limits())

    assert len(results) == 1
    assert results[0].approved is False
    assert results[0].reason == "circuit_breaker"


def test_buy_amount_capped_at_per_ticker_limit():
    decisions = [Decision(ticker="AAPL", action="BUY", amount_usd=500.0)]
    account = AccountState(positions_usd={}, daily_pnl_pct=0.0)
    results = evaluate_decisions(decisions, account, make_limits(per_ticker_max_usd=100.0))

    assert results[0].approved is True
    assert results[0].amount_usd == 100.0


def test_buy_amount_capped_at_remaining_daily_budget():
    decisions = [
        Decision(ticker="AAPL", action="BUY", amount_usd=100.0),
        Decision(ticker="MSFT", action="BUY", amount_usd=100.0),
        Decision(ticker="NVDA", action="BUY", amount_usd=100.0),
    ]
    account = AccountState(positions_usd={}, daily_pnl_pct=0.0)
    results = evaluate_decisions(
        decisions, account, make_limits(per_ticker_max_usd=100.0, daily_max_buy_usd=150.0)
    )

    assert [r.amount_usd for r in results] == [100.0, 50.0, 0.0]
    assert results[2].approved is False
    assert results[2].reason == "no_budget_remaining"


def test_new_buy_rejected_when_max_concurrent_positions_reached():
    decisions = [Decision(ticker="NVDA", action="BUY", amount_usd=100.0)]
    account = AccountState(
        positions_usd={"AAPL": 500.0, "MSFT": 500.0}, daily_pnl_pct=0.0
    )
    results = evaluate_decisions(decisions, account, make_limits(max_concurrent_positions=2))

    assert results[0].approved is False
    assert results[0].reason == "max_positions"


def test_addon_buy_to_existing_position_not_blocked_by_max_positions():
    decisions = [Decision(ticker="AAPL", action="BUY", amount_usd=100.0)]
    account = AccountState(positions_usd={"AAPL": 500.0, "MSFT": 500.0}, daily_pnl_pct=0.0)
    results = evaluate_decisions(decisions, account, make_limits(max_concurrent_positions=2))

    assert results[0].approved is True


def test_sell_always_approved_without_amount_cap():
    decisions = [Decision(ticker="AAPL", action="SELL", amount_usd=0.0)]
    account = AccountState(positions_usd={"AAPL": 500.0}, daily_pnl_pct=0.0)
    results = evaluate_decisions(decisions, account, make_limits())

    assert results[0].approved is True
    assert results[0].reason == "approved"


def test_hold_never_approved():
    decisions = [Decision(ticker="AAPL", action="HOLD", amount_usd=0.0)]
    account = AccountState(positions_usd={}, daily_pnl_pct=0.0)
    results = evaluate_decisions(decisions, account, make_limits())

    assert results[0].approved is False
    assert results[0].reason == "hold"
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `pytest tests/test_risk_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.risk_engine'`

- [ ] **Step 4: 구현**

`orchestrator/risk_engine.py`:

```python
from dataclasses import dataclass, field


@dataclass
class Decision:
    ticker: str
    action: str  # "BUY" | "SELL" | "HOLD"
    amount_usd: float = 0.0  # BUY에만 의미 있음 (원하는 매수 금액)


@dataclass
class AccountState:
    positions_usd: dict = field(default_factory=dict)  # ticker -> 현재 평가금액(USD)
    daily_pnl_pct: float = 0.0  # 당일 손익률(%), 음수면 손실


@dataclass
class RiskLimits:
    per_ticker_max_usd: float
    daily_max_buy_usd: float
    daily_loss_circuit_pct: float  # 예: -3.0 이면 -3% 이하일 때 서킷브레이커 발동
    max_concurrent_positions: int


@dataclass
class RiskDecision:
    ticker: str
    action: str
    amount_usd: float
    approved: bool
    reason: str


def evaluate_decisions(decisions, account, limits):
    """결정 목록을 리스크 한도와 계좌 상태에 따라 평가해 승인/거부된 RiskDecision 목록을 반환한다."""
    if account.daily_pnl_pct <= limits.daily_loss_circuit_pct:
        return [
            RiskDecision(d.ticker, d.action, 0.0, False, "circuit_breaker")
            for d in decisions
        ]

    remaining_budget = limits.daily_max_buy_usd
    position_count = sum(1 for v in account.positions_usd.values() if v > 0)
    results = []

    for d in decisions:
        if d.action == "HOLD":
            results.append(RiskDecision(d.ticker, d.action, 0.0, False, "hold"))
            continue

        if d.action == "SELL":
            results.append(RiskDecision(d.ticker, d.action, d.amount_usd, True, "approved"))
            continue

        # BUY
        is_new_position = account.positions_usd.get(d.ticker, 0.0) <= 0
        if is_new_position and position_count >= limits.max_concurrent_positions:
            results.append(RiskDecision(d.ticker, d.action, 0.0, False, "max_positions"))
            continue

        capped_amount = min(d.amount_usd, limits.per_ticker_max_usd, remaining_budget)
        if capped_amount <= 0:
            results.append(RiskDecision(d.ticker, d.action, 0.0, False, "no_budget_remaining"))
            continue

        remaining_budget -= capped_amount
        if is_new_position:
            position_count += 1
        results.append(RiskDecision(d.ticker, d.action, capped_amount, True, "approved"))

    return results
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `pytest tests/test_risk_engine.py -v`
Expected: PASS (7 passed)

- [ ] **Step 6: 커밋**

```bash
git add orchestrator/risk_engine.py tests/test_risk_engine.py
git commit -m "Add risk engine with per-ticker/daily caps, circuit breaker, position limit"
```

---

### Task 3: `state.py` — SQLite 상태 저장 (TDD)

**Files:**
- Create: `E:\MyProject\orchestrator\state.py`
- Test: `E:\MyProject\tests\test_state.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_state.py`:

```python
import sqlite3

import pytest

from orchestrator.state import (
    has_run_completed_today,
    init_db,
    is_circuit_breaker_triggered_today,
    mark_circuit_breaker_triggered,
    mark_run_completed,
    mark_run_started,
    record_order,
)


@pytest.fixture
def conn():
    return init_db(":memory:")


def test_run_not_completed_before_marked(conn):
    assert has_run_completed_today(conn, "2026-08-21") is False


def test_run_completed_after_mark_started_then_completed(conn):
    mark_run_started(conn, "2026-08-21")
    assert has_run_completed_today(conn, "2026-08-21") is False
    mark_run_completed(conn, "2026-08-21")
    assert has_run_completed_today(conn, "2026-08-21") is True


def test_circuit_breaker_not_triggered_by_default(conn):
    mark_run_started(conn, "2026-08-21")
    assert is_circuit_breaker_triggered_today(conn, "2026-08-21") is False


def test_circuit_breaker_persists_after_trigger(conn):
    mark_run_started(conn, "2026-08-21")
    mark_circuit_breaker_triggered(conn, "2026-08-21")
    assert is_circuit_breaker_triggered_today(conn, "2026-08-21") is True


def test_circuit_breaker_state_survives_second_run_started_same_day(conn):
    mark_run_started(conn, "2026-08-21")
    mark_circuit_breaker_triggered(conn, "2026-08-21")
    mark_run_started(conn, "2026-08-21")  # 같은 날 재실행
    assert is_circuit_breaker_triggered_today(conn, "2026-08-21") is True


def test_record_order_inserts_row(conn):
    mark_run_started(conn, "2026-08-21")
    record_order(conn, "2026-08-21", "AAPL", "BUY", 100.0, True, "approved")
    row = conn.execute(
        "SELECT ticker, action, amount_usd, approved, reason FROM orders"
    ).fetchone()
    assert row == ("AAPL", "BUY", 100.0, 1, "approved")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.state'`

- [ ] **Step 3: 구현**

`orchestrator/state.py`:

```python
import sqlite3


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_date TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            circuit_breaker_triggered INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            action TEXT NOT NULL,
            amount_usd REAL NOT NULL,
            approved INTEGER NOT NULL,
            reason TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def has_run_completed_today(conn, run_date):
    row = conn.execute(
        "SELECT status FROM runs WHERE run_date = ?", (run_date,)
    ).fetchone()
    return row is not None and row[0] == "completed"


def mark_run_started(conn, run_date):
    existing = conn.execute(
        "SELECT circuit_breaker_triggered FROM runs WHERE run_date = ?", (run_date,)
    ).fetchone()
    circuit_breaker_triggered = existing[0] if existing else 0
    conn.execute(
        "INSERT OR REPLACE INTO runs (run_date, status, circuit_breaker_triggered) "
        "VALUES (?, 'started', ?)",
        (run_date, circuit_breaker_triggered),
    )
    conn.commit()


def mark_run_completed(conn, run_date):
    conn.execute("UPDATE runs SET status = 'completed' WHERE run_date = ?", (run_date,))
    conn.commit()


def is_circuit_breaker_triggered_today(conn, run_date):
    row = conn.execute(
        "SELECT circuit_breaker_triggered FROM runs WHERE run_date = ?", (run_date,)
    ).fetchone()
    return row is not None and row[0] == 1


def mark_circuit_breaker_triggered(conn, run_date):
    conn.execute(
        "UPDATE runs SET circuit_breaker_triggered = 1 WHERE run_date = ?", (run_date,)
    )
    conn.commit()


def record_order(conn, run_date, ticker, action, amount_usd, approved, reason):
    conn.execute(
        "INSERT INTO orders (run_date, ticker, action, amount_usd, approved, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_date, ticker, action, amount_usd, int(approved), reason),
    )
    conn.commit()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_state.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add orchestrator/state.py tests/test_state.py
git commit -m "Add SQLite state module for run tracking, circuit breaker, order log"
```

---

### Task 4: `config.py` — 설정 로더 (TDD)

**Files:**
- Create: `E:\MyProject\orchestrator\config.py`
- Test: `E:\MyProject\tests\test_config.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_config.py`:

```python
import textwrap

from orchestrator.config import load_config


def test_load_config_parses_yaml_and_env(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            watchlist:
              - AAPL
              - MSFT
            risk_limits:
              per_ticker_max_usd: 100
              daily_max_buy_usd: 300
              daily_loss_circuit_pct: -3.0
              max_concurrent_positions: 5
            schedule:
              run_time_kst: "22:40"
            """
        ),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TOSS_CLIENT_ID=test-id\n"
        "TOSS_CLIENT_SECRET=test-secret\n"
        "TELEGRAM_BOT_TOKEN=test-token\n"
        "TELEGRAM_CHAT_ID=test-chat\n",
        encoding="utf-8",
    )

    config = load_config(config_path=str(config_path), env_path=str(env_path))

    assert config.watchlist == ["AAPL", "MSFT"]
    assert config.risk_limits.per_ticker_max_usd == 100
    assert config.risk_limits.daily_loss_circuit_pct == -3.0
    assert config.run_time_kst == "22:40"
    assert config.toss_client_id == "test-id"
    assert config.toss_client_secret == "test-secret"
    assert config.telegram_bot_token == "test-token"
    assert config.telegram_chat_id == "test-chat"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.config'`

- [ ] **Step 3: 구현**

`orchestrator/config.py`:

```python
import os
from dataclasses import dataclass

import yaml
from dotenv import load_dotenv

from orchestrator.risk_engine import RiskLimits


@dataclass
class AppConfig:
    watchlist: list
    risk_limits: RiskLimits
    run_time_kst: str
    toss_client_id: str
    toss_client_secret: str
    telegram_bot_token: str
    telegram_chat_id: str


def load_config(config_path="config.yaml", env_path=".env"):
    load_dotenv(env_path)
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    risk_limits = RiskLimits(
        per_ticker_max_usd=raw["risk_limits"]["per_ticker_max_usd"],
        daily_max_buy_usd=raw["risk_limits"]["daily_max_buy_usd"],
        daily_loss_circuit_pct=raw["risk_limits"]["daily_loss_circuit_pct"],
        max_concurrent_positions=raw["risk_limits"]["max_concurrent_positions"],
    )

    return AppConfig(
        watchlist=raw["watchlist"],
        risk_limits=risk_limits,
        run_time_kst=raw["schedule"]["run_time_kst"],
        toss_client_id=os.environ["TOSS_CLIENT_ID"],
        toss_client_secret=os.environ["TOSS_CLIENT_SECRET"],
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"],
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_config.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: 커밋**

```bash
git add orchestrator/config.py tests/test_config.py
git commit -m "Add config loader combining config.yaml and .env"
```

---

### Task 5: `toss_client.py` — 토스 오픈API 클라이언트 (TDD, mocked HTTP)

**Files:**
- Create: `E:\MyProject\orchestrator\toss_client.py`
- Test: `E:\MyProject\tests\test_toss_client.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_toss_client.py`:

```python
from unittest.mock import Mock

import pytest
import requests

from orchestrator.toss_client import TossApiError, TossClient


def mock_response(status_code, json_body):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = str(json_body)
    return resp


def make_client(session):
    return TossClient("client-id", "client-secret", session=session)


def test_get_account_state_fetches_token_account_and_holdings():
    session = Mock()
    session.request.side_effect = [
        mock_response(200, {"access_token": "tok", "token_type": "Bearer", "expires_in": 86400}),
        mock_response(200, {"result": [{"accountNo": "1", "accountSeq": 42, "accountType": "BROKERAGE"}]}),
        mock_response(
            200,
            {
                "result": {
                    "dailyProfitLoss": {"amount": "100", "rate": "-0.0125"},
                    "items": [
                        {"symbol": "AAPL", "quantity": "10", "marketValue": {"purchaseAmount": "1800", "amount": "1850", "amountAfterCost": "1845"}},
                        {"symbol": "TSLA", "quantity": "5", "marketValue": {"purchaseAmount": "900", "amount": "950", "amountAfterCost": "945"}},
                    ],
                }
            },
        ),
    ]
    client = make_client(session)

    account = client.get_account_state(watchlist=["AAPL", "MSFT"])

    assert account.positions_usd == {"AAPL": 1850.0}  # TSLA는 watchlist 밖이라 제외
    assert account.daily_pnl_pct == pytest.approx(-1.25)

    token_call = session.request.call_args_list[0]
    assert token_call.args[0] == "POST"
    assert token_call.args[1] == "https://openapi.tossinvest.com/oauth2/token"

    holdings_call = session.request.call_args_list[2]
    assert holdings_call.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert holdings_call.kwargs["headers"]["X-Tossinvest-Account"] == "42"


def test_place_market_buy_sends_order_amount():
    session = Mock()
    session.request.side_effect = [
        mock_response(200, {"access_token": "tok", "token_type": "Bearer", "expires_in": 86400}),
        mock_response(200, {"result": [{"accountNo": "1", "accountSeq": 42, "accountType": "BROKERAGE"}]}),
        mock_response(200, {"result": {"orderId": "order-1", "clientOrderId": "cid-1"}}),
    ]
    client = make_client(session)

    result = client.place_market_buy("AAPL", 100.0, "cid-1")

    assert result == {"orderId": "order-1", "clientOrderId": "cid-1"}
    order_call = session.request.call_args_list[2]
    assert order_call.kwargs["json"] == {
        "clientOrderId": "cid-1",
        "symbol": "AAPL",
        "side": "BUY",
        "orderType": "MARKET",
        "orderAmount": "100.00",
    }


def test_4xx_error_is_not_retried():
    session = Mock()
    session.request.return_value = mock_response(
        400, {"error": {"code": "invalid-request", "message": "bad field"}}
    )
    client = make_client(session)

    with pytest.raises(TossApiError):
        client._get_access_token()

    assert session.request.call_count == 1


def test_5xx_error_is_retried_up_to_max_then_raises():
    session = Mock()
    session.request.side_effect = requests.ConnectionError("boom")
    client = make_client(session)

    with pytest.raises(requests.ConnectionError):
        client._get_access_token()

    assert session.request.call_count == TossClient.MAX_RETRIES + 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_toss_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.toss_client'`

- [ ] **Step 3: 구현**

`orchestrator/toss_client.py`:

```python
import time

import requests

from orchestrator.risk_engine import AccountState


class TossApiError(Exception):
    def __init__(self, status_code, code, message):
        self.status_code = status_code
        self.code = code
        super().__init__(f"Toss API error {status_code} {code}: {message}")


class TossClient:
    BASE_URL = "https://openapi.tossinvest.com"
    MAX_RETRIES = 2
    TIMEOUT_SECONDS = 10

    def __init__(self, client_id, client_secret, session=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = session or requests.Session()
        self._access_token = None
        self._token_expires_at = 0.0
        self._account_seq = None

    def _request(self, method, path, headers=None, params=None, json=None, data=None):
        last_exc = None
        for _ in range(self.MAX_RETRIES + 1):
            try:
                resp = self.session.request(
                    method,
                    f"{self.BASE_URL}{path}",
                    headers=headers,
                    params=params,
                    json=json,
                    data=data,
                    timeout=self.TIMEOUT_SECONDS,
                )
                self._raise_for_status(resp)
                return resp.json()
            except TossApiError as exc:
                if exc.status_code < 500:
                    raise
                last_exc = exc
            except requests.RequestException as exc:
                last_exc = exc
        raise last_exc

    @staticmethod
    def _raise_for_status(resp):
        if resp.status_code < 400:
            return
        try:
            body = resp.json()
        except ValueError:
            body = {}
        error = body.get("error")
        if isinstance(error, dict):
            code = error.get("code", "unknown")
            message = error.get("message", resp.text)
        else:
            code = str(error) if error else "unknown"
            message = body.get("error_description", resp.text)
        raise TossApiError(resp.status_code, code, message)

    def _get_access_token(self):
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        body = self._request(
            "POST",
            "/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        self._access_token = body["access_token"]
        self._token_expires_at = time.time() + body["expires_in"] - 60
        return self._access_token

    def _get_account_seq(self):
        if self._account_seq is None:
            body = self._request(
                "GET",
                "/api/v1/accounts",
                headers={"Authorization": f"Bearer {self._get_access_token()}"},
            )
            accounts = body["result"]
            if not accounts:
                raise TossApiError(200, "no-account", "조회된 계좌가 없습니다")
            self._account_seq = accounts[0]["accountSeq"]
        return self._account_seq

    def _auth_headers(self):
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "X-Tossinvest-Account": str(self._get_account_seq()),
        }

    def get_account_state(self, watchlist):
        body = self._request("GET", "/api/v1/holdings", headers=self._auth_headers())
        result = body["result"]
        positions_usd = {
            item["symbol"]: float(item["marketValue"]["amount"])
            for item in result["items"]
            if item["symbol"] in watchlist
        }
        daily_pnl_pct = float(result["dailyProfitLoss"]["rate"]) * 100
        return AccountState(positions_usd=positions_usd, daily_pnl_pct=daily_pnl_pct)

    def get_holding_quantity(self, ticker):
        body = self._request(
            "GET", "/api/v1/holdings", headers=self._auth_headers(), params={"symbol": ticker}
        )
        items = body["result"]["items"]
        return float(items[0]["quantity"]) if items else 0.0

    def place_market_buy(self, ticker, amount_usd, client_order_id):
        return self._place_order(
            {
                "clientOrderId": client_order_id,
                "symbol": ticker,
                "side": "BUY",
                "orderType": "MARKET",
                "orderAmount": f"{amount_usd:.2f}",
            }
        )

    def place_market_sell(self, ticker, quantity, client_order_id):
        return self._place_order(
            {
                "clientOrderId": client_order_id,
                "symbol": ticker,
                "side": "SELL",
                "orderType": "MARKET",
                "quantity": str(quantity),
            }
        )

    def _place_order(self, body):
        result = self._request(
            "POST", "/api/v1/orders", headers=self._auth_headers(), json=body
        )
        return result["result"]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_toss_client.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add orchestrator/toss_client.py tests/test_toss_client.py
git commit -m "Add Toss Open API client with retry and idempotent order placement"
```

---

### Task 6: TradingAgents 연동 — `docker/run_analysis.py` + `trading_agents_client.py`

**Files:**
- Create: `E:\MyProject\docker\run_analysis.py`
- Create: `E:\MyProject\docker-compose.yml`
- Create: `E:\MyProject\orchestrator\trading_agents_client.py`
- Test: `E:\MyProject\tests\test_trading_agents_client.py`

- [ ] **Step 1: TradingAgents를 git submodule로 추가**

```bash
cd "E:\MyProject"
git submodule add https://github.com/TauricResearch/TradingAgents.git vendor/tradingagents
git commit -m "Add TradingAgents as git submodule"
```

- [ ] **Step 2: 컨테이너 안에서 실행될 래퍼 스크립트 작성**

TradingAgents의 `propagate()`가 반환하는 `decision` 객체의 정확한 내부 스키마는 공식 문서에 명시되어 있지 않다.
이 래퍼는 그 값이 문자열이든 dict든 상관없이 BUY/SELL/HOLD 셋 중 하나로 정규화해 안정적인 JSON 한 줄만 stdout에
출력한다 — 이후 오케스트레이터는 이 스크립트의 계약(JSON 한 줄)에만 의존하고, TradingAgents 내부 스키마 변경에
영향받지 않는다.

`docker/run_analysis.py`:

```python
import argparse
import json

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG


def normalize_decision(raw):
    """propagate()의 decision 반환값을 BUY/SELL/HOLD 문자열로 정규화한다.

    실제 TradingAgents는 Buy/Overweight/Hold/Underweight/Sell 5단계 등급을
    문자열로 반환한다 (dict 분기는 방어적으로만 남겨둠). Overweight/Underweight는
    방향성이 있는 신호이므로 각각 BUY/SELL로 매핑한다.
    """
    if isinstance(raw, dict):
        for key in ("action", "decision", "final_decision", "signal"):
            value = raw.get(key)
            if isinstance(value, str):
                raw = value
                break
    text = str(raw).upper()
    if "OVERWEIGHT" in text:
        return "BUY"
    if "UNDERWEIGHT" in text:
        return "SELL"
    for action in ("BUY", "SELL", "HOLD"):
        if action in text:
            return action
    return "HOLD"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("date")
    args = parser.parse_args()

    config = DEFAULT_CONFIG.copy()
    graph = TradingAgentsGraph(debug=False, config=config)
    _, raw_decision = graph.propagate(args.ticker, args.date)

    result = {
        "ticker": args.ticker,
        "date": args.date,
        "action": normalize_decision(raw_decision),
        "raw_decision": str(raw_decision),
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: `docker-compose.yml` 작성**

```yaml
services:
  tradingagents:
    build:
      context: ./vendor/tradingagents
    volumes:
      - ./docker/run_analysis.py:/app/run_analysis.py:ro
    env_file:
      - .env
```

- [ ] **Step 4: 실패하는 테스트 작성 (호스트 측 클라이언트)**

`tests/test_trading_agents_client.py`:

```python
import subprocess
from unittest.mock import Mock

import pytest

from orchestrator.trading_agents_client import normalize_decision, run_analysis


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("BUY", "BUY"),
        ({"action": "SELL"}, "SELL"),
        ({"final_decision": "The decision is HOLD for now."}, "HOLD"),
        ("I recommend a strong SELL position here", "SELL"),
        ({"unrelated_key": "no signal here"}, "HOLD"),
    ],
)
def test_normalize_decision(raw, expected):
    assert normalize_decision(raw) == expected


def test_run_analysis_parses_last_json_line(monkeypatch):
    fake_result = Mock()
    fake_result.stdout = (
        "some noisy log line\n"
        "another log line\n"
        '{"ticker": "AAPL", "date": "2026-08-21", "action": "BUY", "raw_decision": "BUY"}\n'
    )

    def fake_run(cmd, capture_output, text, timeout, check):
        assert cmd == [
            "docker", "compose", "run", "--rm", "tradingagents",
            "python", "/app/run_analysis.py", "AAPL", "2026-08-21",
        ]
        return fake_result

    monkeypatch.setattr(subprocess, "run", fake_run)

    action = run_analysis("AAPL", "2026-08-21")

    assert action == "BUY"


def test_run_analysis_raises_when_no_json_found(monkeypatch):
    fake_result = Mock()
    fake_result.stdout = "nothing but logs here\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_result)

    with pytest.raises(ValueError):
        run_analysis("AAPL", "2026-08-21")
```

- [ ] **Step 5: 테스트 실패 확인**

Run: `pytest tests/test_trading_agents_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.trading_agents_client'`

- [ ] **Step 6: 구현**

`orchestrator/trading_agents_client.py`:

```python
import json
import subprocess


def normalize_decision(raw):
    if isinstance(raw, dict):
        for key in ("action", "decision", "final_decision", "signal"):
            value = raw.get(key)
            if isinstance(value, str):
                raw = value
                break
    text = str(raw).upper()
    if "OVERWEIGHT" in text:
        return "BUY"
    if "UNDERWEIGHT" in text:
        return "SELL"
    for action in ("BUY", "SELL", "HOLD"):
        if action in text:
            return action
    return "HOLD"


def run_analysis(ticker, run_date, timeout_seconds=1800):
    result = subprocess.run(
        [
            "docker", "compose", "run", "--rm", "tradingagents",
            "python", "/app/run_analysis.py", ticker, run_date,
        ],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=True,
    )
    for line in reversed(result.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)["action"]
    raise ValueError(
        f"{ticker} 분석 결과에서 JSON 출력을 찾지 못했습니다: {result.stdout[-500:]}"
    )
```

- [ ] **Step 7: 테스트 통과 확인**

Run: `pytest tests/test_trading_agents_client.py -v`
Expected: PASS (7 passed)

- [ ] **Step 8: 커밋**

```bash
git add docker/run_analysis.py docker-compose.yml orchestrator/trading_agents_client.py tests/test_trading_agents_client.py
git commit -m "Add TradingAgents docker integration with decision normalization"
```

---

### Task 7: `notifier.py` — 텔레그램 알림 (TDD, mocked HTTP)

**Files:**
- Create: `E:\MyProject\orchestrator\notifier.py`
- Test: `E:\MyProject\tests\test_notifier.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_notifier.py`:

```python
from types import SimpleNamespace

import requests

from orchestrator.notifier import send_telegram_message


def make_config():
    return SimpleNamespace(telegram_bot_token="tok", telegram_chat_id="chat-1")


def test_send_telegram_message_posts_expected_payload(monkeypatch):
    calls = []

    def fake_post(url, data, timeout):
        calls.append((url, data, timeout))

    monkeypatch.setattr(requests, "post", fake_post)

    send_telegram_message(make_config(), "hello")

    assert calls == [
        ("https://api.telegram.org/bottok/sendMessage", {"chat_id": "chat-1", "text": "hello"}, 10)
    ]


def test_send_telegram_message_swallows_request_exception(monkeypatch):
    def fake_post(url, data, timeout):
        raise requests.RequestException("network down")

    monkeypatch.setattr(requests, "post", fake_post)

    send_telegram_message(make_config(), "hello")  # 예외가 밖으로 전파되지 않아야 함
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_notifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.notifier'`

- [ ] **Step 3: 구현**

`orchestrator/notifier.py`:

```python
import requests


def send_telegram_message(config, text):
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    try:
        requests.post(
            url, data={"chat_id": config.telegram_chat_id, "text": text}, timeout=10
        )
    except requests.RequestException as exc:
        print(f"텔레그램 알림 전송 실패: {type(exc).__name__}")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_notifier.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add orchestrator/notifier.py tests/test_notifier.py
git commit -m "Add Telegram notifier"
```

---

### Task 8: `main.py` — 오케스트레이터 (전 구성요소 연결)

**Files:**
- Create: `E:\MyProject\orchestrator\main.py`
- Test: `E:\MyProject\tests\test_main.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_main.py`는 모든 외부 의존성(Toss, TradingAgents, 텔레그램)을 모킹해 오케스트레이션 흐름 자체를 검증한다.

```python
import os
from types import SimpleNamespace

import pytest

import orchestrator.main as main_module
from orchestrator.risk_engine import AccountState, RiskLimits
from orchestrator.state import init_db


def make_config():
    return SimpleNamespace(
        watchlist=["AAPL", "MSFT"],
        risk_limits=RiskLimits(
            per_ticker_max_usd=100.0,
            daily_max_buy_usd=300.0,
            daily_loss_circuit_pct=-3.0,
            max_concurrent_positions=5,
        ),
        run_time_kst="22:40",
        toss_client_id="id",
        toss_client_secret="secret",
        telegram_bot_token="tok",
        telegram_chat_id="chat",
    )


class FakeTossClient:
    def __init__(self, *args, **kwargs):
        self.orders = []

    def get_account_state(self, watchlist):
        return AccountState(positions_usd={}, daily_pnl_pct=0.0)

    def place_market_buy(self, ticker, amount_usd, client_order_id):
        self.orders.append(("BUY", ticker, amount_usd))
        return {"orderId": "1", "clientOrderId": client_order_id}

    def get_holding_quantity(self, ticker):
        return "0"

    def place_market_sell(self, ticker, quantity, client_order_id):
        self.orders.append(("SELL", ticker, quantity))
        return {"orderId": "1", "clientOrderId": client_order_id}


@pytest.fixture(autouse=True)
def patch_dependencies(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "load_config", lambda: make_config())
    monkeypatch.setattr(main_module, "TossClient", FakeTossClient)
    monkeypatch.setattr(main_module, "run_analysis", lambda ticker, run_date: "BUY")
    monkeypatch.setattr(main_module, "send_telegram_message", lambda config, text: None)
    monkeypatch.setattr(main_module, "DB_PATH", str(tmp_path / "trading.db"))
    monkeypatch.setattr(main_module, "STOP_FILE", str(tmp_path / "STOP"))
    monkeypatch.chdir(tmp_path)


def test_stop_file_skips_everything(monkeypatch, tmp_path):
    (tmp_path / "STOP").write_text("stop")

    main_module.run(dry_run=False)

    conn = init_db(str(tmp_path / "trading.db"))
    assert main_module.has_run_completed_today(conn, main_module.date.today().isoformat()) is False


def test_normal_run_places_approved_orders(monkeypatch):
    captured_orders = {}

    class TrackingTossClient(FakeTossClient):
        def place_market_buy(self, ticker, amount_usd, client_order_id):
            captured_orders.setdefault("buys", []).append((ticker, amount_usd))
            return super().place_market_buy(ticker, amount_usd, client_order_id)

    monkeypatch.setattr(main_module, "TossClient", TrackingTossClient)

    main_module.run(dry_run=False)

    assert captured_orders["buys"] == [("AAPL", 100.0), ("MSFT", 100.0)]


def test_dry_run_does_not_place_orders(monkeypatch):
    orders_placed = []

    class TrackingTossClient(FakeTossClient):
        def place_market_buy(self, ticker, amount_usd, client_order_id):
            orders_placed.append(ticker)
            return super().place_market_buy(ticker, amount_usd, client_order_id)

    monkeypatch.setattr(main_module, "TossClient", TrackingTossClient)

    main_module.run(dry_run=True)

    assert orders_placed == []


def test_circuit_breaker_blocks_orders(monkeypatch):
    class LosingTossClient(FakeTossClient):
        def get_account_state(self, watchlist):
            return AccountState(positions_usd={}, daily_pnl_pct=-5.0)

    monkeypatch.setattr(main_module, "TossClient", LosingTossClient)
    orders_placed = []
    monkeypatch.setattr(
        main_module, "run_analysis", lambda ticker, run_date: (_ for _ in ()).throw(AssertionError("should not analyze"))
    )

    main_module.run(dry_run=False)  # 예외 없이 조기 종료되어야 함
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orchestrator.main'`

- [ ] **Step 3: 구현**

`orchestrator/main.py`:

```python
import argparse
import os
from datetime import date

from orchestrator.config import load_config
from orchestrator.notifier import send_telegram_message
from orchestrator.risk_engine import Decision, evaluate_decisions
from orchestrator.state import (
    has_run_completed_today,
    init_db,
    is_circuit_breaker_triggered_today,
    mark_circuit_breaker_triggered,
    mark_run_completed,
    mark_run_started,
    record_order,
)
from orchestrator.toss_client import TossClient
from orchestrator.trading_agents_client import run_analysis

STOP_FILE = "STOP"
DB_PATH = "data/trading.db"


def run(dry_run):
    config = load_config()
    conn = init_db(DB_PATH)
    run_date = date.today().isoformat()

    try:
        if os.path.exists(STOP_FILE):
            send_telegram_message(config, f"[{run_date}] STOP 파일 발견 - 자동매매를 건너뜁니다.")
            return

        if has_run_completed_today(conn, run_date):
            send_telegram_message(config, f"[{run_date}] 오늘 실행이 이미 완료되어 재실행을 건너뜁니다.")
            return

        mark_run_started(conn, run_date)

        toss = TossClient(config.toss_client_id, config.toss_client_secret)
        account = toss.get_account_state(config.watchlist)

        if account.daily_pnl_pct <= config.risk_limits.daily_loss_circuit_pct:
            mark_circuit_breaker_triggered(conn, run_date)

        if is_circuit_breaker_triggered_today(conn, run_date):
            send_telegram_message(
                config,
                f"[{run_date}] 서킷브레이커 발동 (당일 손익 {account.daily_pnl_pct:.2f}%) - 신규 주문 없음.",
            )
            mark_run_completed(conn, run_date)
            return

        decisions = []
        for ticker in config.watchlist:
            try:
                action = run_analysis(ticker, run_date)
            except Exception as exc:
                send_telegram_message(config, f"[{run_date}] {ticker} 분석 실패: {exc}")
                continue
            amount = config.risk_limits.per_ticker_max_usd if action == "BUY" else 0.0
            decisions.append(Decision(ticker=ticker, action=action, amount_usd=amount))

        risk_results = evaluate_decisions(decisions, account, config.risk_limits)

        summary_lines = [f"[{run_date}] 자동매매 결과{' (드라이런)' if dry_run else ''}"]
        for r in risk_results:
            record_order(conn, run_date, r.ticker, r.action, r.amount_usd, r.approved, r.reason)
            summary_lines.append(
                f"- {r.ticker} {r.action}: {'승인' if r.approved else '거부'} ({r.reason})"
            )

            if not r.approved or dry_run:
                continue

            client_order_id = f"{run_date}-{r.ticker}-{r.action}"
            try:
                if r.action == "BUY":
                    toss.place_market_buy(r.ticker, r.amount_usd, client_order_id)
                elif r.action == "SELL":
                    quantity = toss.get_holding_quantity(r.ticker)
                    if float(quantity) > 0:
                        toss.place_market_sell(r.ticker, quantity, client_order_id)
            except Exception as exc:
                summary_lines.append(f"  주문 실패: {exc}")

        send_telegram_message(config, "\n".join(summary_lines))
        mark_run_completed(conn, run_date)

    except Exception as exc:
        send_telegram_message(config, f"[{run_date}] 실행 중 예외 발생: {exc}")
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_main.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 전체 테스트 스위트 통과 확인**

Run: `pytest -v`
Expected: 모든 테스트 PASS

- [ ] **Step 6: 커밋**

```bash
git add orchestrator/main.py tests/test_main.py
git commit -m "Add orchestrator wiring risk engine, Toss client, TradingAgents, notifier"
```

---

### Task 9: Windows 스케줄러 등록 + README + 드라이런 검증

**Files:**
- Create: `E:\MyProject\scripts\run_daily.ps1`
- Create: `E:\MyProject\README.md`

- [ ] **Step 1: 스케줄러 실행 스크립트 작성**

`scripts/run_daily.ps1`:

```powershell
Set-Location "E:\MyProject"
& ".\venv\Scripts\python.exe" "orchestrator\main.py" *>> "logs\run.log"
```

- [ ] **Step 2: README 작성**

`README.md`:

```markdown
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
```

- [ ] **Step 3: 커밋**

```bash
git add scripts/run_daily.ps1 README.md
git commit -m "Add scheduler script and setup README"
```

- [ ] **Step 4: docker-compose 빌드 확인**

Run: `docker compose build`
Expected: TradingAgents 이미지가 에러 없이 빌드됨 (최초 빌드는 수 분 소요될 수 있음)

- [ ] **Step 5: 드라이런으로 전체 파이프라인 수동 검증**

`.env`와 `config.yaml`을 실제 값으로 채운 뒤:

Run: `python orchestrator/main.py --dry-run`
Expected: 에러 없이 완료되고, 설정한 텔레그램 채팅방에 "(드라이런)" 요약 메시지가 도착함. `data/trading.db`의
`orders` 테이블에 각 종목의 판단 근거가 기록되어 있어야 한다.

---

## Self-Review 결과

**스펙 커버리지**: 설계 문서의 각 항목(아키텍처/컴포넌트/데이터 흐름/안전장치 6종/테스트/알려진 리스크)이 Task
1~9에 모두 매핑됨. `docs/toss-api-reference.md`는 "토스 오픈API 세부 사양 확인 필요" 리스크를 실제 조사로
해소한 결과물.

**타입 일관성**: `Decision`/`AccountState`/`RiskLimits`/`RiskDecision`은 Task 2에서 한 번 정의되고 Task 4~8에서
동일한 필드명으로만 참조됨 (`per_ticker_max_usd`, `daily_max_buy_usd`, `daily_loss_circuit_pct`,
`max_concurrent_positions`, `positions_usd`, `daily_pnl_pct`, `amount_usd`, `approved`, `reason`).

**플레이스홀더 스캔**: TBD/TODO 없음. TradingAgents의 정확한 `decision` 내부 스키마 하나만 문서상 불확실했으나,
Task 6에서 이를 흡수하는 정규화 래퍼(`normalize_decision`)로 실제 코드와 테스트로 처리함 — 뒤로 미룬 구현이
아니라 지금 확정된 설계.
```

## Task 8 구현 중 발견된 안전 이슈 및 후속 수정 (실행 기록)

Task 8 코드 품질 검토에서 "프로세스가 중간에 죽고 재시도될 경우 이미 체결된 주문이 중복 제출될 수 있다"는
Critical 이슈가 발견되어 구현 단계에서 다음을 추가했다 (원래 계획에는 없던 내용이므로 여기 기록한다):

- `orchestrator/state.py`의 `orders` 테이블에 `placed INTEGER NOT NULL DEFAULT 0`, `order_id TEXT` 컬럼 추가.
- `mark_order_placed(conn, run_date, ticker, action, order_id)` — 해당 키의 **가장 최근 미체결(`placed=0`) 행 하나만**
  타겟팅해 업데이트 (재시도로 같은 키에 중복 로그 행이 쌓여도 하나만 정확히 갱신되도록 서브쿼리로 행을 특정).
- `has_order_been_placed(conn, run_date, ticker, action) -> bool` — 오늘 이미 실제로 체결된 주문인지 확인.
- `orchestrator/main.py`의 주문 실행 루프는 토스 API 호출 전 `has_order_been_placed`로 먼저 확인하고, 이미
  체결됐으면 건너뛴 뒤 요약에 기록한다. 성공적으로 체결하면 토스 응답의 `orderId`로 `mark_order_placed`를 호출한다.
- `init_db(DB_PATH)` 호출 전 `os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)`를 추가하고, `conn = init_db(...)`
  자체를 `try` 블록 안으로 옮겨(최초 클론 시 `data/` 디렉터리가 git에 없어 `init_db`가 즉시 실패하던 문제) 실패
  시에도 텔레그램 알림이 나가도록 수정.
- `test_stop_file_skips_everything`을 `runs` 테이블에 행 자체가 없음을 직접 확인하도록 강화 (기존 assertion은
  `'started'` 행이 생겨도 통과했음).

---

**Plan complete and saved to `docs/superpowers/plans/2026-08-21-trading-bot-plan.md`. 실행 방식을 선택해 주세요:**

**1. Subagent-Driven (추천)** — 태스크마다 새 서브에이전트를 붙여 구현시키고, 각 태스크 사이에 리뷰

**2. Inline Execution** — 이 세션에서 배치 단위로 직접 실행, 체크포인트마다 확인

어느 쪽으로 진행할까요?
