"""Environment-only configuration for the Bothost image (no embedded secrets)."""

import os
import re

BOT_TOKEN = (os.environ.get('BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN') or '').strip()
if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', BOT_TOKEN):
    raise ValueError('Добавьте BOT_TOKEN в переменные окружения Bothost.')

_admins = os.environ.get('ADMIN_IDS', '').strip()
if not re.fullmatch(r'[1-9][0-9]*(?:[ ,;]+[1-9][0-9]*)*', _admins):
    raise ValueError('Задайте ADMIN_IDS: числовые Telegram ID администраторов через запятую.')
ADMIN = [int(value) for value in re.split(r'[ ,;]+', _admins)]
