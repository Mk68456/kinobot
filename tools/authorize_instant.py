"""Owner-run interactive authorization. The assistant must not enter OTP/2FA for you."""

import argparse
import asyncio
from getpass import getpass
import json
import os
from pathlib import Path
import uuid


async def authorize(args):
    from telethon import TelegramClient
    path = args.session.resolve()
    if path.exists():
        raise ValueError('Файл сессии уже существует. Перезапись запрещена.')
    destination = path.parent / 'bridge.json'
    if destination.exists():
        raise ValueError('Конфигурация уже существует. Перезапись запрещена.')
    api_id = int(os.environ.get('TELEGRAM_API_ID') or input('Telegram API ID: '))
    api_hash = os.environ.get('TELEGRAM_API_HASH') or getpass('Telegram API hash: ')
    path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(path), api_id, api_hash)
    try:
        # Telethon's interactive prompts are answered by the account owner, not an agent.
        await client.start(phone=lambda: input('Номер телефона владельца: '),
                           password=lambda: getpass('Пароль двухэтапной проверки: '))
        me = await client.get_me()
        if me.bot:
            raise ValueError('Нужна пользовательская учётная запись.')
        config = {'profile': 'independent', 'session': '/app/data/yougen/provider.session',
                  'api_id': api_id, 'api_hash': api_hash, 'user_id': me.id,
                  'target': '@kinomobi_bot', 'panel_url': args.panel,
                  'panel_token': getpass('Отдельный код из /desktop: '),
                  'instance_id': 'yougen-server-' + uuid.uuid4().hex,
                  'link_existing_catalog': True}
        destination.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Отдельная сессия и приватная конфигурация сохранены рядом. Не добавляйте их в Git.')
    finally:
        await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, default=Path('runtime/yougen-server/provider.session'))
    parser.add_argument('--panel', required=True)
    args = parser.parse_args()
    try:
        asyncio.run(authorize(args))
    except Exception as error:
        print('Вход остановлен: ' + type(error).__name__)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
