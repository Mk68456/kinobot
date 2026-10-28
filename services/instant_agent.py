"""Optional child process; the bot remains usable if the private agent is absent."""

import asyncio
import json
import logging
import os
from pathlib import Path
import sys

process = None
logger = logging.getLogger(__name__)


async def start():
    global process
    from data import settings
    if not settings.INSTANT_AGENT_ENABLED or not settings.INSTANT_ENABLED:
        return
    if process is not None and process.returncode is None:
        return
    path = settings.DATA_DIR / 'yougen' / 'bridge.json'
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
        # A server needs its own login/session, not the still-active Desktop auth key.
        if config.get('profile') != 'independent':
            raise ValueError('independent session required')
        session = Path(config['session']).resolve()
        if settings.DATA_DIR not in session.parents or not session.is_file():
            raise ValueError('private session missing')
        options = {'creationflags': 0x08000000} if os.name == 'nt' else {}
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'tools.yougen_agent', '--config', str(path),
            '--queue', str(settings.DATA_DIR / 'yougen' / 'remote.sqlite'), '--serve',
            cwd=str(Path(__file__).resolve().parents[1]), **options)
        logger.info('Instant agent started.')
    except Exception as error:
        logger.error('Instant agent unavailable (%s). Bot startup continues.', type(error).__name__)


async def stop():
    global process
    if process is None:
        return
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 15)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
    process = None
