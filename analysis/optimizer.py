"""
analysis/optimizer.py — Адаптивная оптимизация v1.0

Система трёхуровневого обучения:

  Уровень 1: Grid Search
    Перебирает 36 комбинаций параметров (min_score, atr_stop, atr_target)
    Сохраняет лучшие параметры в БД per-coin.

  Уровень 2: Indicator Reliability Tracking
    Для каждого закрытого трейда анализирует какие индикаторы
    были активны при входе и насколько они были предсказательны.
    Сохраняет reliability (win rate per indicator) в БД.

  Уровень 3: Применение
    get_learned_weights(coin) возвращает адаптированные веса
    которые _calc_setup_score() использует вместо хардкодных.

Всё хранится в SQLite/PostgreSQL — выживает перезапуски.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Grid поиска
_SCORE_GRID   = [15, 20, 25, 30]
_STOP_GRID    = [1.0, 1.5, 2.0]
_TARGET_GRID  = [2.0, 2.5, 3.0]
_HOLD_GRID    = [15, 20]

# Минимум сделок чтобы доверять метрикам
MIN_TRADES_FOR_TRUST = 3

# Индикаторные состояния которые отслеживаем
INDICATOR_CONDITIONS = [
    "rsi_oversold",   # RSI <= 35
    "rsi_moderate",   # 35 < RSI <= 50
    "macd_bull",      # MACD > 0
    "bb_low",         # BB pband <= 0.25
    "ema_bull",       # price > EMA20 > EMA50
    "ema_above_200",  # price > EMA200
    "adx_trending",   # ADX >= 20
]


# ── Уровень 1: Grid Search ────────────────────────────────────────────────────

def run_grid_search(
    coin: str,
    interval: str = "1d",
    lookback: int = 180,
) -> Optional[dict]:
    """
    Перебирает 36 комбинаций параметров бэктеста.
    Загружает данные один раз — все прогоны на одном DataFrame.

    Returns лучшие параметры или None если данных недостаточно.
    """
    from market_data.binance_rest import get_klines
    from analysis.backtest import _run_backtest_core, WARMUP_BARS

    # Загружаем данные один раз
    from analysis.backtest import MAX_HOLD_BARS as DEFAULT_HOLD
    needed = lookback + WARMUP_BARS + DEFAULT_HOLD + 20
    df = get_klines(coin, interval=interval, limit=needed)

    if df.empty or len(df) < WARMUP_BARS + 20:
        logger.warning("Grid search %s: недостаточно данных", coin)
        return None

    best_quality = -999.0
    best_params = None
    best_result = None
    all_results = []

    total = len(_SCORE_GRID) * len(_STOP_GRID) * len(_TARGET_GRID) * len(_HOLD_GRID)
    logger.info("Grid search %s: %d комбинаций...", coin.upper(), total)

    for min_score in _SCORE_GRID:
        for atr_stop in _STOP_GRID:
            for atr_target in _TARGET_GRID:
                for max_hold in _HOLD_GRID:
                    try:
                        result = _run_backtest_core(
                            df=df,
                            coin=coin,
                            interval=interval,
                            lookback=lookback,
                            min_score=min_score,
                            atr_stop_mult=atr_stop,
                            atr_target_mult=atr_target,
                            max_hold_bars=max_hold,
                        )
                        if result is None or result.n_trades < MIN_TRADES_FOR_TRUST:
                            continue

                        # Quality = win_rate × avg_rr - |max_drawdown|
                        # Скорректированный на риск доход
                        quality = (
                            result.win_rate * result.avg_rr_achieved
                            - abs(result.max_drawdown)
                        )

                        all_results.append({
                            "params": {
                                "min_score": min_score,
                                "atr_stop": atr_stop,
                                "atr_target": atr_target,
                                "max_hold": max_hold,
                            },
                            "quality": quality,
                            "win_rate": result.win_rate,
                            "avg_rr": result.avg_rr_achieved,
                            "n_trades": result.n_trades,
                            "max_drawdown": result.max_drawdown,
                            "total_return": result.total_return,
                        })

                        if quality > best_quality:
                            best_quality = quality
                            best_params = {
                                "min_score": min_score,
                                "atr_stop": atr_stop,
                                "atr_target": atr_target,
                                "max_hold": max_hold,
                            }
                            best_result = result

                    except Exception as e:
                        logger.debug("Grid search error %s (s=%d,sl=%.1f,tp=%.1f): %s",
                                     coin, min_score, atr_stop, atr_target, e)

    if best_params is None:
        logger.warning("Grid search %s: нет результатов с >= %d сделок", coin, MIN_TRADES_FOR_TRUST)
        return None

    logger.info(
        "Grid search %s: лучшие params=%s, quality=%.3f, WR=%.1f%%, R/R=%.2f, trades=%d",
        coin.upper(), best_params, best_quality,
        best_result.win_rate * 100, best_result.avg_rr_achieved, best_result.n_trades
    )

    return {
        "coin": coin,
        "interval": interval,
        "lookback": lookback,
        "params": best_params,
        "quality": best_quality,
        "win_rate": best_result.win_rate,
        "avg_rr": best_result.avg_rr_achieved,
        "n_trades": best_result.n_trades,
        "max_drawdown": best_result.max_drawdown,
        "total_return": best_result.total_return,
        "all_results": all_results,
    }


# ── Уровень 2: Indicator Reliability ─────────────────────────────────────────

def _extract_indicator_states(ind: dict) -> dict[str, bool]:
    """Возвращает словарь {condition_name: True/False} для набора индикаторов."""
    states = {}
    rsi = ind.get("rsi")
    macd = ind.get("macd_diff")
    pband = ind.get("bb_pband")
    price = ind.get("price")
    ema20 = ind.get("ema20")
    ema50 = ind.get("ema50")
    ema200 = ind.get("ema200")
    adx = ind.get("adx")

    states["rsi_oversold"] = rsi is not None and rsi <= 35
    states["rsi_moderate"] = rsi is not None and 35 < rsi <= 50
    states["macd_bull"]    = macd is not None and macd > 0
    states["bb_low"]       = pband is not None and pband <= 0.25
    states["ema_bull"]     = (price and ema20 and ema50 and
                               price > ema20 and ema20 > ema50)
    states["ema_above_200"] = price is not None and ema200 is not None and price > ema200
    states["adx_trending"] = adx is not None and adx >= 20

    return states


def analyze_indicator_reliability(
    coin: str,
    interval: str = "1d",
    lookback: int = 365,
) -> Optional[dict[str, dict]]:
    """
    Детальный бэктест с отслеживанием состояний индикаторов.

    Для каждого трейда записывает какие условия были активны при входе
    и был ли трейд прибыльным. Считает win_rate per indicator condition.

    Returns: {condition_name: {wins, losses, reliability}}
    """
    from market_data.binance_rest import get_klines
    from analysis.backtest import WARMUP_BARS, MAX_HOLD_BARS, ATR_STOP_MULT, ATR_TARGET_MULT
    from analysis.backtest import _run_backtest_core
    from analysis.technical import calculate_indicators

    df = get_klines(coin, interval=interval, limit=lookback + WARMUP_BARS + 30)
    if df.empty or len(df) < WARMUP_BARS + 20:
        return None

    # Получаем оптимальные параметры если есть
    params = get_optimized_params(coin, interval)
    min_score = params.get("min_score", 20)
    atr_stop = params.get("atr_stop", ATR_STOP_MULT)
    atr_target = params.get("atr_target", ATR_TARGET_MULT)
    max_hold = params.get("max_hold", MAX_HOLD_BARS)

    # Прогон бэктеста с отслеживанием состояний
    stats: dict[str, dict] = {
        cond: {"wins": 0, "losses": 0} for cond in INDICATOR_CONDITIONS
    }

    from analysis.backtest import _calc_bar_score
    test_start = max(WARMUP_BARS, len(df) - lookback)

    in_trade = False
    entry_price = 0.0
    stop_loss   = 0.0
    target      = 0.0
    entry_states: dict[str, bool] = {}
    entry_bar   = 0

    for i in range(WARMUP_BARS, len(df)):
        bar_close = float(df["close"].iloc[i])
        bar_high  = float(df["high"].iloc[i]) if "high" in df.columns else bar_close
        bar_low   = float(df["low"].iloc[i])  if "low"  in df.columns else bar_close
        in_test   = i >= test_start

        if in_trade:
            bars_held = i - entry_bar
            outcome = None

            if bar_low <= stop_loss:
                outcome = "loss"
            elif bar_high >= target:
                outcome = "win"
            elif bars_held >= max_hold:
                outcome = "timeout"  # нейтральный — не учитываем

            if outcome in ("win", "loss"):
                if in_test:
                    for cond, was_active in entry_states.items():
                        if was_active:
                            stats[cond][outcome + "s"] += 1
                in_trade = False

            elif outcome == "timeout":
                in_trade = False

        if not in_trade and in_test:
            df_slice = df.iloc[:i + 1]
            try:
                ind = calculate_indicators(df_slice)
                score = _calc_bar_score(ind)

                if score >= min_score:
                    atr = ind.get("atr") or bar_close * 0.02
                    entry_price   = bar_close
                    stop_loss     = entry_price - atr * atr_stop
                    target        = entry_price + atr * atr_target
                    in_trade      = True
                    entry_bar     = i
                    entry_states  = _extract_indicator_states(ind)
            except Exception:
                pass

    # Считаем reliability
    result = {}
    for cond in INDICATOR_CONDITIONS:
        wins   = stats[cond]["wins"]
        losses = stats[cond]["losses"]
        total  = wins + losses
        reliability = wins / total if total > 0 else None
        result[cond] = {
            "wins":        wins,
            "losses":      losses,
            "total":       total,
            "reliability": reliability,
        }

    logger.info("Indicator reliability %s: %s", coin.upper(),
                {k: f"{v['reliability']*100:.0f}%" if v['reliability'] is not None else "—"
                 for k, v in result.items()})
    return result


# ── DB: сохранение и загрузка ─────────────────────────────────────────────────

def save_optimized_params(coin: str, interval: str, result: dict) -> None:
    """Сохраняет лучшие параметры в БД."""
    from db.database import _use_postgres, _get_pg_conn
    import sqlite3, os

    params = result["params"]
    now = datetime.now(timezone.utc).isoformat()

    try:
        if _use_postgres():
            conn = _get_pg_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO optimized_params
                  (coin, interval, min_score, atr_stop, atr_target, max_hold,
                   win_rate, avg_rr, n_trades, quality, lookback, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (coin, interval) DO UPDATE SET
                  min_score=EXCLUDED.min_score, atr_stop=EXCLUDED.atr_stop,
                  atr_target=EXCLUDED.atr_target, max_hold=EXCLUDED.max_hold,
                  win_rate=EXCLUDED.win_rate, avg_rr=EXCLUDED.avg_rr,
                  n_trades=EXCLUDED.n_trades, quality=EXCLUDED.quality,
                  lookback=EXCLUDED.lookback, updated_at=EXCLUDED.updated_at
            """, (
                coin, interval,
                params["min_score"], params["atr_stop"],
                params["atr_target"], params["max_hold"],
                result["win_rate"], result["avg_rr"],
                result["n_trades"], result["quality"],
                result["lookback"], now,
            ))
            conn.commit(); cur.close(); conn.close()
        else:
            db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO optimized_params
                  (coin, interval, min_score, atr_stop, atr_target, max_hold,
                   win_rate, avg_rr, n_trades, quality, lookback, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                coin, interval,
                params["min_score"], params["atr_stop"],
                params["atr_target"], params["max_hold"],
                result["win_rate"], result["avg_rr"],
                result["n_trades"], result["quality"],
                result["lookback"], now,
            ))
            conn.commit(); conn.close()
        logger.info("Saved optimized params for %s/%s", coin, interval)
    except Exception as e:
        logger.error("save_optimized_params: %s", e)


