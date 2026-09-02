# 레짐 스위치 (인버스 ETF) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 롱온리 자동매매 봇에, 규칙 기반 시장 레짐 감지기가 risk-off로 판정하면 보유 롱을 전량 매도하고 1x 인버스 ETF(PSQ)를 매수하는 방어/수익 기능을 추가한다.

**Architecture:** `orchestrator/main.py`의 `run()` 시퀀스에 8.5단계(계좌 조회 이후, 서킷브레이커 early return 이전)로 삽입. 레짐 판정은 순수 규칙(yfinance로 받은 QQQ 200/50일 SMA + VIX, 2일 확인봉 + 히스테리시스), LLM 미사용. 스위치 상태는 SQLite에 영속. `regime_switch.enabled: false`(기본값)이면 8.5단계는 통째로 건너뛰고 기존 봇과 100% 동일 동작.

**Tech Stack:** Python, yfinance (신규 프로덕션 의존성), sqlite3, pytest + monkeypatch. 기존 패턴: dataclass 설정, Fake 클라이언트 테스트, `init_db(":memory:")` fixture.

**전체 설계:** `docs/superpowers/specs/2026-09-03-regime-switch-design.md`

---

## File Structure

**신규 파일:**
| 파일 | 책임 |
|---|---|
| `orchestrator/regime.py` | 레짐 데이터 조회(`fetch_regime_data`), 원시 신호(`compute_raw_signals`), 판정(`evaluate_regime`), 스위치 실행(`execute_switch_to_off` / `execute_switch_to_on`) |
| `scripts/backtest_regime_switch.py` | 오프라인 백테스트 (프로덕션 실행에 불필요) |
| `tests/test_regime.py` | `regime.py`의 판정 로직 테스트 |
| `tests/test_regime_switch.py` | 스위치 실행 + main.py 통합 테스트 |

**수정 파일:**
| 파일 | 변경 |
|---|---|
| `orchestrator/state.py` | `regime_signals` / `regime_state` 테이블 + 함수 5개 |
| `orchestrator/config.py` | `RegimeSwitchConfig` dataclass + 파싱 |
| `orchestrator/toss_client.py` | `get_purchasing_power_usd()` |
| `orchestrator/main.py` | 8.5단계 삽입 (`_handle_regime`) |
| `config.yaml` | `regime_switch` 블록 (`enabled: false`) |
| `requirements.txt` | `yfinance==1.6.0` 추가 |
| `tests/test_state.py` | regime 테이블 테스트 (기존 파일에 추가) |
| `tests/test_config.py` | `regime_switch` 파싱 테스트 (기존 파일에 추가) |
| `README.md` | 레짐 스위치 섹션 |

---

## Task 1: 설정 — `RegimeSwitchConfig` + config.yaml + requirements

**Files:**
- Modify: `orchestrator/config.py`
- Modify: `config.yaml`
- Modify: `requirements.txt`
- Test: `tests/test_config.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_config.py` 끝에 추가:

```python
def test_regime_switch_defaults_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("TOSS_CLIENT_ID", "x")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "x")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "watchlist: [AAPL]\n"
        "risk_limits: {per_ticker_max_usd: 10, per_ticker_max_position_usd: 50, "
        "daily_max_buy_usd: 300, daily_loss_circuit_pct: -3.0, max_concurrent_positions: 5}\n"
        "schedule: {run_time_kst: '22:40'}\n",
        encoding="utf-8",
    )
    from orchestrator.config import load_config
    config = load_config(config_path=str(cfg_file), env_path=str(tmp_path / ".env"))
    assert config.regime_switch.enabled is False
    assert config.regime_switch.proxy_ticker == "QQQ"
    assert config.regime_switch.inverse_ticker == "PSQ"
    assert config.regime_switch.confirm_days == 2
    assert config.regime_switch.max_deploy_pct == 90.0


def test_regime_switch_parsed_from_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("TOSS_CLIENT_ID", "x")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "x")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "watchlist: [AAPL]\n"
        "risk_limits: {per_ticker_max_usd: 10, per_ticker_max_position_usd: 50, "
        "daily_max_buy_usd: 300, daily_loss_circuit_pct: -3.0, max_concurrent_positions: 5}\n"
        "schedule: {run_time_kst: '22:40'}\n"
        "regime_switch: {enabled: true, vix_threshold: 30.0, confirm_days: 3}\n",
        encoding="utf-8",
    )
    from orchestrator.config import load_config
    config = load_config(config_path=str(cfg_file), env_path=str(tmp_path / ".env"))
    assert config.regime_switch.enabled is True
    assert config.regime_switch.vix_threshold == 30.0
    assert config.regime_switch.confirm_days == 3
    assert config.regime_switch.inverse_ticker == "PSQ"  # 미지정 필드는 기본값
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_config.py -k regime_switch -v`
Expected: FAIL — `AttributeError: 'AppConfig' object has no attribute 'regime_switch'`

- [ ] **Step 3: 구현**

`orchestrator/config.py` 상단 dataclass 영역에 추가:

```python
@dataclass
class RegimeSwitchConfig:
    enabled: bool = False
    proxy_ticker: str = "QQQ"
    inverse_ticker: str = "PSQ"
    sma_long: int = 200
    sma_short: int = 50
    vix_threshold: float = 25.0
    confirm_days: int = 2
    max_deploy_pct: float = 90.0
```

`AppConfig`에 필드 추가 (마지막 필드 뒤):

```python
    approval_timeout_minutes: int
    regime_switch: RegimeSwitchConfig
```

`load_config` 안, `approval = raw.get("approval", {})` 다음 줄에 추가:

```python
    regime_switch_raw = raw.get("regime_switch", {})
    known = {f.name for f in fields(RegimeSwitchConfig)}
    regime_switch = RegimeSwitchConfig(
        **{k: v for k, v in regime_switch_raw.items() if k in known}
    )
```

`return AppConfig(...)` 에 인자 추가 (마지막 줄):

```python
        approval_timeout_minutes=approval.get("timeout_minutes", 20),
        regime_switch=regime_switch,
    )
```

`config.py` import 줄 수정:

```python
from dataclasses import dataclass, fields
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (기존 테스트 포함 전부)

- [ ] **Step 5: config.yaml + requirements.txt 수정**

`config.yaml` 끝에 추가:

```yaml

regime_switch:
  enabled: false          # 기본 false → 기존 봇과 동일 동작
  proxy_ticker: QQQ
  inverse_ticker: PSQ
  sma_long: 200
  sma_short: 50
  vix_threshold: 25.0
  confirm_days: 2
  max_deploy_pct: 90      # min(매수가능금액, 매도한 종목들의 직전 시장가치 합)의 90%까지 인버스 매수
