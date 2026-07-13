"""
Получение постов с Reddit через PRAW.
Приложение: https://www.reddit.com/prefs/apps
"""
import os
import logging

logger = logging.getLogger(__name__)

SUBREDDITS: dict[str, list[str]] = {
    "btc": ["Bitcoin", "CryptoCurrency"],
    "eth": ["ethereum", "CryptoCurrency"],
    "sol": ["solana", "CryptoCurrency"],
    "bnb": ["binance", "CryptoCurrency"],
    "doge": ["dogecoin", "CryptoCurrency"],
}


def get_reddit_posts(coin: str, limit: int = 5) -> list[dict]:
    """
    Возвращает горячие посты из связанных сабреддитов.
    Если ключей нет — возвращает [] без ошибки.
    """
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        logger.warning("REDDIT_CLIENT_ID/SECRET не заданы — Reddit недоступен.")
        return []

    try:
        import praw
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=os.getenv("REDDIT_USER_AGENT", "Sokratanti/1.0"),
        )
        subs = SUBREDDITS.get(coin.lower(), ["CryptoCurrency"])
        subreddit = reddit.subreddit("+".join(subs))
        posts = []
        for post in subreddit.hot(limit=limit):
            posts.append({
                "title": post.title,
                "score": post.score,
                "url": f"https://reddit.com{post.permalink}",
            })
        return posts
    except Exception as e:
        logger.error("Reddit error: %s", e)
        return []
