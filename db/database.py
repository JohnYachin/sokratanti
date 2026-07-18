"""
База данных PostgreSQL для Sokratanti.
Хранит историю сигналов, цен и сентимента.
Fallback на SQLite если PostgreSQL недоступен.
"""
import os
import logging
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Определяем бэкенд ─────────────────────────────────────────────────────────
def _get_pg_conn():
    """Подключение к PostgreSQL."""
    try:
        import psycopg2
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            return None
        conn = psycopg2.connect(dsn)
        return conn
    except Exception as e:
        logger.warning("PostgreSQL недоступен: %s", e)
        return None


def _use_postgres() -> bool:
    conn = _get_pg_conn()
    if conn:
        conn.close()
        return True
    return False


# ── Инициализация ─────────────────────────────────────────────────────────────
def init_db():
    """Создаёт таблицы если не существуют."""
    if _use_postgres():
        _init_postgres()
    else:
        _init_sqlite()


def _init_postgres():
    conn = _get_pg_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id          SERIAL PRIMARY KEY,
            coin        VARCHAR(10) NOT NULL,
            signal      VARCHAR(10) NOT NULL,
            score       INTEGER,
            rsi         NUMERIC(6,2),
            macd_diff   NUMERIC(12,6),
            price_usd   NUMERIC(16,2),
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id          SERIAL PRIMARY KEY,
            coin        VARCHAR(10) NOT NULL,
            price_usd   NUMERIC(16,2),
            change_24h  NUMERIC(8,2),
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sentiment_history (
            id          SERIAL PRIMARY KEY,
            coin        VARCHAR(10) NOT NULL,
            score       NUMERIC(5,3),
            sentiment   VARCHAR(20),
            summary     TEXT,
            source      VARCHAR(50),
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id              SERIAL PRIMARY KEY,
            idempotency_key VARCHAR(128) UNIQUE,
            coin            VARCHAR(10) NOT NULL,
            signal          VARCHAR(30) NOT NULL,
            price_usd       NUMERIC(16,4),
            sent_at         TIMESTAMPTZ DEFAULT NOW(),
            cooldown_until  TIMESTAMPTZ
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id          SERIAL PRIMARY KEY,
            coin        VARCHAR(10) NOT NULL,
            quantity    NUMERIC(20,8) NOT NULL,
            buy_price   NUMERIC(16,4) NOT NULL,
            buy_date    DATE DEFAULT CURRENT_DATE,
            notes       TEXT,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS signal_trades (
            id            SERIAL PRIMARY KEY,
            coin          VARCHAR(10) NOT NULL,
            signal_type   VARCHAR(20) NOT NULL,
            setup_score   INTEGER,
            entry_price   NUMERIC(20,6),
            stop_loss     NUMERIC(20,6),
            target1       NUMERIC(20,6),
            target2       NUMERIC(20,6),
            leverage      INTEGER DEFAULT 1,
            status        VARCHAR(15) DEFAULT 'open',
            exit_price    NUMERIC(20,6),
            pnl_pct       NUMERIC(8,4),
            pnl_lev_pct   NUMERIC(8,4),
            outcome_note  TEXT,
            created_at    TIMESTAMPTZ DEFAULT NOW(),
            closed_at     TIMESTAMPTZ
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS optimized_params (
            coin        VARCHAR(10) NOT NULL,
            interval    VARCHAR(5)  NOT NULL,
            min_score   INTEGER,
            atr_stop    NUMERIC(4,2),
            atr_target  NUMERIC(4,2),
            max_hold    INTEGER,
            win_rate    NUMERIC(5,3),
            avg_rr      NUMERIC(6,3),
            n_trades    INTEGER,
            quality     NUMERIC(8,4),
            lookback    INTEGER,
            updated_at  TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (coin, interval)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS indicator_weights (
            coin        VARCHAR(10) NOT NULL,
            indicator   VARCHAR(30) NOT NULL,
            wins        INTEGER DEFAULT 0,
            losses      INTEGER DEFAULT 0,
            weight_adj  NUMERIC(6,2) DEFAULT 0,
            updated_at  TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (coin, indicator)
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    logger.info("PostgreSQL БД готова.")


def _init_sqlite():
    db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coin TEXT NOT NULL,
            signal TEXT NOT NULL,
            score INTEGER,
            rsi REAL,
            macd_diff REAL,
            price_usd REAL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coin TEXT NOT NULL,
            price_usd REAL,
            change_24h REAL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sentiment_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coin TEXT NOT NULL,
            score REAL,
            sentiment TEXT,
            summary TEXT,
            source TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT UNIQUE,
            coin            TEXT NOT NULL,
            signal          TEXT NOT NULL,
            price_usd       REAL,
            sent_at         TEXT DEFAULT (datetime('now')),
            cooldown_until  TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            coin        TEXT NOT NULL,
            quantity    REAL NOT NULL,
            buy_price   REAL NOT NULL,
            buy_date    TEXT DEFAULT (date('now')),
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS signal_trades (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            coin          TEXT NOT NULL,
            signal_type   TEXT NOT NULL,
            setup_score   INTEGER,
            entry_price   REAL,
            stop_loss     REAL,
            target1       REAL,
            target2       REAL,
            leverage      INTEGER DEFAULT 1,
            status        TEXT DEFAULT 'open',
            exit_price    REAL,
            pnl_pct       REAL,
            pnl_lev_pct   REAL,
            outcome_note  TEXT,
            created_at    TEXT DEFAULT (datetime('now')),
            closed_at     TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS optimized_params (
            coin        TEXT NOT NULL,
            interval    TEXT NOT NULL,
            min_score   INTEGER,
            atr_stop    REAL,
            atr_target  REAL,
            max_hold    INTEGER,
            win_rate    REAL,
            avg_rr      REAL,
            n_trades    INTEGER,
            quality     REAL,
            lookback    INTEGER,
            updated_at  TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (coin, interval)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS indicator_weights (
            coin        TEXT NOT NULL,
            indicator   TEXT NOT NULL,
            wins        INTEGER DEFAULT 0,
            losses      INTEGER DEFAULT 0,
            weight_adj  REAL DEFAULT 0,
            updated_at  TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (coin, indicator)
        )
    """)
    conn.commit()
    conn.close()
    logger.info("SQLite БД готова: %s", db_path)


# ── Запись сигнала ─────────────────────────────────────────────────────────────
def save_signal(coin: str, signal: str, score: int = 0,
                rsi: float = 0.0, macd_diff: float = 0.0,
                price_usd: float = 0.0):
    if _use_postgres():
        conn = _get_pg_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO signals (coin, signal, score, rsi, macd_diff, price_usd) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (coin, signal, score, rsi, macd_diff, price_usd)
        )
        conn.commit(); cur.close(); conn.close()
    else:
        db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO signals (coin, signal, score, rsi, macd_diff, price_usd) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (coin, signal, score, rsi, macd_diff, price_usd)
        )
        conn.commit(); conn.close()


# ── Запись цены ───────────────────────────────────────────────────────────────
def save_price(coin: str, price_usd: float, change_24h: float):
    if _use_postgres():
        conn = _get_pg_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO prices (coin, price_usd, change_24h) VALUES (%s, %s, %s)",
            (coin, price_usd, change_24h)
        )
        conn.commit(); cur.close(); conn.close()
    else:
        db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO prices (coin, price_usd, change_24h) VALUES (?, ?, ?)",
            (coin, price_usd, change_24h)
        )
        conn.commit(); conn.close()


# ── Запись сентимента ─────────────────────────────────────────────────────────
def save_sentiment(coin: str, score: float, sentiment: str,
                   summary: str, source: str = "openai"):
    if _use_postgres():
        conn = _get_pg_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sentiment_history (coin, score, sentiment, summary, source) "
            "VALUES (%s, %s, %s, %s, %s)",
            (coin, score, sentiment, summary, source)
        )
        conn.commit(); cur.close(); conn.close()
    else:
        db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO sentiment_history (coin, score, sentiment, summary, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (coin, score, sentiment, summary, source)
        )
        conn.commit(); conn.close()


# ── Запись алерта ─────────────────────────────────────────────────────────────
def save_alert(coin: str, signal: str, price_usd: float):
    if _use_postgres():
        conn = _get_pg_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO alerts (coin, signal, price_usd) VALUES (%s, %s, %s)",
            (coin, signal, price_usd)
        )
        conn.commit(); cur.close(); conn.close()
    else:
        db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO alerts (coin, signal, price_usd) VALUES (?, ?, ?)",
            (coin, signal, price_usd)
        )
        conn.commit(); conn.close()


# ── Чтение истории сигналов ───────────────────────────────────────────────────
def get_history(limit: int = 10) -> list:
    if _use_postgres():
        conn = _get_pg_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT coin, signal, score, created_at FROM signals "
            "ORDER BY created_at DESC LIMIT %s",
            (limit,)
        )
        rows = cur.fetchall()
        cur.close(); conn.close()
        return rows
    else:
        db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT coin, signal, score, created_at FROM signals "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        rows = cur.fetchall()
        conn.close()
        return rows


# ── Статистика ────────────────────────────────────────────────────────────────
def get_stats() -> dict:
    """Статистика по всем монетам за последние 7 дней."""
    if _use_postgres():
        conn = _get_pg_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT coin,
                   COUNT(*) FILTER (WHERE signal='BUY')  AS buys,
                   COUNT(*) FILTER (WHERE signal='SELL') AS sells,
                   COUNT(*) FILTER (WHERE signal='HOLD') AS holds,
                   COUNT(*) AS total
            FROM signals
            WHERE created_at >= NOW() - INTERVAL '7 days'
            GROUP BY coin ORDER BY coin
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
    else:
        db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT coin,
                   SUM(CASE WHEN signal='BUY'  THEN 1 ELSE 0 END),
                   SUM(CASE WHEN signal='SELL' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN signal='HOLD' THEN 1 ELSE 0 END),
                   COUNT(*)
            FROM signals
            WHERE created_at >= datetime('now', '-7 days')
            GROUP BY coin ORDER BY coin
        """)
        rows = cur.fetchall()
        conn.close()

    return {
        row[0]: {"buy": row[1], "sell": row[2], "hold": row[3], "total": row[4]}
        for row in rows
    }


# ── Идемпотентные алерты (состояние в БД) ─────────────────────────────────────────────────────

def alert_already_sent(idempotency_key: str, cooldown_seconds: int = 14400) -> bool:
    """
    Проверяет был ли алерт с этим ключом отправлен за последние cooldown_seconds.
    Возвращает True если алерт уже был, False если нужно отправить.
    """
    try:
        if _use_postgres():
            conn = _get_pg_conn()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT 1 FROM alerts
                WHERE idempotency_key = %s
                  AND sent_at >= NOW() - INTERVAL '%s seconds'
                LIMIT 1
                """,
                (idempotency_key, cooldown_seconds)
            )
            found = cur.fetchone() is not None
            cur.close(); conn.close()
            return found
        else:
            db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT 1 FROM alerts
                WHERE idempotency_key = ?
                  AND sent_at >= datetime('now', ? || ' seconds')
                LIMIT 1
                """,
                (idempotency_key, f"-{cooldown_seconds}")
            )
            found = cur.fetchone() is not None
            conn.close()
            return found
    except Exception as e:
        logger.warning("alert_already_sent error: %s", e)
        return False  # При ошибке — отправляем (не пропускаем)


def record_alert(idempotency_key: str, coin: str, signal: str, price_usd: float):
    """
    Записывает алерт в БД с idempotency_key.
    INSERT OR IGNORE — если ключ уже есть, игнорируем.
    """
    try:
        if _use_postgres():
            conn = _get_pg_conn()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO alerts (idempotency_key, coin, signal, price_usd)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (idempotency_key, coin, signal, price_usd)
            )
            conn.commit(); cur.close(); conn.close()
        else:
            db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR IGNORE INTO alerts (idempotency_key, coin, signal, price_usd)
                VALUES (?, ?, ?, ?)
                """,
                (idempotency_key, coin, signal, price_usd)
            )
            conn.commit(); conn.close()
    except Exception as e:
        logger.error("record_alert error: %s", e)


# ── Portfolio CRUD ─────────────────────────────────────────────────────────────

def portfolio_add(coin: str, quantity: float, buy_price: float, notes: str = "") -> int:
    """
    Добавляет позицию в портфель.
    Returns: id новой записи
    """
    coin = coin.lower()
    try:
        if _use_postgres():
            conn = _get_pg_conn()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO portfolio (coin, quantity, buy_price, notes) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (coin, quantity, buy_price, notes)
            )
            row_id = cur.fetchone()[0]
            conn.commit(); cur.close(); conn.close()
            return row_id
        else:
            db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO portfolio (coin, quantity, buy_price, notes) VALUES (?, ?, ?, ?)",
                (coin, quantity, buy_price, notes)
            )
            row_id = cur.lastrowid
            conn.commit(); conn.close()
            return row_id
    except Exception as e:
        logger.error("portfolio_add error: %s", e)
        return -1


def portfolio_remove(entry_id: int) -> bool:
    """Удаляет запись по id. Returns True если удалено."""
    try:
        if _use_postgres():
            conn = _get_pg_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM portfolio WHERE id = %s", (entry_id,))
            deleted = cur.rowcount > 0
            conn.commit(); cur.close(); conn.close()
            return deleted
        else:
            db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("DELETE FROM portfolio WHERE id = ?", (entry_id,))
            deleted = cur.rowcount > 0
            conn.commit(); conn.close()
            return deleted
    except Exception as e:
        logger.error("portfolio_remove error: %s", e)
        return False


def portfolio_get_all() -> list[dict]:
    """
    Возвращает все позиции портфеля.
    Returns: [{"id", "coin", "quantity", "buy_price", "buy_date", "notes"}, ...]
    """
    try:
        if _use_postgres():
            conn = _get_pg_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT id, coin, quantity, buy_price, buy_date, notes "
                "FROM portfolio ORDER BY coin, created_at"
            )
            rows = cur.fetchall()
            cur.close(); conn.close()
        else:
            db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT id, coin, quantity, buy_price, buy_date, notes "
                "FROM portfolio ORDER BY coin, created_at"
            )
            rows = cur.fetchall()
            conn.close()

        return [
            {"id": r[0], "coin": r[1], "quantity": float(r[2]),
             "buy_price": float(r[3]), "buy_date": str(r[4]), "notes": r[5] or ""}
            for r in rows
        ]
    except Exception as e:
        logger.error("portfolio_get_all error: %s", e)
        return []


def portfolio_remove_by_coin(coin: str) -> int:
    """Удаляет ВСЕ позиции по монете. Returns количество удалённых записей."""
    coin = coin.lower()
    try:
        if _use_postgres():
            conn = _get_pg_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM portfolio WHERE coin = %s", (coin,))
            count = cur.rowcount
            conn.commit(); cur.close(); conn.close()
            return count
        else:
            db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("DELETE FROM portfolio WHERE coin = ?", (coin,))
            count = cur.rowcount
            conn.commit(); conn.close()
            return count
    except Exception as e:
        logger.error("portfolio_remove_by_coin error: %s", e)
        return 0


# ── Signal Trades (трекинг результатов) ──────────────────────────────────────

def signal_trade_open(coin: str, signal_type: str, setup_score: int,
                      entry_price: float, stop_loss: float,
                      target1: float, target2: float | None,
                      leverage: int) -> int:
    """Сохраняет новый открытый сигнал. Возвращает ID."""
    try:
        if _use_postgres():
            conn = _get_pg_conn(); cur = conn.cursor()
            cur.execute("""
                INSERT INTO signal_trades
                  (coin, signal_type, setup_score, entry_price, stop_loss,
                   target1, target2, leverage)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (coin, signal_type, setup_score, entry_price, stop_loss,
                  target1, target2, leverage))
            row_id = cur.fetchone()[0]
            conn.commit(); cur.close(); conn.close()
            return row_id
        else:
            import sqlite3, os
            db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
            conn = sqlite3.connect(db_path); cur = conn.cursor()
            cur.execute("""
                INSERT INTO signal_trades
                  (coin, signal_type, setup_score, entry_price, stop_loss,
                   target1, target2, leverage)
                VALUES (?,?,?,?,?,?,?,?)
            """, (coin, signal_type, setup_score, entry_price, stop_loss,
                  target1, target2, leverage))
            row_id = cur.lastrowid
            conn.commit(); conn.close()
            return row_id
    except Exception as e:
        logger.error("signal_trade_open error: %s", e)
        return -1


def signal_trade_close(trade_id: int, status: str, exit_price: float,
                       pnl_pct: float, pnl_lev_pct: float,
                       outcome_note: str = "") -> bool:
    """Закрывает сигнал с результатом. status: 'win'/'loss'/'timeout'."""
    try:
        if _use_postgres():
            conn = _get_pg_conn(); cur = conn.cursor()
            cur.execute("""
                UPDATE signal_trades SET
                  status=%, exit_price=%s, pnl_pct=%s, pnl_lev_pct=%s,
                  outcome_note=%s, closed_at=NOW()
                WHERE id=%s
            """, (status, exit_price, pnl_pct, pnl_lev_pct, outcome_note, trade_id))
            conn.commit(); cur.close(); conn.close()
        else:
            import sqlite3, os
            db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
            conn = sqlite3.connect(db_path); cur = conn.cursor()
            cur.execute("""
                UPDATE signal_trades SET
                  status=?, exit_price=?, pnl_pct=?, pnl_lev_pct=?,
                  outcome_note=?, closed_at=datetime('now')
                WHERE id=?
            """, (status, exit_price, pnl_pct, pnl_lev_pct, outcome_note, trade_id))
            conn.commit(); conn.close()
        return True
    except Exception as e:
        logger.error("signal_trade_close error: %s", e)
        return False


def signal_trades_get_open() -> list[dict]:
    """Возвращает все незакрытые сигналы."""
    try:
        cols = ("id", "coin", "signal_type", "setup_score", "entry_price",
                "stop_loss", "target1", "target2", "leverage", "created_at")
        if _use_postgres():
            conn = _get_pg_conn(); cur = conn.cursor()
            cur.execute(
                "SELECT id,coin,signal_type,setup_score,entry_price,"
                "stop_loss,target1,target2,leverage,created_at "
                "FROM signal_trades WHERE status='open' ORDER BY created_at"
            )
            rows = cur.fetchall(); cur.close(); conn.close()
        else:
            import sqlite3, os
            db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
            conn = sqlite3.connect(db_path); cur = conn.cursor()
            cur.execute(
                "SELECT id,coin,signal_type,setup_score,entry_price,"
                "stop_loss,target1,target2,leverage,created_at "
                "FROM signal_trades WHERE status='open' ORDER BY created_at"
            )
            rows = cur.fetchall(); conn.close()
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        logger.error("signal_trades_get_open error: %s", e)
        return []


def signal_trades_get_recent(limit: int = 10) -> list[dict]:
    """Возвращает последние закрытые сигналы для статистики."""
    try:
        cols = ("id", "coin", "signal_type", "setup_score", "entry_price",
                "exit_price", "pnl_pct", "pnl_lev_pct", "leverage",
                "status", "outcome_note", "created_at", "closed_at")
        if _use_postgres():
            conn = _get_pg_conn(); cur = conn.cursor()
            cur.execute(
                "SELECT id,coin,signal_type,setup_score,entry_price,"
                "exit_price,pnl_pct,pnl_lev_pct,leverage,status,"
                "outcome_note,created_at,closed_at "
                "FROM signal_trades WHERE status!='open' "
                "ORDER BY closed_at DESC LIMIT %s", (limit,)
            )
            rows = cur.fetchall(); cur.close(); conn.close()
        else:
            import sqlite3, os
            db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
            conn = sqlite3.connect(db_path); cur = conn.cursor()
            cur.execute(
                "SELECT id,coin,signal_type,setup_score,entry_price,"
                "exit_price,pnl_pct,pnl_lev_pct,leverage,status,"
                "outcome_note,created_at,closed_at "
                "FROM signal_trades WHERE status!='open' "
                "ORDER BY closed_at DESC LIMIT ?", (limit,)
            )
            rows = cur.fetchall(); conn.close()
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        logger.error("signal_trades_get_recent error: %s", e)
        return []
