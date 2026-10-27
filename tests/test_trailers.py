import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from services import trailers


@asynccontextmanager
async def fake_session():
    yield object()


class TrailerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        trailers._cache.clear()
        self.key_patch = patch("services.tmdb.TMDB_API_KEY", "offline-key")
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)

    async def test_series_uses_tv_endpoint_and_english_fallback_and_cache(self):
        candidate = {"tmdb_id": 12, "title": "Show", "year": "2013"}
        video = {"type": "Trailer", "site": "YouTube", "key": "abcdefghijk"}
        response = AsyncMock(side_effect=[{"results": []}, {"results": [video]}])
        with (
            patch("services.tmdb.search", AsyncMock(return_value=[candidate])) as search,
            patch("services.tmdb._new_session", fake_session),
            patch("services.tmdb._get_json", response),
        ):
            url = await trailers.find_trailer("Show (2013)", "series")
            self.assertEqual(url, "https://www.youtube.com/watch?v=abcdefghijk")
            self.assertIn("/tv/12/videos", response.call_args.args[1])
            self.assertEqual(response.call_args.args[2]["language"], "en-US")
            self.assertEqual(await trailers.find_trailer("Show (2013)", "series"), url)
            search.assert_awaited_once_with("Show", "series", limit=20)

    async def test_russian_movie_trailer_is_preferred(self):
        candidate = {"tmdb_id": 4, "title": "Movie", "year": "2020"}
        video = {"type": "Trailer", "site": "YouTube", "key": "abcdefghijk"}
        response = AsyncMock(return_value={"results": [video]})
        with (
            patch("services.tmdb.search", AsyncMock(return_value=[candidate])),
            patch("services.tmdb._new_session", fake_session),
            patch("services.tmdb._get_json", response),
        ):
            self.assertIsNotNone(await trailers.find_trailer("Movie", "movie"))
            self.assertIn("/movie/4/videos", response.call_args.args[1])
            self.assertEqual(response.call_args.args[2]["language"], "ru-RU")
            response.assert_awaited_once()

    async def test_ambiguous_titles_are_not_guessed(self):
        results = [
            {"tmdb_id": 1, "title": "Movie", "year": "1990"},
            {"tmdb_id": 2, "title": "Movie", "year": "2020"},
        ]
        with patch("services.tmdb.search", AsyncMock(return_value=results)):
            self.assertIsNone(await trailers.find_trailer("Movie"))

    async def test_network_failure_does_not_escape_or_cache(self):
        with patch("services.trailers._find", AsyncMock(side_effect=RuntimeError("offline"))):
            self.assertIsNone(await trailers.find_trailer("Movie"))
            self.assertFalse(trailers._cache)

    def test_only_valid_trailers_are_linked(self):
        self.assertIsNone(
            trailers._video_url(
                [
                    {"type": "Clip", "site": "YouTube", "key": "abcdefghijk"},
                    {"type": "Trailer", "site": "YouTube", "key": "bad&url=evil"},
                ]
            )
        )
