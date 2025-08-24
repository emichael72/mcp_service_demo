"""
Script:         logger.py
Author:         DevOps Team

Description:
    Simple logger class implementation.
"""

import logging
from typing import Optional


class CoreMCPLogger:
    """
    Very simplified logger implementation for terminal output.
    """

    _log_format: str = '[%(asctime)s %(levelname)-8s] %(name)-14s: %(message)s'
    _date_format: str = '%d-%m %H:%M:%S'

    def __init__(self, name: Optional[str] = None, level: int = logging.INFO):
        self._logger = logging.getLogger(name or "MCPLogger")
        if not self._logger.handlers:  # prevent duplicate handlers
            handler = logging.StreamHandler()
            formatter = logging.Formatter(self._log_format, self._date_format)
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)
        self._logger.setLevel(level)

    def debug(self, msg: str, *args, **kwargs):
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        self._logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        self._logger.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        self._logger.critical(msg, *args, **kwargs)
