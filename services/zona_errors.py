"""Public lookup diagnostics without signed URLs or response bodies."""

from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit


def lookup_failure(error):
    host = ''
    if isinstance(error, HTTPError):
        try:
            host = (urlsplit(error.url).hostname or '').lower()
        except ValueError:
            pass
        stage = {
            'vsw01.zonasearch.com': 'получение ссылок на видео',
            'apiw1.mzona.net': 'получение списка источников',
        }.get(host, 'чтение источника')
        return (f'Не удалось получить варианты: {stage}, HTTP {error.code}. '
                'Если код 500–599, сервер источника сообщил о внутреннем сбое. '
                'Попробуйте повторить /zona позже.'), f'http_{error.code}'
    if isinstance(error, (TimeoutError, URLError, ConnectionError)):
        return ('Не удалось связаться с источником видео: ошибка сети или время ожидания истекло. '
                'Повторите /zona позже; если ошибка остаётся, проверьте доступ к источнику с хостинга.'), 'network'
    return ('Источник не вернул поддерживаемые варианты видео. Формат ответа мог измениться '
            'или для этой страницы нет доступного MP4.'), 'unsupported_response'