def save_indicator_weights(coin: str, reliability: dict[str, dict]) -> None:
    """Сохраняет reliability индикаторов в БД."""
    from db.database import _use_postgres, _get_pg_conn
    import sqlite3, os

    now = datetime.now(timezone.utc).isoformat()

    try:
        if _use_postgres():
            conn = _get_pg_conn()
            cur = conn.cursor()
            for cond, data in reliability.items():
                if data["total"] == 0:
                    continue
                adj = _reliability_to_adjustment(data["reliability"])
                cur.execute("""
                    INSERT INTO indicator_weights (coin, indicator, wins, losses, weight_adj, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (coin, indicator) DO UPDATE SET
                      wins=EXCLUDED.wins, losses=EXCLUDED.losses,
                      weight_adj=EXCLUDED.weight_adj, updated_at=EXCLUDED.updated_at
                """, (coin, cond, data["wins"], data["losses"], adj, now))
            conn.commit(); cur.close(); conn.close()
        else:
            db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            for cond, data in reliability.items():
                if data["total"] == 0:
                    continue
                adj = _reliability_to_adjustment(data["reliability"])
                cur.execute("""
                    INSERT OR REPLACE INTO indicator_weights
                      (coin, indicator, wins, losses, weight_adj, updated_at)
                    VALUES (?,?,?,?,?,?)
                """, (coin, cond, data["wins"], data["losses"], adj, now))
            conn.commit(); conn.close()
        logger.info("Saved indicator weights for %s", coin)
    except Exception as e:
        logger.error("save_indicator_weights: %s", e)


