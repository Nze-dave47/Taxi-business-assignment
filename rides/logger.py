import logging
import os
from logging.handlers import RotatingFileHandler

# Configure a package logger for rides
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
LOG_DIR = os.path.join(BASE_DIR, 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'ride_actions.log')

os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger('rides')
if not logger.handlers:
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')

    fh = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding='utf-8')
    fh.setFormatter(fmt)
    fh.setLevel(logging.INFO)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)
    logger.addHandler(ch)

# Expose logger
__all__ = ['logger']
