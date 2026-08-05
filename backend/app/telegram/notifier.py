import os
import logging
from telegram import Bot
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self):
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.bot = Bot(token=token) if token else None
        self.admin_id = int(os.environ.get("TELEGRAM_USER_ID", "0"))

    async def send_signal_alert(self, user_id: int, signal: dict) -> None:
        if not self.bot:
            return
        
        emoji = "⬆️" if signal.get("action") == "BUY" else "⬇️"
        text = (
            f"🚨 *NEW SIGNAL ALERT*\n\n"
            f"{emoji} *{signal.get('asset')}* — {signal.get('action')} ({signal.get('confidence', 0)}% confidence)\n"
            f"Price: ${signal.get('price', 0)}\n"
            f"Reason: {signal.get('reason', 'N/A')}"
        )
        try:
            await self.bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Failed to send signal alert: {e}")

    async def send_council_summary(self, user_id: int, result: dict) -> None:
        if not self.bot:
            return
            
        text = (
            f"🤖 *Council Cycle Complete*\n\n"
            f"Analyzed Assets: {result.get('assets_count', 0)}\n"
            f"Strong Signals: {result.get('strong_signals', 0)}\n"
            f"Consensus Duration: {result.get('duration', '0s')}"
        )
        try:
            await self.bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Failed to send council summary: {e}")

    async def broadcast_top_signals(self, signals: list[dict]) -> None:
        if not self.bot or not self.admin_id:
            return
            
        # Simplified broadcast to admin for now
        text = "📣 *Periodic Top Signals Broadcast*\n\n"
        for i, sig in enumerate(signals[:3], 1):
            text += f"{i}. {sig.get('asset')} — {sig.get('action')}\n"
            
        try:
            await self.bot.send_message(chat_id=self.admin_id, text=text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Failed to broadcast top signals: {e}")

    async def send_error_alert(self, message: str) -> None:
        if not self.bot or not self.admin_id:
            return
            
        text = f"⚠️ *SYSTEM ERROR*\n\n{message}"
        try:
            await self.bot.send_message(chat_id=self.admin_id, text=text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Failed to send error alert: {e}")