```

`requirements.txt` 에 한 줄 추가 (pytest 줄 앞):

```
yfinance==1.6.0
```

- [ ] **Step 6: yfinance 설치**

Run: `.\venv\Scripts\python.exe -m pip install yfinance==1.6.0`
Expected: 성공 (pandas/numpy 등 딸려옴 — 의도된 것, 스펙에 명시)

- [ ] **Step 7: 커밋**

```bash
git add orchestrator/config.py config.yaml requirements.txt tests/test_config.py
git commit -m "Add RegimeSwitchConfig and regime_switch config block"
```

---

## Task 2: 상태 레이어 — regime 테이블 + 함수

**Files:**
- Modify: `orchestrator/state.py`
- Test: `tests/test_state.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_state.py` import 블록에 추가:

```python
from orchestrator.state import (
    count_consecutive_signal,
    get_current_regime,
    record_regime_signal,
    set_pending_inverse_buy,
    set_regime,
)
```

`tests/test_state.py` 끝에 추가:

```python
def test_current_regime_defaults_to_risk_on(conn):
    assert get_current_regime(conn) == ("risk_on", 0)


def test_set_regime_then_read_back(conn):
    set_regime(conn, "risk_off", "2026-09-03")
    assert get_current_regime(conn) == ("risk_off", 0)


def test_set_regime_unchanged_keeps_since_date(conn):
    set_regime(conn, "risk_off", "2026-09-03")
    set_regime(conn, "risk_off", "2026-09-10")  # 같은 레짐 재설정
    row = conn.execute("SELECT regime, since_date FROM regime_state WHERE id = 1").fetchone()
    assert row == ("risk_off", "2026-09-03")  # since_date 는 첫 전이일 유지


def test_pending_inverse_buy_flag(conn):
    set_regime(conn, "risk_off", "2026-09-03")
    set_pending_inverse_buy(conn, 1)
    assert get_current_regime(conn) == ("risk_off", 1)
    set_pending_inverse_buy(conn, 0)
    assert get_current_regime(conn) == ("risk_off", 0)


def test_record_regime_signal_is_idempotent_per_date(conn):
    record_regime_signal(conn, "2026-09-03", 1, 0, 21000.0, 21500.0, 21200.0, 28.0)
    record_regime_signal(conn, "2026-09-03", 1, 0, 21001.0, 21500.0, 21200.0, 28.5)
    rows = conn.execute("SELECT COUNT(*) FROM regime_signals").fetchone()
    assert rows[0] == 1


def test_count_consecutive_signal(conn):
    # 가장 최근 3일 연속 risk_off, 그 전 1일은 아님
    record_regime_signal(conn, "2026-09-01", 0, 1, 1, 1, 1, 15.0)
    record_regime_signal(conn, "2026-09-02", 1, 0, 1, 1, 1, 26.0)
    record_regime_signal(conn, "2026-09-03", 1, 0, 1, 1, 1, 27.0)
    record_regime_signal(conn, "2026-09-04", 1, 0, 1, 1, 1, 28.0)
    assert count_consecutive_signal(conn, "2026-09-04", "risk_off") == 3
    assert count_consecutive_signal(conn, "2026-09-04", "risk_on") == 0
    assert count_consecutive_signal(conn, "2026-09-01", "risk_on") == 1


def test_count_consecutive_signal_respects_as_of_date(conn):
    record_regime_signal(conn, "2026-09-02", 1, 0, 1, 1, 1, 26.0)
    record_regime_signal(conn, "2026-09-03", 1, 0, 1, 1, 1, 27.0)
    # 09-02 시점에서 보면 연속 1일
    assert count_consecutive_signal(conn, "2026-09-02", "risk_off") == 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_state.py -k regime -v`
Expected: FAIL — `ImportError: cannot import name 'get_current_regime'`

- [ ] **Step 3: 구현**

`orchestrator/state.py` 상단 import 수정:

```python
import sqlite3
from datetime import date, datetime
```

`init_db()` 의 `conn.commit()` 앞에 테이블 2개 추가:

```python
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS regime_signals (
            signal_date TEXT PRIMARY KEY,
            raw_risk_off INTEGER NOT NULL,
            raw_risk_on INTEGER NOT NULL,
            qqq_close REAL,
            sma200 REAL,
            sma50 REAL,
            vix REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS regime_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            regime TEXT NOT NULL,
            since_date TEXT NOT NULL,
            pending_inverse_buy INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
```

`state.py` 끝에 함수 추가:

```python
def _ensure_regime_row(conn):
    conn.execute(
        "INSERT OR IGNORE INTO regime_state (id, regime, since_date, pending_inverse_buy, updated_at) "
        "VALUES (1, 'risk_on', ?, 0, ?)",
        (date.today().isoformat(), datetime.now().isoformat()),
    )


def get_current_regime(conn):
    """(regime, pending_inverse_buy) 반환. 행이 없으면 ('risk_on', 0)."""
    row = conn.execute(
        "SELECT regime, pending_inverse_buy FROM regime_state WHERE id = 1"
    ).fetchone()
    if row is None:
        return ("risk_on", 0)
    return (row[0], row[1])


def set_regime(conn, regime, since_date):
    """레짐을 갱신한다. 레짐이 실제로 바뀔 때만 since_date 를 갱신하고,
    같은 레짐 재설정이면 updated_at 만 갱신한다 (pending 플래그는 건드리지 않음)."""
    _ensure_regime_row(conn)
    current = conn.execute("SELECT regime FROM regime_state WHERE id = 1").fetchone()[0]
    now = datetime.now().isoformat()
    if current != regime:
        conn.execute(
            "UPDATE regime_state SET regime = ?, since_date = ?, updated_at = ? WHERE id = 1",
            (regime, since_date, now),
        )
    else:
        conn.execute("UPDATE regime_state SET updated_at = ? WHERE id = 1", (now,))
    conn.commit()


def set_pending_inverse_buy(conn, value):
    _ensure_regime_row(conn)
    conn.execute(
        "UPDATE regime_state SET pending_inverse_buy = ?, updated_at = ? WHERE id = 1",
        (int(value), datetime.now().isoformat()),
    )
    conn.commit()


def record_regime_signal(conn, signal_date, raw_risk_off, raw_risk_on, qqq_close, sma200, sma50, vix):
    conn.execute(
        "INSERT OR REPLACE INTO regime_signals "
        "(signal_date, raw_risk_off, raw_risk_on, qqq_close, sma200, sma50, vix) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (signal_date, int(raw_risk_off), int(raw_risk_on), qqq_close, sma200, sma50, vix),
    )
    conn.commit()


def count_consecutive_signal(conn, as_of_date, kind):
    """as_of_date(포함) 이전 날짜들에서 raw_{kind} 가 연속 1인 일수."""
    col = "raw_risk_off" if kind == "risk_off" else "raw_risk_on"
    rows = conn.execute(
        f"SELECT {col} FROM regime_signals WHERE signal_date <= ? ORDER BY signal_date DESC",
        (as_of_date,),
    ).fetchall()
    count = 0
    for (value,) in rows:
        if value == 1:
            count += 1
        else:
            break
    return count
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_state.py -v`
Expected: PASS (기존 테스트 포함 전부)

- [ ] **Step 5: 커밋**

```bash
git add orchestrator/state.py tests/test_state.py
git commit -m "Add regime_signals/regime_state tables and state functions"
```

---

## Task 3: 시세 데이터 조회 — `fetch_regime_data`

**Files:**
- Create: `orchestrator/regime.py`
- Test: `tests/test_regime.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime.py` 생성:

```python
import pandas as pd
import pytest

import orchestrator.regime as regime


def _fake_history(closes):
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=idx)


class FakeTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, period=None):
        if self.symbol == "^VIX":
            return _fake_history([18.0, 19.0, 20.5])
        # QQQ: 250일치 완만한 상승 후 마지막 급락
        closes = [100.0 + i * 0.1 for i in range(250)] + [90.0]
        return _fake_history(closes)


def test_fetch_regime_data_computes_smas(monkeypatch):
    monkeypatch.setattr(regime.yf, "Ticker", FakeTicker)
    data = regime.fetch_regime_data("QQQ", sma_long=200, sma_short=50)
    assert data is not None
    assert data.qqq_close == 90.0
    assert data.sma200 > 0
    assert data.sma50 > 0
    assert data.vix == 20.5


def test_fetch_regime_data_none_when_insufficient_history(monkeypatch):
    class ShortTicker(FakeTicker):
        def history(self, period=None):
            return _fake_history([100.0] * 50)

    monkeypatch.setattr(regime.yf, "Ticker", ShortTicker)
    assert regime.fetch_regime_data("QQQ", sma_long=200, sma_short=50) is None


def test_fetch_regime_data_none_on_exception(monkeypatch):
    class BrokenTicker:
        def __init__(self, symbol):
            pass

        def history(self, period=None):
            raise RuntimeError("network down")

    monkeypatch.setattr(regime.yf, "Ticker", BrokenTicker)
    monkeypatch.setattr(regime.time, "sleep", lambda s: None)
    assert regime.fetch_regime_data("QQQ") is None


def test_fetch_regime_data_vix_none_when_vix_fails(monkeypatch):
    class VixBrokenTicker(FakeTicker):
        def history(self, period=None):
            if self.symbol == "^VIX":
                raise RuntimeError("vix down")
            return super().history(period)

    monkeypatch.setattr(regime.yf, "Ticker", VixBrokenTicker)
    data = regime.fetch_regime_data("QQQ")
    assert data is not None
    assert data.vix is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_regime.py -k fetch -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.regime'`

- [ ] **Step 3: 구현**

`orchestrator/regime.py` 생성:

```python
"""시장 레짐 감지 + 인버스 ETF 스위치.

LLM 미사용. yfinance 로 받은 QQQ 200/50일 SMA + VIX 를 규칙으로 평가해
risk_on / risk_off 를 판정하고, 전이 시 롱 전량 매도 ↔ 인버스 ETF 매수를 실행한다.
"""

import time
from dataclasses import dataclass

import yfinance as yf

MIN_ORDER_USD = 1.0  # 이 금액 미만이면 인버스 매수를 시도하지 않는다 (토스 최소 주문금액은 구현 시 확인)
_FETCH_ATTEMPTS = 3
_FETCH_BACKOFF_SECONDS = 2


@dataclass
class RegimeData:
    qqq_close: float
    sma200: float
    sma50: float
    vix: float | None  # VIX 조회 실패 시 None


def fetch_regime_data(proxy_ticker="QQQ", sma_long=200, sma_short=50):
    """지수 프록시 일봉으로 SMA 를 계산하고 VIX 최근값을 붙여 RegimeData 를 반환한다.

    조회 실패(예외 3회) 또는 데이터가 sma_long 일 미만이면 None 을 반환한다 (호출부는
    이전 레짐을 유지하는 fail-safe 로 처리).
    """
    for attempt in range(_FETCH_ATTEMPTS):
        try:
            hist = yf.Ticker(proxy_ticker).history(period="400d")
            if len(hist) < sma_long:
                return None
            close = hist["Close"]
            sma200 = float(close.rolling(sma_long).mean().iloc[-1])
            sma50 = float(close.rolling(sma_short).mean().iloc[-1])
            qqq_close = float(close.iloc[-1])

            vix = None
            try:
                vix_hist = yf.Ticker("^VIX").history(period="5d")
                if len(vix_hist) > 0:
                    vix = float(vix_hist["Close"].iloc[-1])
            except Exception:
                vix = None

            return RegimeData(qqq_close=qqq_close, sma200=sma200, sma50=sma50, vix=vix)
        except Exception:
            if attempt < _FETCH_ATTEMPTS - 1:
                time.sleep(_FETCH_BACKOFF_SECONDS)
    return None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_regime.py -k fetch -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add orchestrator/regime.py tests/test_regime.py
git commit -m "Add fetch_regime_data (yfinance SMA + VIX)"
```

---

## Task 4: 판정 로직 — `compute_raw_signals` + `evaluate_regime`

**Files:**
- Modify: `orchestrator/regime.py`
- Test: `tests/test_regime.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime.py` 끝에 추가:

```python
def _data(close, sma200, sma50, vix):
    return regime.RegimeData(qqq_close=close, sma200=sma200, sma50=sma50, vix=vix)


def test_raw_signals_risk_off_below_200_and_death_cross():
    off, on = regime.compute_raw_signals(_data(90, 100, 95, 15.0), vix_threshold=25.0)
    assert off is True and on is False


def test_raw_signals_risk_off_below_200_and_high_vix():
    # 데드크로스 아님(sma50 > sma200)이지만 VIX 높음
    off, on = regime.compute_raw_signals(_data(90, 100, 105, 30.0), vix_threshold=25.0)
    assert off is True


def test_raw_signals_not_risk_off_below_200_but_calm():
    # 200일선 아래지만 데드크로스도 아니고 VIX 도 낮음 → 중립
    off, on = regime.compute_raw_signals(_data(99, 100, 105, 15.0), vix_threshold=25.0)
    assert off is False and on is False


def test_raw_signals_risk_on_above_200():
    off, on = regime.compute_raw_signals(_data(110, 100, 105, 15.0), vix_threshold=25.0)
    assert off is False and on is True


def test_raw_signals_vix_none_ignored():
    off, on = regime.compute_raw_signals(_data(90, 100, 105, None), vix_threshold=25.0)
    assert off is False  # 데드크로스 아님 + VIX 없음


def test_evaluate_regime_transition_on_to_off_requires_confirm():
    # risk_on 상태, off 스트릭 1일 → 아직 전이 안 함
    assert regime.evaluate_regime("risk_on", off_streak=1, on_streak=0, confirm_days=2) == ("risk_on", False)
    # off 스트릭 2일 → 전이
    assert regime.evaluate_regime("risk_on", off_streak=2, on_streak=0, confirm_days=2) == ("risk_off", True)


def test_evaluate_regime_transition_off_to_on_requires_confirm():
    assert regime.evaluate_regime("risk_off", off_streak=0, on_streak=1, confirm_days=2) == ("risk_off", False)
    assert regime.evaluate_regime("risk_off", off_streak=0, on_streak=2, confirm_days=2) == ("risk_on", True)


def test_evaluate_regime_hysteresis_neutral_holds_current():
    # 중립(둘 다 스트릭 0)이면 현재 레짐 유지
    assert regime.evaluate_regime("risk_off", off_streak=0, on_streak=0, confirm_days=2) == ("risk_off", False)
    assert regime.evaluate_regime("risk_on", off_streak=0, on_streak=0, confirm_days=2) == ("risk_on", False)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_regime.py -k "raw_signals or evaluate_regime" -v`
