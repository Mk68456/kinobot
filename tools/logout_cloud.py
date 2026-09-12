"""Explicit, one-time cloud logout before switching to the local Bot API."""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def logout():
    from aiogram import Bot
    from data.config import BOT_TOKEN

    bot = Bot(BOT_TOKEN)  # Deliberately use cloud, regardless of BOT_API_BASE.
    try:
        if not await bot.log_out():
            raise RuntimeError("Cloud logout returned false")
        print("Cloud logout complete.")
    finally:
        session = await bot.get_session()
        await session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", required=True, action="store_true")
    parser.parse_args()
    try:
        asyncio.run(logout())
    except Exception as error:
        # Network exception strings may contain a URL with the bot token.
        print(f"Cloud logout failed ({type(error).__name__}).", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
