"""
Logging configuration for the Axiom Monitor.
"""

import logging
from logging.handlers import RotatingFileHandler


def setup_logging() -> logging.Logger:
    """
    Configure logging with console and file handlers.

    Returns:
        Configured logger instance
    """
    # Create consistent formatter for all handlers
    log_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Console handler: Show all INFO+ messages
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_formatter)

    # File handler: Only WARNING and ERROR, with rotation to prevent disk overflow
    file_handler = RotatingFileHandler(
        "axiom_monitor.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB per file
        backupCount=5,  # Keep 5 backup files
    )
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(log_formatter)

    # Configure root logger with both handlers
    logging.basicConfig(
        level=logging.INFO,
        handlers=[console_handler, file_handler],
    )

    return logging.getLogger(__name__)