Expected: FAIL — `AttributeError: module 'orchestrator.regime' has no attribute 'compute_raw_signals'`

- [ ] **Step 3: 구현**

`orchestrator/regime.py` 의 `fetch_regime_data` 뒤에 추가:

```python
def compute_raw_signals(data, vix_threshold):
    """오늘 하루치 원시 신호 (raw_risk_off, raw_risk_on) 를 반환한다.

    둘은 상호 배타가 아니다 — 200일선 바로 아래이면서 데드크로스도 아니고
    VIX 도 낮은 '중립' 구간에서는 둘 다 False.
    """
    below_200 = data.qqq_close < data.sma200
    death_cross = data.sma50 < data.sma200
    vix_high = data.vix is not None and data.vix > vix_threshold
    raw_risk_off = below_200 and (death_cross or vix_high)
    raw_risk_on = data.qqq_close >= data.sma200
    return raw_risk_off, raw_risk_on


def evaluate_regime(prev_regime, off_streak, on_streak, confirm_days):
    """확인봉 + 히스테리시스로 새 레짐을 확정한다.

    진입(risk_off)과 청산(risk_on)이 서로 다른 조건을 쓴다 (히스테리시스):
    청산 조건이 진입보다 느슨해 200일선 근처 휩쏘에서 빠르게 롱 복귀한다.

    반환: (new_regime, transitioned)
    """
    off_confirmed = off_streak >= confirm_days
    on_confirmed = on_streak >= confirm_days
    if prev_regime == "risk_on":
        new_regime = "risk_off" if off_confirmed else "risk_on"
    else:  # risk_off
        new_regime = "risk_on" if on_confirmed else "risk_off"
    return new_regime, (new_regime != prev_regime)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_regime.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add orchestrator/regime.py tests/test_regime.py
git commit -m "Add compute_raw_signals and evaluate_regime (confirm + hysteresis)"
```

---

## Task 5: 토스 클라이언트 — `get_purchasing_power_usd`

**Files:**
- Modify: `orchestrator/toss_client.py`
- Test: `tests/test_toss_client.py`

- [ ] **Step 0: 응답 필드명 확인 (필수)**

`https://openapi.tossinvest.com/openapi-docs/latest/openapi.json` 에서 `/api/v1/order-info`
응답 스키마를 확인하고, Step 3 의 파싱 줄(`result` 에서 매수가능금액을 꺼내는 부분)을
실제 필드명에 맞춘다. 아래 구현은 `purchasingPower.amount` / `orderableAmount` 두 가지를
방어적으로 시도한다 — 실제 필드가 다르면 그 이름을 추가한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_toss_client.py` 의 기존 테스트 스타일을 확인한 뒤(대개 `requests_mock`
또는 `responses`, 또는 `FakeSession`), 아래를 그 스타일에 맞춰 추가. 아래는 세션을
직접 대체하는 방식:

```python
def test_get_purchasing_power_usd_parses_amount():
    from orchestrator.toss_client import TossClient

    class FakeResp:
        status_code = 200

        def json(self):
            return {"result": {"purchasingPower": {"amount": "1234.56"}}}

    class FakeSession:
        def request(self, method, url, **kwargs):
            if url.endswith("/oauth2/token"):
                class R:
                    status_code = 200

                    def json(self):
                        return {"access_token": "t", "token_type": "Bearer", "expires_in": 3600}

                return R()
            if url.endswith("/api/v1/accounts"):
                class R:
                    status_code = 200

                    def json(self):
                        return {"result": [{"accountNo": "1", "accountSeq": 1, "accountType": "BROKERAGE"}]}

                return R()
            return FakeResp()

    client = TossClient("id", "secret", session=FakeSession())
    assert client.get_purchasing_power_usd() == 1234.56
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_toss_client.py -k purchasing_power -v`
Expected: FAIL — `AttributeError: 'TossClient' object has no attribute 'get_purchasing_power_usd'`

- [ ] **Step 3: 구현**

`orchestrator/toss_client.py` 의 `get_holding_quantity` 메서드 뒤에 추가:

```python
    def get_purchasing_power_usd(self):
        """현금 기반 매수 가능 금액(USD)을 반환한다.

        GET /api/v1/order-info. 응답 필드명은 openapi.json 으로 확인해 필요 시
        아래 후보 키를 조정한다.
        """
        body = self._request(
            "GET", "/api/v1/order-info", headers=self._auth_headers()
        )
        result = body["result"]
        for key in ("purchasingPower", "orderableAmount", "buyingPower"):
            value = result.get(key)
            if isinstance(value, dict) and "amount" in value:
                return float(value["amount"])
            if isinstance(value, (int, float, str)) and value != "":
                return float(value)
        raise TossApiError(200, "no-purchasing-power", f"order-info 응답에서 매수가능금액을 찾지 못함: {result}")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_toss_client.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add orchestrator/toss_client.py tests/test_toss_client.py
git commit -m "Add TossClient.get_purchasing_power_usd (order-info endpoint)"
```

---

## Task 6: 스위치 실행 — `execute_switch_to_off` / `execute_switch_to_on`

**Files:**
- Modify: `orchestrator/regime.py`
- Test: `tests/test_regime_switch.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_switch.py` 생성:

```python
import pytest

import orchestrator.regime as regime
from orchestrator.config import RegimeSwitchConfig
from orchestrator.state import get_current_regime, init_db


@pytest.fixture
def conn():
    return init_db(":memory:")


class FakeToss:
    def __init__(self, holdings=None, purchasing_power=1000.0):
        self._holdings = holdings or {}
        self._pp = purchasing_power
        self.orders = []

    def get_holding_quantity(self, ticker):
        return str(self._holdings.get(ticker, 0))

    def get_purchasing_power_usd(self):
        return self._pp

    def place_market_sell(self, ticker, quantity, client_order_id):
        self.orders.append(("SELL", ticker, quantity))
        self._holdings[ticker] = 0
        return {"orderId": f"s-{ticker}", "clientOrderId": client_order_id}

    def place_market_buy(self, ticker, amount_usd, client_order_id):
        self.orders.append(("BUY", ticker, round(amount_usd, 2)))
        return {"orderId": f"b-{ticker}", "clientOrderId": client_order_id}


