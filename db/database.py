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
            id          SERIAL PRIMARY KEY,
            coin        VARCHAR(10) NOT NULL,
            signal      VARCHAR(10) NOT NULL,
            price_usd   NUMERIC(16,2),
            sent_at     TIMESTAMPTZ DEFAULT NOW()
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coin TEXT NOT NULL,
            signal TEXT NOT NULL,
            price_usd REAL,
            sent_at TEXT DEFAULT (datetime('now'))
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
