import math


def normalize(body):
    result = {}
    for key in ('rating_kp', 'rating_imdb'):
        value = body.get(key)
        if value is None or isinstance(value, str) and not value.strip():
            result[key] = None
            continue
        try:
            if isinstance(value, bool):
                raise ValueError()
            value = float(str(value).strip().replace(',', '.'))
        except (ValueError, TypeError):
            raise ValueError('Рейтинг должен быть числом от 0 до 10.') from None
        if not math.isfinite(value) or not 0 <= value <= 10:
            raise ValueError('Рейтинг должен быть числом от 0 до 10.')
        result[key] = value
    age = body.get('age_rating') or None
    if age not in (None, '0+', '6+', '12+', '16+', '18+'):
        raise ValueError('Выберите возрастную маркировку из списка.')
    result['age_rating'] = age
    return result


def line(info):
    values = normalize(info)
    parts = []
    for key, label in (('rating_kp', 'КиноПоиск'), ('rating_imdb', 'IMDb')):
        if values[key] is not None:
            parts.append(label + ': ' + format(values[key], '.10g').replace('.', ','))
    if values['age_rating']:
        parts.append('Возраст: ' + values['age_rating'])
    return ' · '.join(parts)
