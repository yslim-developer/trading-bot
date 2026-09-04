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
