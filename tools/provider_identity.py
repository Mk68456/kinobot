"""Cosmetic title normalization; truncated search labels are never proof of media identity."""
import re
import unicodedata


def valid_voice_name(value):
    # A label supplied by the authenticated agent, never executable navigation.
    return (isinstance(value, str) and 1 <= len(value) <= 80 and value == value.strip()
            and not any(c in value for c in '/\\:\r\n\0\t')
            and value not in ('.', '..'))


def canonical_title(value):
    value = unicodedata.normalize('NFKC', value).casefold().replace('ё', 'е')
    return ' '.join(re.sub(r'[\W_]+', ' ', value, flags=re.UNICODE).split())


def source_name(value):
    return value.split(' (', 1)[0].strip()


def inline_title_matches(display, expected):
    name = source_name(display)
    truncated = name.endswith(('…', '...'))
    if truncated:
        name = re.sub(r'(?:…|\.{3})$', '', name).rstrip()
    actual, wanted = canonical_title(name), canonical_title(expected)
    if actual == wanted:
        return True
    # Candidate selection only. The complete video caption must be verified later.
    return truncated and len(actual) >= 24 and len(actual.split()) >= 3 and wanted.startswith(actual)


def video_label(label):
    match = re.fullmatch(r'(.+) \((.+) \[([0-9]{3,4}p)\]\)', label)
    if not match:
        return None
    name = source_name(match[1])
    if name.endswith(('…', '...')):
        return None
    return {'title': name, 'voice': match[2], 'quality': match[3]}


def full_video_title_matches(label, title):
    parsed = video_label(label)
    return bool(parsed and canonical_title(parsed['title']) == canonical_title(title))
