import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import ClientSession, ClientTimeout
from data import settings
from webpanel.server import start
from webpanel.uploads import Uploads


class WebStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_start_accepts_http_connections(self):
        with (
            patch.object(settings, "WEB_HOST", "127.0.0.1"),
            patch.object(settings, "WEB_PORT", 0),
            patch.object(settings, "WEB_PUBLIC_URL", "http://127.0.0.1"),
            patch.object(Uploads, "start"),
            patch.object(Uploads, "stop", new_callable=AsyncMock),
        ):
            runner = await start(None)
            try:
                site = next(iter(runner.sites))
                port = site._server.sockets[0].getsockname()[1]
                async with ClientSession(timeout=ClientTimeout(total=5)) as client:
                    async with client.get(f"http://127.0.0.1:{port}/") as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn("KinoTime", await response.text())
                    async with client.get(f"http://127.0.0.1:{port}/static/app.js") as response:
                        self.assertEqual(response.status, 200)
            finally:
                await runner.cleanup()