def _reliability_to_adjustment(reliability: Optional[float]) -> float:
    """
    Конвертирует reliability (0..1) в поправочный коэффициент (-5..+7).
    > 70% → +7 (увеличиваем вес)
    50-70% → +3 (небольшой бонус)
    40-50% → 0  (нейтрально)
    < 40%  → -5 (уменьшаем вес)
    None   → 0  (нет данных)
    """
    if reliability is None:
        return 0.0
    if reliability >= 0.70:
        return 7.0
    if reliability >= 0.55:
        return 3.0
    if reliability >= 0.45:
        return 0.0
    return -5.0


def get_optimized_params(coin: str, interval: str = "1d") -> dict:
    """
    Загружает лучшие параметры для монеты из БД.
    Возвращает дефолты если не оптимизировано.
    """
    from db.database import _use_postgres, _get_pg_conn
    import sqlite3, os

    defaults = {
        "min_score": 20, "atr_stop": 1.5,
        "atr_target": 2.5, "max_hold": 20,
    }
    try:
        if _use_postgres():
            conn = _get_pg_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT min_score, atr_stop, atr_target, max_hold, win_rate, avg_rr, quality, updated_at "
                "FROM optimized_params WHERE coin=%s AND interval=%s",
                (coin, interval)
            )
            row = cur.fetchone()
            cur.close(); conn.close()
        else:
            db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT min_score, atr_stop, atr_target, max_hold, win_rate, avg_rr, quality, updated_at "
                "FROM optimized_params WHERE coin=? AND interval=?",
                (coin, interval)
            )
            row = cur.fetchone()
            conn.close()

        if row:
            return {
                "min_score": row[0], "atr_stop": row[1],
                "atr_target": row[2], "max_hold": row[3],
                "win_rate": row[4], "avg_rr": row[5],
                "quality": row[6], "updated_at": row[7],
            }
    except Exception as e:
        logger.warning("get_optimized_params error: %s", e)

    return defaults


