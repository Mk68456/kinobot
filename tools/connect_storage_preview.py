"""Connect a Telegram account with access to Storage for browser playback."""

import asyncio
import getpass
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main():
    from telethon import TelegramClient
    from webpanel.media_preview import connection_settings
    api_id, api_hash, session = connection_settings()
    if not api_id.isdecimal() or not api_hash:
        raise SystemExit('Set STORAGE_PREVIEW_API_ID and STORAGE_PREVIEW_API_HASH first.')
    session.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(session), int(api_id), api_hash, receive_updates=False)
    try:
        await client.start(phone=lambda: input('Telegram phone number: ').strip(),
                           code_callback=lambda: getpass.getpass('Telegram login code (hidden): '),
                           password=lambda: getpass.getpass('Telegram 2FA password (hidden): '))
        if (await client.get_me()).bot:
            raise SystemExit('Use an account that is a member of Storage, not a bot.')
        await client.get_dialogs(limit=None)
        os.chmod(session, 0o600)
        print('Preview account connected. Session:', session)
    finally:
        await client.disconnect()
        if session.exists() and hasattr(os, 'getuid') and os.getuid() == 0:
            import pwd
            try:
                account = pwd.getpwnam('bot')
            except KeyError:
                pass
            else:
                os.chown(session, account.pw_uid, account.pw_gid)


if __name__ == '__main__':
    asyncio.run(main())
