"""Selection cards preserve context and safe HTML within Telegram text budgets."""
import unittest
from html.parser import HTMLParser

from services.episode_cards import selection_text


class VisibleText(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.tags = []
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))

    def handle_data(self, data):
        self.text.append(data)

    @property
    def value(self):
        return ''.join(self.text)


def utf16(value):
    return len(value.encode('utf-16-le')) // 2


class EpisodeCardTextTests(unittest.TestCase):
    def info(self, **changes):
        return {'movie_title': 'Друзьяшки Улыбашки', 'card_description': 'Описание сериала.',
                'rating_kp': 8.2, 'rating_imdb': 8.5, 'age_rating': '18+', **changes}

    def card(self, info=None, **changes):
        arguments = {'code': 1010, 'season': 'Сезон 3', 'episode': 'Серия 3 Mole Man',
                     'prompt': 'Выберите озвучку:', 'quality': '720p'}
        arguments.update(changes)
        return selection_text(info or self.info(), **arguments)

    def test_normal_card_retains_title_ratings_description_and_context(self):
        text = self.card()
        self.assertEqual(VisibleText(text).value,
            'Код: 1010\n\nДрузьяшки Улыбашки\nКиноПоиск: 8,2 · IMDb: 8,5 · Возраст: 18+'
            '\n\nОписание сериала.\n\nСезон 3\nСерия 3 Mole Man\nКачество: 720p\n\nВыберите озвучку:')
        self.assertIn('<strong>Друзьяшки Улыбашки</strong>', text)

    def test_qualities_and_voices_share_full_card_description(self):
        for quality, prompt in ((None, 'Выберите качество:'), ('Исходное качество', 'Выберите озвучку:')):
            with self.subTest(quality=quality):
                visible = VisibleText(self.card(quality=quality, prompt=prompt)).value
                self.assertIn('Описание сериала.', visible)
                self.assertIn('Друзьяшки Улыбашки', visible)
                self.assertTrue(visible.endswith(prompt))
                if quality:
                    self.assertIn('Качество: Исходное качество', visible)
                else:
                    self.assertNotIn('Качество:', visible)

    def test_description_markup_is_stripped_entities_decoded_and_paragraphs_preserved(self):
        description = '<p><strong>Первый &amp; второй</strong><br>Строка &lt; третья</p><div>Абзац</div>'
        text = self.card(self.info(card_description=description))
        visible = VisibleText(text)
        self.assertIn('Первый & второй\nСтрока < третья\n\nАбзац', visible.value)
        self.assertEqual(visible.tags, [('strong', [])])
        self.assertNotIn('<p>', text)
        self.assertNotIn('<div>', text)

    def test_unsafe_field_values_are_escaped_as_text_and_no_links_or_images_survive(self):
        info = self.info(movie_title='<script>x & "y"</script>',
                         card_description='<a href="https://example.invalid">Ссылка</a><img src="secret">Текст')
        text = self.card(info, season='<img src="x">', episode='A < B & C',
                         prompt='<b>Выберите</b>', quality='720p < x', notice='<a>Удалено</a>')
        visible = VisibleText(text)
        self.assertEqual(visible.tags, [('strong', [])])
        self.assertIn('<script>x & "y"</script>', visible.value)
        self.assertIn('СсылкаТекст', visible.value)
        self.assertIn('Серия: A < B & C', visible.value)
        self.assertIn('<b>Выберите</b>', visible.value)
        self.assertIn('<a>Удалено</a>', visible.value)

    def test_literal_less_than_and_malformed_description_remain_safe(self):
        text = self.card(self.info(card_description='Один < два & три <'))
        visible = VisibleText(text)
        self.assertIn('Один < два & три <', visible.value)
        self.assertEqual(visible.tags, [('strong', [])])

    def test_long_description_is_clipped_by_visible_utf16_not_markup_or_entities(self):
        text = self.card(self.info(card_description='&lt;' * 3000))
        visible = VisibleText(text).value
        self.assertLessEqual(utf16(visible), 1024)
        self.assertEqual(utf16(visible), 1024)
        self.assertIn('<' * 100, visible)
        self.assertIn('…\n\nСезон 3', visible)
        self.assertTrue(visible.endswith('Выберите озвучку:'))
        self.assertGreater(len(text), 1024)

    def test_caption_keeps_nonempty_description_and_all_context_even_when_every_field_is_huge(self):
        info = self.info(movie_title='Название ' * 2000, card_description='Описание ' * 2000)
        text = self.card(info, season='Сезон ' * 1000, episode='Серия ' * 1000,
                         quality='1080p ' * 1000, notice='Предыдущий вариант удалён. ' * 1000,
                         prompt='Выберите озвучку: ' * 1000)
        visible = VisibleText(text).value
        self.assertLessEqual(utf16(visible), 1024)
        self.assertIn('Описание ', visible)
        self.assertIn('\n\nСезон ', visible)
        self.assertIn('\nСерия ', visible)
        self.assertIn('Качество: 1080p ', visible)
        self.assertIn('Предыдущий вариант удалён.', visible)
        self.assertIn('Выберите озвучку:', visible)
        self.assertTrue(visible.endswith('…'))

    def test_astral_emoji_are_counted_twice_and_not_split_at_truncation(self):
        text = self.card(self.info(movie_title='🎬' * 300, card_description='🌟' * 3000))
        visible = VisibleText(text).value
        self.assertLessEqual(utf16(visible), 1024)
        self.assertGreater(visible.count('🌟'), 100)
        self.assertTrue(visible.endswith('Выберите озвучку:'))
        self.assertNotIn('\ufffd', visible)
        text.encode('utf-8')

    def test_4096_text_fallback_can_show_more_description_and_retains_notice(self):
        info = self.info(card_description='Развёрнутое описание. ' * 500)
        caption = VisibleText(self.card(info, notice='Предыдущий вариант удалён.', limit=1024)).value
        message = VisibleText(self.card(info, notice='Предыдущий вариант удалён.', limit=4096)).value
        self.assertLessEqual(utf16(message), 4096)
        self.assertGreater(message.count('Развёрнутое'), caption.count('Развёрнутое'))
        self.assertIn('Предыдущий вариант удалён.', caption)
        self.assertIn('Предыдущий вариант удалён.', message)
        self.assertTrue(message.endswith('Выберите озвучку:'))

    def test_empty_description_ratings_and_optional_fields_do_not_add_fake_values(self):
        text = self.card(self.info(card_description='', rating_kp=None, rating_imdb=None, age_rating=None),
                         quality=None, notice=None)
        visible = VisibleText(text).value
        self.assertNotIn('КиноПоиск', visible)
        self.assertNotIn('IMDb', visible)
        self.assertNotIn('Возраст:', visible)
        self.assertNotIn('Качество:', visible)
        self.assertNotIn('None', visible)

    def test_description_normal_whitespace_and_explicit_breaks_are_not_lost(self):
        description = '  Строка один\nСтрока два  '
        self.assertIn(description, VisibleText(self.card(self.info(card_description=description))).value)
        self.assertIn('\nОписание\n', VisibleText(self.card(self.info(card_description='<br>Описание<br>'))).value)

    def test_invalid_or_impossibly_small_limits_are_explicit_errors(self):
        for limit in (True, None, 0, -1, 1, 20):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.card(limit=limit)

    def test_existing_season_episode_prefixes_are_not_duplicated_but_short_values_get_labels(self):
        plain = VisibleText(self.card(season='3', episode='Mr. Frog')).value
        self.assertIn('Сезон: 3\nСерия: Mr. Frog', plain)
        named = VisibleText(self.card(season='сезон 3', episode='СЕРИЯ 7 Mr. Frog')).value
        self.assertIn('сезон 3\nСЕРИЯ 7 Mr. Frog', named)
        self.assertNotIn('Сезон: сезон', named)
        self.assertNotIn('Серия: СЕРИЯ', named)

    def test_cyrillic_angle_bracket_text_is_preserved_in_title_and_description(self):
        text = self.card(self.info(movie_title='<Название>', card_description='Описание <не тег> и <Название>'))
        visible = VisibleText(text)
        self.assertIn('<Название>', visible.value)
        self.assertIn('Описание <не тег> и <Название>', visible.value)
        self.assertEqual(visible.tags, [('strong', [])])


if __name__ == '__main__':
    unittest.main()
