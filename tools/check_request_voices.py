"""Read-only provider check, independent of Telegram and the database."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import request_voices


async def check(imdb):
    try:
        payload = await request_voices._fetch(imdb)
        from datetime import datetime, timezone
        result = request_voices.parse_voices(payload, datetime.now(timezone.utc).isoformat(timespec='seconds'))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] in ('ok', 'empty') else 1
    except Exception as error:
        print(f"Источник не вернул список озвучек ({type(error).__name__}). Ручной ввод в боте работает.")
        return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--imdb', default='tt0903747')
    args = parser.parse_args()
    if not re.fullmatch(r'tt\d{5,12}', args.imdb):
        parser.error('--imdb must be an IMDb ID, e.g. tt0903747')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(asyncio.run(check(args.imdb)))
