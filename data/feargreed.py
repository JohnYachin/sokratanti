"""
Fear & Greed Index от alternative.me — бесплатно, без ключа.
Значение от 0 (Extreme Fear) до 100 (Extreme Greed).
"""
import logging
import requests

logger = logging.getLogger(__name__)

API_URL = "https://api.alternative.me/fng/?limit=1"


def get_fear_greed() -> dict:
    """
    Возвращает текущий Fear & Greed Index.
    {
        'value': 72,
        'label': 'Greed',
        'label_ru': 'Жадность',
        'emoji': '🟢',
        'score': +1  # очки для сигнального движка
    }
    """
    try:
        r = requests.get(API_URL, timeout=10)
        r.raise_for_status()
        data = r.json()["data"][0]
        value = int(data["value"])
        label = data["value_classification"]

        label_ru, emoji, score = _interpret(value)

        return {
            "value": value,
            "label": label,
            "label_ru": label_ru,
            "emoji": emoji,
            "score": score,
        }
    except Exception as e:
        logger.error("Fear & Greed API error: %s", e)
        return {
            "value": 50,
            "label": "Neutral",
            "label_ru": "Нейтральный",
            "emoji": "🟡",
            "score": 0,
        }


def _interpret(value: int) -> tuple[str, str, int]:
    if value <= 20:
        return "Экстремальный страх", "😱", +2   # сигнал покупки (дно)
    elif value <= 40:
        return "Страх", "😨", +1
    elif value <= 60:
        return "Нейтральный", "😐", 0
    elif value <= 80:
        return "Жадность", "😏", -1
    else:
        return "Экстремальная жадность", "🤑", -2  # сигнал продажи (пик)
