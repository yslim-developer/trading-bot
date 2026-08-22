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
            reason TEXT NOT NULL,
            placed INTEGER NOT NULL DEFAULT 0,
            order_id TEXT,
            quantity TEXT
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
