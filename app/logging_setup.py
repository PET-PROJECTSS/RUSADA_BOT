import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logger(name: str, level: str, logs_dir: Path, to_console: bool = True) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level.upper())
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter("[%(asctime)s] %(levelname)s | %(name)s | %(message)s")

    if to_console:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        logger.addHandler(console)

    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        logs_dir / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
