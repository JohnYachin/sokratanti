"""
SQLite база данных для хранения истории торговых сигналов.
"""
import os
import sqlite3
import logging

logger = logging.getLogger(__name__)

# БД создаётся в корне проекта
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "sokratanti.db")


def init_db():
    """Создаёт таблицу signals если её нет."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                coin           TEXT    NOT NULL,
                signal         TEXT    NOT NULL,
                score          REAL    NOT NULL DEFAULT 0,
                rsi            REAL,
                macd_diff      REAL,
                sentiment_score REAL   DEFAULT 0,
                created_at     TEXT    DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()
    logger.info("DB готова: %s", os.path.abspath(DB_PATH))


def save_signal(
    coin: str,
    signal: str,
    score: float,
    rsi: float = 0.0,
    macd_diff: float = 0.0,
    sentiment_score: float = 0.0,
):
    """Сохраняет торговый сигнал."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO signals (coin, signal, score, rsi, macd_diff, sentiment_score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (coin.upper(), signal, score, rsi, macd_diff, sentiment_score),
        )
        conn.commit()


def get_history(limit: int = 10) -> list[tuple]:
    """Возвращает последние N записей: (coin, signal, score, created_at)."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            SELECT coin, signal, score, created_at
            FROM signals
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return cursor.fetchall()


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)