def _cfg(**kw):
    return RegimeSwitchConfig(enabled=True, inverse_ticker="PSQ", max_deploy_pct=90.0, **kw)


def test_switch_to_off_sells_all_longs_and_buys_inverse(conn):
    toss = FakeToss(holdings={"MSFT": 2, "MU": 5}, purchasing_power=1000.0)
    positions_usd = {"MSFT": 60.0, "MU": 40.0}  # 매도합 100
    lines = regime.execute_switch_to_off(
        conn, toss, _cfg(), "2026-09-03", ["MSFT", "MU"], positions_usd,
        dry_run=False, transitioned=True, pending=0,
    )
    assert ("SELL", "MSFT", "2") in toss.orders
    assert ("SELL", "MU", "5") in toss.orders
    # deploy = min(1000, 100) * 0.9 = 90
    assert ("BUY", "PSQ", 90.0) in toss.orders
    assert get_current_regime(conn) == ("risk_off", 0)  # 매수 성공 → pending 해제


def test_switch_to_off_defers_inverse_buy_when_no_purchasing_power(conn):
    toss = FakeToss(holdings={"MSFT": 2}, purchasing_power=0.0)
    lines = regime.execute_switch_to_off(
        conn, toss, _cfg(), "2026-09-03", ["MSFT"], {"MSFT": 60.0},
        dry_run=False, transitioned=True, pending=0,
    )
    assert ("SELL", "MSFT", "2") in toss.orders
    assert not any(o[0] == "BUY" for o in toss.orders)
    assert get_current_regime(conn) == ("risk_off", 1)  # pending 유지


def test_switch_to_off_pending_retry_buys_without_selling(conn):
    from orchestrator.state import set_regime, set_pending_inverse_buy
    set_regime(conn, "risk_off", "2026-09-02")
    set_pending_inverse_buy(conn, 1)
    toss = FakeToss(holdings={}, purchasing_power=200.0)
    lines = regime.execute_switch_to_off(
        conn, toss, _cfg(), "2026-09-03", ["MSFT"], {},
        dry_run=False, transitioned=False, pending=1,
    )
    assert toss.orders == [("BUY", "PSQ", 180.0)]  # 200 * 0.9
    assert get_current_regime(conn) == ("risk_off", 0)


def test_switch_to_off_dry_run_places_no_orders_and_no_state_change(conn):
    toss = FakeToss(holdings={"MSFT": 2})
    regime.execute_switch_to_off(
        conn, toss, _cfg(), "2026-09-03", ["MSFT"], {"MSFT": 60.0},
        dry_run=True, transitioned=True, pending=0,
    )
    assert toss.orders == []
    assert get_current_regime(conn) == ("risk_on", 0)  # 기본값 그대로


def test_switch_to_off_isolates_per_ticker_sell_failure(conn):
    class FlakyToss(FakeToss):
        def place_market_sell(self, ticker, quantity, client_order_id):
            if ticker == "MSFT":
                raise RuntimeError("boom")
            return super().place_market_sell(ticker, quantity, client_order_id)

    toss = FlakyToss(holdings={"MSFT": 2, "MU": 5}, purchasing_power=1000.0)
    lines = regime.execute_switch_to_off(
        conn, toss, _cfg(), "2026-09-03", ["MSFT", "MU"], {"MSFT": 60.0, "MU": 40.0},
        dry_run=False, transitioned=True, pending=0,
    )
    assert ("SELL", "MU", "5") in toss.orders  # MSFT 실패해도 MU 는 진행
    assert any("MSFT" in ln and "실패" in ln for ln in lines)


def test_switch_to_on_sells_inverse(conn):
    from orchestrator.state import set_regime
    set_regime(conn, "risk_off", "2026-09-01")
    toss = FakeToss(holdings={"PSQ": 30})
    lines = []
    regime.execute_switch_to_on(conn, toss, _cfg(), "2026-09-03", dry_run=False, lines=lines)
    assert ("SELL", "PSQ", "30") in toss.orders
    assert get_current_regime(conn) == ("risk_on", 0)


def test_switch_to_on_dry_run_no_orders_no_state_change(conn):
    from orchestrator.state import set_regime
    set_regime(conn, "risk_off", "2026-09-01")
    toss = FakeToss(holdings={"PSQ": 30})
    regime.execute_switch_to_on(conn, toss, _cfg(), "2026-09-03", dry_run=True, lines=[])
    assert toss.orders == []
    assert get_current_regime(conn) == ("risk_off", 0)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_regime_switch.py -v`
Expected: FAIL — `AttributeError: module 'orchestrator.regime' has no attribute 'execute_switch_to_off'`

- [ ] **Step 3: 구현**

`orchestrator/regime.py` 상단 import 에 추가:

```python
from orchestrator.state import (
    has_order_been_placed,
    mark_order_placed,
    record_order,
    set_pending_inverse_buy,
    set_regime,
)
```

`regime.py` 끝에 추가:

```python
def _sell_one(conn, toss, ticker, run_date, positions_usd, dry_run, lines, reason):
    """한 종목을 시장가 전량 매도한다. 실패는 이 종목에만 격리한다.
    반환: 이 종목의 직전 시장가치(매도합 누적용). 매도를 못 했으면 0.0."""
    try:
        qty = toss.get_holding_quantity(ticker)
    except Exception as exc:
        lines.append(f"  {ticker} 보유수량 조회 실패: {exc}")
        return 0.0
    if float(qty) <= 0:
        return 0.0
    if has_order_been_placed(conn, run_date, ticker, "SELL"):
        lines.append(f"  {ticker} 이미 매도됨 - 건너뜀")
        return positions_usd.get(ticker, 0.0)

    record_order(conn, run_date, ticker, "SELL", 0.0, True, reason)
    if dry_run:
        lines.append(f"  [드라이런] {ticker} {qty}주 매도")
        return positions_usd.get(ticker, 0.0)

    client_order_id = f"{run_date}-{ticker}-SELL"
    try:
        result = toss.place_market_sell(ticker, qty, client_order_id)
    except Exception as exc:
        lines.append(f"  {ticker} 매도 실패: {exc}")
        return 0.0
    mark_order_placed(conn, run_date, ticker, "SELL", result["orderId"], quantity=qty)
    lines.append(f"  {ticker} {qty}주 매도 (order {result['orderId']})")
    return positions_usd.get(ticker, 0.0)


