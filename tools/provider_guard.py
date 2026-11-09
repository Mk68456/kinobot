"""Durable pauses and conservative pacing; never solves provider challenges."""
import asyncio
from contextvars import ContextVar
import json
from pathlib import Path
import re
import time
from tools.yougen_bridge import BridgeError

current = ContextVar('provider_guard', default=None)
pairing_only = ContextVar('provider_pairing_only', default=False)


class ProviderPaused(BridgeError):
    pass


class Guard:
    def __init__(self, path):
        self.path = Path(path)
        self.state = dict(until=0, manual=False, generation=0, ignored=0, challenge=0, last_action=0)
        if self.path.exists():
            self.state.update(json.loads(self.path.read_text(encoding='utf-8')))
        self.lock = asyncio.Lock()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state), encoding='utf-8')
        temporary.replace(self.path)

    def blocked(self):
        return self.state['manual'] or self.state['until'] > time.time()

    def check(self):
        if self.state['until'] > time.time() or self.state['manual'] and not pairing_only.get():
            raise ProviderPaused('Очередь приостановлена: соблюдается ограничение поставщика.')

    def restrict(self, seconds=None, message_id=0):
        if seconds is None:
            self.state['manual'] = True
            self.state['challenge'] = max(self.state['challenge'], message_id)
        else:
            self.state['until'] = max(self.state['until'], time.time() + max(1, seconds) + 1)
        self.save()

    def observe(self, message):
        if getattr(message, 'out', False):
            return
        text = getattr(message, 'raw_text', '') or ''
        number = getattr(message, 'id', 0)
        if number <= self.state['ignored']:
            return
        if re.search(r'подтверди.{0,30}не бот|прой[дт]и.{0,20}проверк|слишком (?:много|часто)|флуд|flood|spam.?block', text, re.I):
            self.restrict(message_id=number)
            self.check()

    async def pace(self):
        async with self.lock:
            self.check()
            delay = max(0, self.state['last_action'] + 3 - time.time())
            if delay:
                await asyncio.sleep(delay)
            self.check()
            self.state['last_action'] = time.time()
            self.save()

    async def sync(self, panel):
        payload = {k: self.state[k] for k in ('until', 'manual', 'generation')}
        remote = await asyncio.to_thread(panel.request, 'provider-queue', payload)
        if remote['generation'] != self.state['generation']:
            self.state['ignored'] = max(self.state['ignored'], self.state['challenge'])
        self.state.update(remote)
        self.save()


class PacedClient:
    async def __call__(self, request, *args, **kwargs):
        guard = current.get()
        if guard:
            guard.check()
            requests = request if isinstance(request, (list, tuple)) else [request]
            if any(type(item).__name__ in {
                    'GetInlineBotResultsRequest', 'SendInlineBotResultRequest',
                    'GetBotCallbackAnswerRequest', 'SendMessageRequest',
                    'SendMediaRequest', 'ForwardMessagesRequest'} for item in requests):
                await guard.pace()
        return await super().__call__(request, *args, **kwargs)


def observe(message):
    guard = current.get()
    if guard:
        guard.observe(message)
