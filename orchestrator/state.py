import sqlite3
from datetime import date, datetime


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
            reason TEXT NOT NULL,
            placed INTEGER NOT NULL DEFAULT 0,
            order_id TEXT,
            quantity TEXT
        )
        """
    )
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


def mark_order_placed(conn, run_date, ticker, action, order_id, quantity=None):
    conn.execute(
        "UPDATE orders SET placed = 1, order_id = ?, quantity = ? WHERE id = ("
        "  SELECT id FROM orders"
        "  WHERE run_date = ? AND ticker = ? AND action = ? AND placed = 0"
        "  ORDER BY id DESC LIMIT 1"
        ")",
        (order_id, quantity, run_date, ticker, action),
    )
    conn.commit()


def has_order_been_placed(conn, run_date, ticker, action):
    row = conn.execute(
        "SELECT 1 FROM orders WHERE run_date = ? AND ticker = ? AND action = ? AND placed = 1",
        (run_date, ticker, action),
    ).fetchone()
    return row is not None


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