def _attempt_inverse_buy(conn, toss, config, run_date, sold_total, dry_run, lines):
    rs = config.regime_switch if hasattr(config, "regime_switch") else config
    inv = rs.inverse_ticker
    if has_order_been_placed(conn, run_date, inv, "BUY"):
        lines.append(f"  {inv} 이미 매수됨 - 건너뜀")
        if not dry_run:
            set_pending_inverse_buy(conn, 0)
        return
    try:
        pp = toss.get_purchasing_power_usd()
    except Exception as exc:
        lines.append(f"  매수가능금액 조회 실패: {exc} - {inv} 매수 보류")
        return
    budget = min(pp, sold_total) if sold_total > 0 else pp
    deploy = budget * rs.max_deploy_pct / 100.0
    if deploy < MIN_ORDER_USD:
        lines.append(f"  매수가능금액 부족 (${pp:.2f}) - {inv} 매수는 결제 후 다음 실행에서")
        return

    record_order(conn, run_date, inv, "BUY", deploy, True, "regime_switch:inverse_buy")
    if dry_run:
        lines.append(f"  [드라이런] {inv} ${deploy:.2f} 매수")
        return
    client_order_id = f"{run_date}-{inv}-BUY"
    try:
        result = toss.place_market_buy(inv, deploy, client_order_id)
    except Exception as exc:
        lines.append(f"  {inv} 매수 실패: {exc} - 다음 실행에서 재시도")
        return
    mark_order_placed(conn, run_date, inv, "BUY", result["orderId"])
    set_pending_inverse_buy(conn, 0)
    lines.append(f"  {inv} ${deploy:.2f} 매수 (order {result['orderId']})")


def execute_switch_to_off(conn, toss, config, run_date, watchlist, positions_usd,
                          dry_run, transitioned, pending):
    """risk_off 상태에서의 스위치.

    - transitioned=True (risk_on→risk_off 최초): 롱 전량 매도 → set_regime + pending=1 → 인버스 매수 시도
    - transitioned=False, pending=1: 인버스 매수만 재시도
    - transitioned=False, pending=0: 홀딩 유지, 주문 없음
    """
    rs = config.regime_switch
    lines = []
    if transitioned:
        sold_total = 0.0
        for ticker in watchlist:
            sold_total += _sell_one(
                conn, toss, ticker, run_date, positions_usd, dry_run, lines,
                "regime_switch:on->off",
            )
        if not dry_run:
            set_regime(conn, "risk_off", run_date)
            set_pending_inverse_buy(conn, 1)
        _attempt_inverse_buy(conn, toss, config, run_date, sold_total, dry_run, lines)
    elif pending:
        lines.append(f"  {rs.inverse_ticker} 매수 재시도 (이전 실행에서 보류됨)")
        _attempt_inverse_buy(conn, toss, config, run_date, 0.0, dry_run, lines)
    else:
        lines.append("  risk_off 유지 - 주문 없음")
    return lines


def execute_switch_to_on(conn, toss, config, run_date, dry_run, lines):
    """risk_off→risk_on 전이: 인버스 ETF 전량 매도 후 레짐을 risk_on 으로."""
    inv = config.regime_switch.inverse_ticker
    try:
        qty = toss.get_holding_quantity(inv)
    except Exception as exc:
        lines.append(f"  {inv} 보유수량 조회 실패: {exc}")
        qty = "0"

    if float(qty) > 0 and not has_order_been_placed(conn, run_date, inv, "SELL"):
        record_order(conn, run_date, inv, "SELL", 0.0, True, "regime_switch:off->on")
        if dry_run:
            lines.append(f"  [드라이런] {inv} {qty}주 매도")
        else:
            client_order_id = f"{run_date}-{inv}-SELL"
            try:
                result = toss.place_market_sell(inv, qty, client_order_id)
                mark_order_placed(conn, run_date, inv, "SELL", result["orderId"], quantity=qty)
                lines.append(f"  {inv} {qty}주 매도 (order {result['orderId']})")
            except Exception as exc:
                lines.append(f"  {inv} 매도 실패: {exc}")

    if not dry_run:
        set_regime(conn, "risk_on", run_date)
        set_pending_inverse_buy(conn, 0)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_regime_switch.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add orchestrator/regime.py tests/test_regime_switch.py
git commit -m "Add execute_switch_to_off/on with per-order isolation and pending retry"
```

---

## Task 7: main.py 통합 — 8.5단계 `_handle_regime`

**Files:**
- Modify: `orchestrator/main.py`
- Test: `tests/test_main.py`, `tests/test_regime_switch.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_main.py` 의 `make_config()` 에 `regime_switch` 추가:

```python
    from orchestrator.config import RegimeSwitchConfig
    # make_config() 의 SimpleNamespace(...) 인자에 추가:
        regime_switch=RegimeSwitchConfig(enabled=False),
```

`FakeTossClient` 에 메서드 추가:

```python
    def get_purchasing_power_usd(self):
        return 1000.0
```

`tests/test_main.py` 끝에 추가:

```python
def test_regime_disabled_runs_normal_long_flow(monkeypatch, tmp_path):
    # regime_switch.enabled=False → 기존 흐름, regime 모듈 호출 안 함
    called = {"fetch": False}

    def fake_fetch(*a, **k):
        called["fetch"] = True
        return None

    monkeypatch.setattr("orchestrator.regime.fetch_regime_data", fake_fetch)
    main_module.run(dry_run=True)
    assert called["fetch"] is False


def test_regime_risk_off_transition_skips_long_analysis(monkeypatch, tmp_path):
    cfg = make_config()
    cfg.regime_switch.enabled = True
    monkeypatch.setattr(main_module, "load_config", lambda: cfg)

    from orchestrator.regime import RegimeData
    monkeypatch.setattr(
        "orchestrator.regime.fetch_regime_data",
        lambda *a, **k: RegimeData(qqq_close=90.0, sma200=100.0, sma50=95.0, vix=30.0),
    )

    analysis_calls = []
    monkeypatch.setattr(
        main_module, "run_analysis",
        lambda ticker, run_date: analysis_calls.append(ticker) or ("BUY", ""),
    )

    # 2일 연속 risk_off 원시신호가 쌓이도록 두 번 실행
    main_module.run(dry_run=False)
    main_module.run(dry_run=False)

    # 전이 후 실행에서는 TradingAgents 분석이 호출되지 않아야 함
    assert analysis_calls == [] or "MSFT" not in analysis_calls[-2:]


def test_regime_data_failure_keeps_long_flow(monkeypatch, tmp_path):
    cfg = make_config()
    cfg.regime_switch.enabled = True
    monkeypatch.setattr(main_module, "load_config", lambda: cfg)
    monkeypatch.setattr("orchestrator.regime.fetch_regime_data", lambda *a, **k: None)

    ran = []
    monkeypatch.setattr(
        main_module, "run_analysis",
        lambda ticker, run_date: ran.append(ticker) or ("HOLD", ""),
    )
    main_module.run(dry_run=False)
    assert ran == ["MSFT", "MU"]  # 데이터 실패해도 롱 흐름 정상 진행 (watchlist 순서)
```

> 참고: `make_config()` 의 watchlist 가 `["AAPL", "MSFT"]` 라면 위 assert 의 티커 이름을 그에 맞춘다. 현재 파일 기준 확인 후 수정.

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_main.py -k regime -v`
Expected: FAIL — `_handle_regime` 미구현으로 regime 분기 없음

