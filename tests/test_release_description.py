import tempfile
import unittest
from pathlib import Path

from tools.release_description import assess, parse_description
from tools.release_assistant_ui import queue_source


ORVILLE = '''Скачать The.Orville.S02.1080p.WEBRip.Profix.Media.torrent
Название: Орвилл
Оригинальное название: The Orville
Год выхода: 2019
Продолжительность: 14 x ~00:48:00
Перевод: Профессиональный многоголосый / Студия "Profix Media"
Внимание, реклама: Присутствует реклама
Формат: Matroska
Качество: WEBRip 1080p
Видео: MPEG-4 AVC, ~ 4293 Кбит/с, 1920x1080
Звук: Русский (AAC, 2 ch, 223 Кбит/с)
Размер 22.42 GB (24071650872 Bytes)'''


class DescriptionTests(unittest.TestCase):
    def test_screenshot_claims_and_evidence(self):
        parsed = parse_description(ORVILLE)
        self.assertEqual(parsed.value('title'), 'Орвилл')
        self.assertEqual(parsed.value('season'), '2')
        self.assertEqual(parsed.value('resolution'), '1080p')
        self.assertEqual(parsed.value('dimensions'), '1920×1080')
        self.assertEqual(parsed.value('quality'), 'WEBRIP')
        self.assertEqual(parsed.value('container'), 'MKV')
        self.assertEqual(parsed.value('video_codec'), 'H.264 / AVC')
        self.assertEqual(parsed.size_bytes, 24071650872)
        self.assertIn('Profix Media', parsed.value('translation'))
        self.assertEqual(parsed.value('advertising'), 'Заявлено наличие')
        self.assertIn('14 (по продолжительности)', parsed.value('episodes'))
        self.assertEqual(len(parsed.facts['quality'][0].evidence), 2)
        self.assertTrue(any('списком файлов' in warning for warning in parsed.warnings))
        self.assertIn('Внимание, реклама: Присутствует реклама', parsed.report())

    def test_html_and_split_labels_do_not_include_scripts(self):
        parsed = parse_description('''<script>Качество: CAMRip</script>
        <table><tr><td><b>Название:</b></td><td>Тест &amp; кино</td></tr>
        <tr><td>Качество:</td><td>WEB-DL 720p</td></tr></table>
        <p>Перевод:</p><p>Студия А</p><br>Реклама: нет''')
        self.assertEqual(parsed.value('title'), 'Тест & кино')
        self.assertEqual(parsed.value('quality'), 'WEBDL')
        self.assertEqual(parsed.value('translation'), 'Студия А')
        self.assertNotIn('CAMRip', parsed.text)
        self.assertEqual(parsed.value('advertising'), 'Заявлено отсутствие')

    def test_bbcode_dashes_comma_size_and_multiple_audio(self):
        parsed = parse_description('''[b]Название[/b] — Пример
        Качество - BDRip 720p
        Размер: 1,5 ГиБ
        Перевод — Студия А, Студия Б
        Аудио #1: Русский AC3
        Audio 2: English DTS
        Без рекламы''')
        self.assertEqual(parsed.value('title'), 'Пример')
        self.assertEqual(parsed.value('quality'), 'BDRIP')
        self.assertEqual(parsed.size_bytes, 1610612736)
        self.assertEqual(len(parsed.facts['audio']), 2)
        self.assertEqual(assess(parsed, 'Студия А', 2), [])

    def test_unknown_is_never_absence_or_inferred_voice(self):
        parsed = parse_description('Example.S01.1080p.WEBRip.SomeStudio\nОписание: без подробностей')
        self.assertNotIn('translation', parsed.facts)
        self.assertNotIn('advertising', parsed.facts)
        self.assertIsNone(parsed.size_bytes)
        self.assertIn('Отсутствие рекламы не подтверждено', assess(parsed))
        self.assertIn('Нужная озвучка не подтверждена', assess(parsed, 'SomeStudio'))

    def test_prose_is_not_video_metadata(self):
        parsed = parse_description('Название: Тест\nО фильме: герой нашёл WEBRip 1080p H.265 и 20 GB')
        for key in ('quality', 'resolution', 'video_codec', 'size'):
            self.assertNotIn(key, parsed.facts)

    def test_conflicting_claims_are_kept(self):
        parsed = parse_description('''Test.1080p.WEB-DL
        Качество: BDRip 720p
        Видео: HEVC 1920x800
        Реклама: нет
        Внимание: есть реклама
        Размер: 2 GB
        Размер: 3 GB''')
        self.assertEqual(len(parsed.facts['quality']), 2)
        self.assertEqual(len(parsed.facts['resolution']), 2)
        self.assertEqual(len(parsed.facts['advertising']), 2)
        self.assertIn('Заявлена реклама', assess(parsed))
        self.assertIn('Размер требует проверки', assess(parsed, max_gib=10))
        self.assertTrue(any('размер кадра различаются' in warning for warning in parsed.warnings))

    def test_dimensions_do_not_imply_progressive_scan(self):
        parsed = parse_description('Видео: H.264, 1920×1080')
        self.assertEqual(parsed.value('dimensions'), '1920×1080')
        self.assertNotIn('resolution', parsed.facts)

    def test_size_limit_is_whole_release_not_per_episode(self):
        parsed = parse_description(ORVILLE)
        self.assertIn('Размер выше лимита исходника', assess(parsed, max_gib=2))
        self.assertNotIn('Размер выше лимита исходника', assess(parsed, max_gib=30))

    def test_season_and_episode_range(self):
        parsed = parse_description('Название: Пример\nСезон: 3\nСерии: 01-08 из 10')
        self.assertEqual(parsed.value('season'), '3')
        self.assertEqual(parsed.value('episodes'), '01-08 из 10')
        self.assertIn('списком файлов', parsed.report())

    def test_invalid_inputs(self):
        for value in ('', '   ', None, 'a' * 150_001, '<script>nothing</script>'):
            with self.subTest(value=str(value)[:12]), self.assertRaises(ValueError):
                parse_description(value)

    def test_empty_translation_does_not_consume_next_field(self):
        parsed = parse_description('Название: Тест\nПеревод:\nКачество: WEBRip 1080p')
        self.assertNotIn('translation', parsed.facts)
        self.assertEqual(parsed.value('quality'), 'WEBRIP')

    def test_multiline_translations_language_labels_and_episode_list(self):
        parsed = parse_description('''Релиз: Пример S03 1080р WEB-DL
        Перевод: Профессиональный | Студия А
        Профессиональный | Студия Б
        Файл
        Аудио Rus: AAC | Студия А
        Аудио Eng: AC3
        Список серий
        01. Первая
        02. Вторая
        Размер: 2 GiB''')
        self.assertEqual(parsed.value('resolution'), '1080p')
        self.assertIn('Студия Б', parsed.value('translation'))
        self.assertEqual(len(parsed.facts['audio']), 2)
        self.assertEqual(parsed.value('episodes'), '2 (по списку серий)')
        self.assertEqual(parsed.size_bytes, 2 * 1024**3)

    def test_source_validation_does_not_accept_page_or_remote_torrent(self):
        self.assertEqual(queue_source(' magnet:?xt=urn:btih:abc '), 'magnet:?xt=urn:btih:abc')
        for source in ('https://example.org/page', 'https://example.org/file.torrent',
                       'magnet:?xt=a\nmagnet:?xt=b', 'missing.torrent', ''):
            with self.subTest(source=source), self.assertRaises(ValueError):
                queue_source(source)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'Тест.torrent'
            source.write_bytes(b'test')
            self.assertEqual(queue_source(str(source)), str(source))


if __name__ == '__main__':
    unittest.main()