def get_learned_weights(coin: str) -> dict[str, float]:
    """
    Загружает выученные поправки к весам индикаторов из БД.
    Возвращает {condition_name: weight_adjustment}.
    Используется в _calc_setup_score() в signals.py.
    """
    from db.database import _use_postgres, _get_pg_conn
    import sqlite3, os

    defaults = {cond: 0.0 for cond in INDICATOR_CONDITIONS}
    try:
        if _use_postgres():
            conn = _get_pg_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT indicator, weight_adj FROM indicator_weights WHERE coin=%s", (coin,)
            )
            rows = cur.fetchall()
            cur.close(); conn.close()
        else:
            db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT indicator, weight_adj FROM indicator_weights WHERE coin=?", (coin,)
            )
            rows = cur.fetchall()
            conn.close()

        if rows:
            return {**defaults, **{row[0]: row[1] for row in rows}}
    except Exception as e:
        logger.warning("get_learned_weights error: %s", e)

    return defaults


# ── Полная оптимизация ────────────────────────────────────────────────────────

def full_optimize(coin: str, interval: str = "1d", lookback: int = 180) -> dict:
    """
    Запускает grid search + reliability analysis.
    Сохраняет результаты в БД.

    Returns: {"params": ..., "reliability": ..., "summary": str}
    """
    logger.info("=== Full optimize %s (%s, %dd) ===", coin.upper(), interval, lookback)

    # Уровень 1: Grid search
    grid_result = run_grid_search(coin, interval=interval, lookback=lookback)
    if grid_result:
        save_optimized_params(coin, interval, grid_result)
        params_info = (
            f"min_score={grid_result['params']['min_score']}, "
            f"SL×{grid_result['params']['atr_stop']}, "
            f"TP×{grid_result['params']['atr_target']}"
        )
        params_quality = (
            f"WR={grid_result['win_rate']*100:.1f}%, "
            f"R/R={grid_result['avg_rr']:.2f}, "
            f"Q={grid_result['quality']:.3f}"
        )
    else:
        params_info    = "недостаточно данных"
        params_quality = ""

    # Уровень 2: Indicator reliability (на большем окне)
    rel_lookback = min(lookback * 2, 365)
    reliability = analyze_indicator_reliability(coin, interval=interval, lookback=rel_lookback)
    if reliability:
        save_indicator_weights(coin, reliability)
        # Находим самый надёжный и самый ненадёжный индикаторы
        scored = [(k, v["reliability"]) for k, v in reliability.items()
                  if v["reliability"] is not None]
        if scored:
            best_ind  = max(scored, key=lambda x: x[1])
            worst_ind = min(scored, key=lambda x: x[1])
            rel_info = (
                f"Лучший: {best_ind[0]} ({best_ind[1]*100:.0f}%), "
                f"Худший: {worst_ind[0]} ({worst_ind[1]*100:.0f}%)"
            )
        else:
            rel_info = "нет данных"
    else:
        reliability = {}
        rel_info    = "нет данных"

    summary = (
        f"✅ {coin.upper()} оптимизирован\n"
        f"📐 Параметры: {params_info}\n"
        f"   {params_quality}\n"
        f"🎯 Индикаторы: {rel_info}"
    )
    logger.info("Optimize %s done: %s", coin.upper(), summary.replace("\n", " | "))

    return {
        "coin":        coin,
        "grid_result": grid_result,
        "reliability": reliability,
        "summary":     summary,
    }


