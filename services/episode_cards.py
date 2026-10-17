"""Pure HTML formatting for the episode quality and voice selection cards."""
import re
from html import escape
from html.parser import HTMLParser

from services import ratings


class _Description(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def _paragraph(self):
        if not self.parts:
            return
        tail = ''.join(value for value, _ in self.parts[-3:])[-2:]
        missing = 2 - len(tail) + len(tail.rstrip('\n'))
        if missing:
            self.parts.append(('\n' * missing, 'paragraph'))

    def handle_starttag(self, tag, attrs):
        if tag == 'br':
            self.parts.append(('\n', 'break'))
        elif tag in ('p', 'div'):
            self._paragraph()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in ('p', 'div'):
            self._paragraph()

    def handle_data(self, data):
        self.parts.append((data, 'data'))

    def text(self):
        while self.parts and self.parts[-1][1] == 'paragraph':
            self.parts.pop()
        return ''.join(value for value, _ in self.parts)


def _description(value):
    parser = _Description()
    parser.feed(str(value or ''))
    parser.close()
    return parser.text()


def _units(value):
    return sum(2 if ord(char) > 0xFFFF else 1 for char in value)


def _clip(value, limit):
    if _units(value) <= limit:
        return value
    if limit <= 0:
        return ''
    result, used = [], 0
    for char in value:
        size = 2 if ord(char) > 0xFFFF else 1
        if used + size > limit - 1:
            break
        result.append(char)
        used += size
    return ''.join(result) + '…'


def _parts(fields, description=''):
    result = [('Код: ' + fields['code'], False), ('\n\n', False), (fields['title'], True)]
    if fields['ratings']:
        result.append(('\n' + fields['ratings'], False))
    if description:
        result.append(('\n\n' + description, False))
    season = fields['season'] if re.match(r'^сезон\b', fields['season'].lstrip(), re.IGNORECASE) else 'Сезон: ' + fields['season']
    episode = fields['episode'] if re.match(r'^серия\b', fields['episode'].lstrip(), re.IGNORECASE) else 'Серия: ' + fields['episode']
    result.append(('\n\n' + season + '\n' + episode, False))
    if fields['quality']:
        result.append(('\nКачество: ' + fields['quality'], False))
    if fields['notice']:
        result.append(('\n\n' + fields['notice'], False))
    if fields['prompt']:
        result.append(('\n\n' + fields['prompt'], False))
    return result


def selection_text(info, code, season, episode, prompt, quality=None, notice=None, limit=1024):
    """Budget plain UTF-16 text first; escape complete fragments afterwards."""
    if type(limit) is not int or limit < 1:
        raise ValueError('Лимит текста должен быть положительным целым числом.')
    caps = {'code': 32, 'title': 160, 'season': 96, 'episode': 240,
            'quality': 64, 'notice': 240, 'prompt': 160}
    fields = {'code': str(code), 'title': str(info.get('movie_title') or ''),
              'season': str(season or ''), 'episode': str(episode or ''), 'prompt': str(prompt or ''),
              'quality': str(quality or ''), 'notice': str(notice or '')}
    fields = {key: _clip(value, caps[key]) for key, value in fields.items()}
    fields['ratings'] = ratings.line(info)
    description = _description(info.get('card_description'))
    minimum_description = min(32, _units(description))
    reserve = minimum_description + (2 if description else 0)

    def length():
        return sum(_units(value) for value, _ in _parts(fields))

    # A full action/context tail takes precedence over long titles and descriptions.
    for key, minimum in (('title', 16), ('notice', 24), ('episode', 24),
                         ('season', 16), ('quality', 16), ('prompt', 24), ('code', 16)):
        excess = length() + reserve - limit
        if excess <= 0:
            break
        budget = max(minimum, _units(fields[key]) - excess)
        fields[key] = _clip(fields[key], budget)
    available = limit - length() - (2 if description else 0)
    if available < minimum_description:
        raise ValueError('Лимит текста слишком мал для названия, описания и выбора серии.')
    description = _clip(description, available) if description else ''
    return ''.join('<strong>' + escape(value) + '</strong>' if strong else escape(value)
                   for value, strong in _parts(fields, description))
