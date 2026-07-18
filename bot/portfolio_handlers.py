"""
bot/portfolio_handlers.py — Команды управления портфелем.

Команды:
  /portfolio        — показать все позиции с P&L + сигналами
  /add BTC 0.5 60000 [заметка]  — добавить позицию
  /remove BTC       — удалить все позиции по монете
  /remove_id 3      — удалить конкретную запись по ID

Формат /add:  /add <монета> <количество> <цена_покупки> [заметка]
Пример:       /add btc 0.1 63500
              /add eth 2.5 1800 "лонг на пробой"
"""
import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from bot.handlers import auth_required
from db.database import (
    portfolio_add, portfolio_remove, portfolio_get_all,
    portfolio_remove_by_coin,
)

logger = logging.getLogger(__name__)


def _fmt_price(v: float) -> str:
    """Форматирует цену с правильным количеством знаков."""
    if v is None:
        return "—"
    if v >= 1000:
        return f"${v:,.2f}"
    elif v >= 1:
        return f"${v:.4f}"
    else:
        return f"${v:.6f}"


def _fmt_qty(qty: float) -> str:
    """Форматирует количество монет."""
    if qty == int(qty):
        return str(int(qty))
    elif qty >= 0.01:
        return f"{qty:.4f}"
    else:
        return f"{qty:.8f}"


