# Sokratanti 🤖

Telegram-бот для крипто-трейдинга. Анализирует рынок и даёт торговые сигналы.

## Функции
- 📊 `/price BTC` — текущая цена
- 🎯 `/signal BTC` — сигнал BUY/SELL/HOLD (RSI + MACD + Bollinger)
- 🧠 `/sentiment BTC` — настроение рынка через AI
- 📜 `/history` — история сигналов
- ⏰ Авто-отчёты каждые N часов

**Монеты:** BTC, ETH, SOL, BNB, DOGE

## Быстрый старт

```bash
# 1. Установить зависимости
pip install -r requirements.txt

# 2. Создать .env файл
cp .env.example .env
# Заполнить .env своими ключами

# 3. Запустить бота
python run.py
```

## Структура проекта
```
sokratanti/
├── bot/          — Telegram handlers, scheduler
├── data/         — CoinGecko, CryptoPanic, Reddit API
├── analysis/     — RSI/MACD/BB, Sentiment, Signals
└── db/           — SQLite история сигналов
```

## API ключи
| Сервис | Где взять | Нужен? |
|--------|-----------|--------|
| Telegram Bot | @BotFather | ✅ Обязательно |
| OpenAI | platform.openai.com | Для AI-sentiment |
| CryptoPanic | cryptopanic.com/developers | Для новостей |
| Reddit | reddit.com/prefs/apps | Для постов |
| CoinGecko | — | Не нужен (бесплатно) |