- [ ] **Step 3: 구현**

`orchestrator/main.py` import 블록에 추가:

```python
from orchestrator import regime as regime_module
from orchestrator.state import (
    count_consecutive_signal,
    get_current_regime,
    has_order_been_placed,
    has_run_completed_today,
    init_db,
    is_circuit_breaker_triggered_today,
    mark_circuit_breaker_triggered,
    mark_order_placed,
    mark_run_completed,
    mark_run_started,
    record_order,
    record_regime_signal,
)
```

`_mark_order_placed_with_retry` 함수 뒤에 추가:

```python
def _handle_regime(conn, toss, config, run_date, account, dry_run):
    """8.5단계: 레짐 판정 + 스위치.

    반환: True 이면 이 실행은 레짐 스위치로 완결됨(롱 분석/주문 10~13단계 건너뜀).
          False 이면 호출부가 기존 롱 흐름을 계속 진행.
    """
    rs = config.regime_switch
    prev_regime, pending = get_current_regime(conn)

    data = regime_module.fetch_regime_data(rs.proxy_ticker, rs.sma_long, rs.sma_short)

    if data is None:
        send_telegram_message(
            config,
            f"[{run_date}] 레짐 판정 스킵 (데이터 조회 실패) — 이전 레짐({prev_regime}) 유지",
        )
        new_regime, transitioned = prev_regime, False
        lines = [f"[{run_date}] 레짐: {prev_regime} (데이터 없음)"]
    else:
        raw_off, raw_on = regime_module.compute_raw_signals(data, rs.vix_threshold)
        record_regime_signal(
            conn, run_date, raw_off, raw_on,
            data.qqq_close, data.sma200, data.sma50, data.vix,
        )
        off_streak = count_consecutive_signal(conn, run_date, "risk_off")
        on_streak = count_consecutive_signal(conn, run_date, "risk_on")
        new_regime, transitioned = regime_module.evaluate_regime(
            prev_regime, off_streak, on_streak, rs.confirm_days
        )
        vix_str = f"{data.vix:.1f}" if data.vix is not None else "N/A"
        head = f"[{run_date}] 레짐: {new_regime}"
        if transitioned:
            head += f" (전환: {prev_regime}→{new_regime})"
        lines = [head, f"  QQQ {data.qqq_close:.0f} / 200일선 {data.sma200:.0f} / VIX {vix_str}"]

    if new_regime == "risk_off":
        lines += regime_module.execute_switch_to_off(
            conn, toss, config, run_date, config.watchlist, account.positions_usd,
            dry_run, transitioned, pending,
        )
        send_telegram_message(config, "\n".join(lines))
        return True

    # risk_on
    if transitioned:
        regime_module.execute_switch_to_on(conn, toss, config, run_date, dry_run, lines)
    send_telegram_message(config, "\n".join(lines))
    return False
```

`run()` 안에서, 아래 블록 (현재 87~88줄):

```python
        if account.daily_pnl_pct <= config.risk_limits.daily_loss_circuit_pct:
            mark_circuit_breaker_triggered(conn, run_date)
```

바로 **다음에** 삽입 (기존 `if is_circuit_breaker_triggered_today(...)` 앞):

```python
        if config.regime_switch.enabled:
            if _handle_regime(conn, toss, config, run_date, account, dry_run):
                ping_healthcheck(config)
                if not dry_run:
                    mark_run_completed(conn, run_date)
                return
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_main.py tests/test_regime_switch.py -v`
Expected: PASS

- [ ] **Step 5: 전체 테스트**

Run: `python -m pytest -v`
Expected: PASS (전부)

- [ ] **Step 6: 커밋**

```bash
git add orchestrator/main.py tests/test_main.py
git commit -m "Wire regime switch into run() as step 8.5"
```

---

## Task 8: 백테스트 스크립트

**Files:**
- Create: `scripts/backtest_regime_switch.py`

- [ ] **Step 1: 구현**

`scripts/backtest_regime_switch.py` 생성 (기존 `scripts/backtest_risk_engine.py` 패턴 —
`if __name__ == "__main__"` 진입, print 로 결과 출력, assert 자체검증 포함):

```python
"""레짐 스위치 오프라인 백테스트.

레짐 규칙(QQQ 200/50일 SMA + VIX, confirm_days, 히스테리시스)을 과거 일봉에
매일 적용해, risk_off 구간엔 인버스(PSQ), risk_on 구간엔 QQQ 를 보유했을 때의
수익률을 바이앤홀드(QQQ)와 비교한다.

프로덕션 봇 실행에는 불필요. requirements-backtest.txt 기반.
    pip install -r requirements-backtest.txt
    python scripts/backtest_regime_switch.py
"""

import sys

import yfinance as yf

sys.path.insert(0, ".")
from orchestrator.regime import compute_raw_signals, evaluate_regime, RegimeData

VIX_THRESHOLD = 25.0
CONFIRM_DAYS = 2
SMA_LONG = 200
SMA_SHORT = 50
START = "2015-01-01"


def _load():
    qqq = yf.Ticker("QQQ").history(start=START)["Close"]
    psq = yf.Ticker("PSQ").history(start=START)["Close"]
    vix = yf.Ticker("^VIX").history(start=START)["Close"]
    df = qqq.to_frame("qqq").join(psq.to_frame("psq")).join(vix.to_frame("vix")).dropna()
    df["sma200"] = df["qqq"].rolling(SMA_LONG).mean()
    df["sma50"] = df["qqq"].rolling(SMA_SHORT).mean()
    return df.dropna()


def run():
    df = _load()
    regime = "risk_on"
    off_streak = on_streak = 0
    transitions = 0
    whipsaws = 0
    last_transition_idx = None

    strat_equity = 1.0
    bh_equity = 1.0
    prev_row = None
    equity_curve = []

    for i, (ts, row) in enumerate(df.iterrows()):
        if prev_row is not None:
            qqq_ret = row["qqq"] / prev_row["qqq"] - 1
            psq_ret = row["psq"] / prev_row["psq"] - 1
            strat_equity *= 1 + (psq_ret if regime == "risk_off" else qqq_ret)
            bh_equity *= 1 + qqq_ret
        equity_curve.append(strat_equity)

        data = RegimeData(row["qqq"], row["sma200"], row["sma50"], row["vix"])
        raw_off, raw_on = compute_raw_signals(data, VIX_THRESHOLD)
        off_streak = off_streak + 1 if raw_off else 0
        on_streak = on_streak + 1 if raw_on else 0
        new_regime, transitioned = evaluate_regime(regime, off_streak, on_streak, CONFIRM_DAYS)
        if transitioned:
            transitions += 1
            if last_transition_idx is not None and i - last_transition_idx <= 7:
                whipsaws += 1
            last_transition_idx = i
        regime = new_regime
        prev_row = row

    peak = 0.0
    max_dd = 0.0
    for e in equity_curve:
        peak = max(peak, e)
        max_dd = min(max_dd, e / peak - 1)

    print(f"기간: {df.index[0].date()} ~ {df.index[-1].date()}  ({len(df)}일)")
    print(f"전략 총수익률:      {strat_equity - 1:+.1%}")
    print(f"QQQ 바이앤홀드:     {bh_equity - 1:+.1%}")
    print(f"전이 횟수:          {transitions}")
    print(f"휩쏘(7일내 재전환): {whipsaws}")
    print(f"전략 최대낙폭:      {max_dd:.1%}")

    assert transitions > 0, "전이가 한 번도 없음 — 규칙/데이터 확인 필요"
    return strat_equity, bh_equity


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: 실행 확인**

Run: `.\venv\Scripts\python.exe scripts/backtest_regime_switch.py`
Expected: 기간/수익률/전이/휩쏘/최대낙폭 출력, 예외 없음

- [ ] **Step 3: 기본값 검토**

출력의 "휩쏘" 수가 전이 대비 과도하거나(예: 절반 이상) 전략 수익률이 바이앤홀드보다
크게 나쁘면, `config.yaml` 의 `vix_threshold` / `confirm_days` / `sma_long` 을 조정해
재실행. 결과 수치를 `docs/superpowers/specs/2026-09-03-regime-switch-design.md` 의
"백테스트" 섹션에 실측값으로 기록.

- [ ] **Step 4: 커밋**

```bash
git add scripts/backtest_regime_switch.py docs/superpowers/specs/2026-09-03-regime-switch-design.md
git commit -m "Add regime-switch backtest script and record measured baseline"
```

---

## Task 9: README 문서화

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 구현**

`README.md` 의 "## 리스크 엔진 백테스트" 섹션 **앞에** 새 섹션 추가:

```markdown
## 레짐 스위치 (인버스 ETF, 선택)