def format_params_report(coin: str, interval: str = "1d") -> str:
    """Форматирует отчёт об оптимальных параметрах для Telegram."""
    from db.database import _use_postgres, _get_pg_conn
    import sqlite3, os

    params = get_optimized_params(coin, interval)
    weights = get_learned_weights(coin)

    is_optimized = "updated_at" in params

    if not is_optimized:
        return (
            f"📊 <b>Параметры {coin.upper()}</b>\n\n"
            f"⚠️ Монета ещё не оптимизирована — используются дефолтные параметры.\n"
            f"Запусти: <code>/optimize {coin}</code>"
        )

    # Сортируем индикаторы по weight_adj
    ind_lines = []
    for cond in INDICATOR_CONDITIONS:
        adj = weights.get(cond, 0.0)
        # Загружаем reliability
        try:
            if _use_postgres():
                conn = _get_pg_conn()
                cur = conn.cursor()
                cur.execute(
                    "SELECT wins, losses FROM indicator_weights WHERE coin=%s AND indicator=%s",
                    (coin, cond)
                )
                row = cur.fetchone()
                cur.close(); conn.close()
            else:
                db_path = os.getenv("SQLITE_PATH", "sokratanti.db")
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                cur.execute(
                    "SELECT wins, losses FROM indicator_weights WHERE coin=? AND indicator=?",
                    (coin, cond)
                )
                row = cur.fetchone()
                conn.close()
            wins = row[0] if row else 0
            losses = row[1] if row else 0
            total = wins + losses
            rel_str = f"{wins/(total)*100:.0f}%" if total > 0 else "—"
        except Exception:
            rel_str = "—"
            adj = 0.0

        bar = "+" if adj > 0 else ("-" if adj < 0 else " ")
        ind_lines.append(f"  {bar} {cond:<18} {rel_str}")

    ind_block = "\n".join(ind_lines)
    updated = str(params.get("updated_at", ""))[:10]

    return (
        f"📊 <b>Оптимальные параметры {coin.upper()}</b>\n"
        f"<i>Обновлено: {updated}</i>\n\n"
        f"<b>Бэктест параметры:</b>\n"
        f"  min_score:  <code>{params['min_score']}</code>\n"
        f"  ATR stop:   <code>×{params['atr_stop']}</code>\n"
        f"  ATR target: <code>×{params['atr_target']}</code>\n"
        f"  Макс. держ: <code>{params['max_hold']} баров</code>\n\n"
        f"<b>Результаты:</b>\n"
        f"  Win rate: <code>{params.get('win_rate', 0)*100:.1f}%</code>\n"
        f"  Avg R/R:  <code>{params.get('avg_rr', 0):.2f}:1</code>\n"
        f"  Quality:  <code>{params.get('quality', 0):.3f}</code>\n\n"
        f"<b>Надёжность индикаторов (+вес/-вес):</b>\n"
        f"<code>{ind_block}</code>\n"
    )
