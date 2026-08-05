import sys
from loguru import logger
from app.core.config import settings

def setup_logging():
    """Configure loguru logging based on settings."""
    logger.remove()
    
    if settings.DEBUG:
        logger.add(sys.stderr, level=settings.LOG_LEVEL, colorize=True,
                   format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")
    else:
        logger.add(sys.stderr, level=settings.LOG_LEVEL, serialize=True)

def get_logger(name: str):
    """Get a bound logger."""
    return logger.bind(name=name)