`config.yaml` 의 `regime_switch.enabled: true` 로 켜면, 매 실행 시 시장 레짐을
규칙으로 판정해 하락장이면 롱을 전량 인버스 ETF 로 전환한다. LLM(TradingAgents)은
쓰지 않는다.

**판정 규칙 (QQQ 를 지수 프록시로):**
- **risk-off 진입**: QQQ 종가 < 200일 SMA **그리고** (50일<200일 데드크로스 **또는**
  VIX > `vix_threshold`), `confirm_days` 일 연속 충족
- **risk-on 복귀**: QQQ 종가 ≥ 200일 SMA, `confirm_days` 일 연속 (진입보다 느슨 =
  휩쏘 완화)

**전환 동작:**
- risk_on → risk_off: watchlist 롱 전량 시장가 매도 → 매도대금·매수가능금액 중
  작은 값의 `max_deploy_pct`% 로 `inverse_ticker`(기본 PSQ) 매수. 그 실행에서는
  TradingAgents 분석/주문을 건너뛴다 (Gemini 호출 0). 서킷브레이커가 발동한 날도
  스위치는 실행한다.
- 미국장 결제 지연으로 인버스 매수가 당일 불가하면, 매도만 하고 다음 실행에서
  자동 재시도한다 (`regime_state.pending_inverse_buy`).
- risk_off → risk_on: 인버스 ETF 전량 매도 후 기존 롱 흐름 재개 (이전 포트폴리오는
  복원하지 않음 — TradingAgents 가 새로 구성).

**시세 데이터**: yfinance 로 QQQ 일봉 + ^VIX 를 받는다. 조회 실패 시 이전 레짐을
유지하고 롱 흐름은 정상 진행한다 (fail-safe).

**드라이런**: 실제 주문·상태 변경을 하지 않고 "이런 전환이 일어났을 것" 알림만
보낸다. 드라이런 중에는 전이가 매일 재감지되어 알림이 반복되는 것이 정상이다.

**백테스트**: `python scripts/backtest_regime_switch.py` 로 규칙 기본값이 휩쏘로
수익을 잠식하지 않는지 검증한다.

**설정 (`config.yaml`):**
\```yaml
regime_switch:
  enabled: false
  proxy_ticker: QQQ
  inverse_ticker: PSQ
  sma_long: 200
  sma_short: 50
  vix_threshold: 25.0
  confirm_days: 2
  max_deploy_pct: 90
\```
```

(위 코드펜스 안의 `\``` 는 실제로는 백틱 3개로 작성)

- [ ] **Step 2: 커밋**

```bash
git add README.md
git commit -m "Document regime switch in README"
```

---

## Self-Review 결과

**Spec coverage:**
- Q1 인버스 ETF 매수 → Task 6 `_attempt_inverse_buy`
- Q2 규칙 기반 감지기 → Task 3~4
- Q3 완전 스위치 → Task 6 `execute_switch_to_off` (롱 전량 매도), Task 7 (10~13 스킵)
- Q4 yfinance → Task 3
- Q5 PSQ 1x → config 기본값 `inverse_ticker: PSQ`
- Q6 2팩터+히스테리시스 → Task 4 `evaluate_regime`
- Q7 별도 한도 `max_deploy_pct` → Task 6, config
- Q8 서킷브레이커 무시 → Task 7 (8.5단계를 서킷브레이커 early return 앞 배치)
- 데이터 모델 (regime_signals/regime_state) → Task 2
- order-info → Task 5
- 알림 → Task 7 `_handle_regime` lines
- 드라이런 상태 불변 → Task 6 (dry_run 가드), Task 7 (mark_run_completed 가드)
- risk_on 결제: per-order 격리 + 다음날 재시도 → 기존 main.py 로직 그대로 (신규 코드 없음, 스펙대로)
- 테스트 → Task 2/3/4/5/6/7
- 백테스트 → Task 8
- README → Task 9

**Placeholder scan:** Task 5 Step 0, Task 7 Step 1 주석, Task 8 Step 3 은 "구현 시 확인" 지시이며 fallback 코드가 있음 — 플레이스홀더 아님.

**Type consistency:**
- `RegimeData(qqq_close, sma200, sma50, vix)` — Task 3 정의, Task 4/6/7/8 에서 동일 사용
- `evaluate_regime(prev_regime, off_streak, on_streak, confirm_days) -> (new_regime, transitioned)` — Task 4 정의, Task 7/8 동일
- `execute_switch_to_off(conn, toss, config, run_date, watchlist, positions_usd, dry_run, transitioned, pending)` — Task 6 정의, Task 7 호출 시그니처 일치
- `execute_switch_to_on(conn, toss, config, run_date, dry_run, lines)` — Task 6 정의, Task 7 호출 일치
- `get_current_regime` → `(regime, pending)` 튜플 — Task 2 정의, Task 6/7 언팩 일치
- `config.regime_switch.{proxy_ticker,sma_long,sma_short,vix_threshold,confirm_days,inverse_ticker,max_deploy_pct}` — Task 1 `RegimeSwitchConfig` 필드와 Task 3/6/7 접근 일치