# ── /add ──────────────────────────────────────────────────────────────────────
@auth_required
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /add BTC 0.5 60000 [заметка]
    Добавляет позицию в портфель.
    """
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "❓ Формат: <code>/add МОНЕТА КОЛИЧЕСТВО ЦЕНА_ПОКУПКИ [заметка]</code>\n"
            "Пример: <code>/add BTC 0.1 63500</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    coin = args[0].lower()
    try:
        quantity  = float(args[1].replace(",", "."))
        buy_price = float(args[2].replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат числа. Пример: <code>/add ETH 2.5 1800</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    notes = " ".join(args[3:]) if len(args) > 3 else ""

    if quantity <= 0 or buy_price <= 0:
        await update.message.reply_text("❌ Количество и цена должны быть положительными.")
        return

    entry_id = await asyncio.to_thread(portfolio_add, coin, quantity, buy_price, notes)
    if entry_id < 0:
        await update.message.reply_text("❌ Ошибка при сохранении позиции.")
        return

    cost = quantity * buy_price
    notes_line = f"\n📝 <i>{notes}</i>" if notes else ""
    text = (
        f"✅ <b>Позиция добавлена</b> (ID: {entry_id})\n\n"
        f"📦 {coin.upper()} × {_fmt_qty(quantity)}\n"
        f"💰 Цена входа: {_fmt_price(buy_price)}\n"
        f"💵 Стоимость:  {_fmt_price(cost)}"
        f"{notes_line}\n\n"
        f"👉 /portfolio — посмотреть весь портфель"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /remove ───────────────────────────────────────────────────────────────────
@auth_required
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /remove BTC — удаляет все позиции по монете.
    """
    args = context.args
    if not args:
        await update.message.reply_text(
            "❓ Формат: <code>/remove МОНЕТА</code>\n"
            "Пример: <code>/remove BTC</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    coin = args[0].lower()
    count = await asyncio.to_thread(portfolio_remove_by_coin, coin)

    if count == 0:
        await update.message.reply_text(
            f"⚠️ Нет позиций по <b>{coin.upper()}</b> в портфеле.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            f"🗑️ Удалено {count} позиций по <b>{coin.upper()}</b>.",
            parse_mode=ParseMode.HTML,
        )


# ── /remove_id ────────────────────────────────────────────────────────────────
@auth_required
async def cmd_remove_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/remove_id 3 — удаляет конкретную запись по ID."""
    args = context.args
    if not args:
        await update.message.reply_text("❓ Формат: <code>/remove_id ID</code>", parse_mode=ParseMode.HTML)
        return
    try:
        entry_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом.", parse_mode=ParseMode.HTML)
        return

    ok = await asyncio.to_thread(portfolio_remove, entry_id)
    if ok:
        await update.message.reply_text(f"🗑️ Запись #{entry_id} удалена.")
    else:
        await update.message.reply_text(f"⚠️ Запись #{entry_id} не найдена.")


# ── /portfolio ────────────────────────────────────────────────────────────────
@auth_required
async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Показывает все позиции с текущим P&L и сигналами.
    """
    msg = await update.message.reply_text(
        "⏳ Считаю P&L и получаю сигналы...", parse_mode=ParseMode.HTML
    )
    try:
        positions = await asyncio.to_thread(portfolio_get_all)

        if not positions:
            await msg.edit_text(
                "📭 Портфель пуст.\n\n"
                "Добавь позицию: <code>/add BTC 0.1 63500</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        # Получаем текущие цены + сигналы конкурентно по уникальным монетам
        from data.coingecko import get_price
        from analysis.signals import generate_signal

        unique_coins = list({p["coin"] for p in positions})
        price_tasks  = [asyncio.to_thread(get_price, c) for c in unique_coins]
        signal_tasks = [asyncio.to_thread(generate_signal, c) for c in unique_coins]

        price_results  = await asyncio.gather(*price_tasks,  return_exceptions=True)
        signal_results = await asyncio.gather(*signal_tasks, return_exceptions=True)

        # Словари: coin → данные
        prices  = {}
        signals = {}
        for coin, pr, sr in zip(unique_coins, price_results, signal_results):
            if not isinstance(pr, Exception):
                prices[coin]  = pr
            if not isinstance(sr, Exception):
                signals[coin] = sr

        # ── Считаем итоги ────────────────────────────────────────────────────
        total_cost    = 0.0
        total_value   = 0.0
        coin_sections = []

        # Группируем позиции по монете
        coins_grouped: dict[str, list] = {}
        for p in positions:
            coins_grouped.setdefault(p["coin"], []).append(p)

        for coin, pos_list in sorted(coins_grouped.items()):
            pd_ = prices.get(coin, {})
            cur_price = pd_.get("price_usd")
            sig = signals.get(coin, {})

            # Суммируем по монете
            total_qty   = sum(p["quantity"]  for p in pos_list)
            total_spent = sum(p["quantity"] * p["buy_price"] for p in pos_list)
            avg_price   = total_spent / total_qty if total_qty > 0 else 0

            cur_value = total_qty * cur_price if cur_price else None
            pnl_abs   = (cur_value - total_spent) if cur_value is not None else None
            pnl_pct   = (pnl_abs / total_spent * 100) if (pnl_abs is not None and total_spent > 0) else None

            if cur_value is not None:
                total_cost  += total_spent
                total_value += cur_value

            pnl_emoji = ""
            if pnl_pct is not None:
                pnl_emoji = "📈" if pnl_pct >= 0 else "📉"

            # Блок монеты
            if cur_price:
                price_line = (
                    f"  💵 {_fmt_price(avg_price)} → {_fmt_price(cur_price)}\n"
                    f"  {pnl_emoji} P&L: "
                )
                if pnl_abs is not None:
                    sign = "+" if pnl_abs >= 0 else ""
                    price_line += (
                        f"<code>{sign}{_fmt_price(abs(pnl_abs))[1:]} ({sign}{pnl_pct:.1f}%)</code>\n"
                    ).replace(sign + _fmt_price(abs(pnl_abs))[1:], ('+' if pnl_abs >= 0 else '-') + _fmt_price(abs(pnl_abs))[1:])
                else:
                    price_line += "—\n"
            else:
                price_line = f"  💵 Куплено: {_fmt_price(avg_price)} (цена недоступна)\n"

            # Сигнал
            sig_line = ""
            if sig:
                setup_score = sig.get("setup_score", 0)
                label_ru    = sig.get("label_ru", sig.get("signal", ""))
                sig_emoji   = sig.get("emoji", "")
                filled = round(setup_score / 20)
                bar = "█" * filled + "░" * (5 - filled)
                sig_line = f"  📊 {sig_emoji} {label_ru} <code>[{bar}]</code> {setup_score}/100\n"

            # Записи если несколько лотов
            lots_line = ""
            if len(pos_list) > 1:
                lots = []
                for p in pos_list:
                    lots.append(f"    #{p['id']} {_fmt_qty(p['quantity'])} @ {_fmt_price(p['buy_price'])}")
                lots_line = "\n".join(lots) + "\n"

            section = (
                f"\n<b>{coin.upper()}</b> × {_fmt_qty(total_qty)}\n"
                f"{price_line}"
                f"{sig_line}"
                f"{lots_line}"
            )
            coin_sections.append(section)

        # ── Итоговая строка ──────────────────────────────────────────────────
        if total_cost > 0 and total_value > 0:
            total_pnl = total_value - total_cost
            total_pnl_pct = total_pnl / total_cost * 100
            sign = "+" if total_pnl >= 0 else ""
            total_line = (
                f"\n━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>Итого:</b>\n"
                f"  Вложено: <code>{_fmt_price(total_cost)}</code>\n"
                f"  Сейчас:  <code>{_fmt_price(total_value)}</code>\n"
                f"  P&L:     <code>{sign}{_fmt_price(abs(total_pnl))[1:]} ({sign}{total_pnl_pct:.1f}%)</code>"
            )
        else:
            total_line = ""

        header = "📦 <b>Мой портфель</b>\n"
        full_text = header + "".join(coin_sections) + total_line
        full_text += "\n\n<i>/add МОНЕТА КОЛ-ВО ЦЕНА — добавить позицию</i>"

        await msg.edit_text(full_text, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error("portfolio error: %s", e)
        await msg.edit_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)
