"""Episode navigation may return a poster before an available voice is selected."""
import re

from tools.yougen_bridge import BridgeError, message_by_id, plain_label, video_record
from tools.provider_identity import video_label
from tools.provider_matching import media_title_matches


def navigation_record(message, payload):
    record = video_record(message)
    if record:
        return record if media_title_matches(record['label'], payload) else None
    if payload.get('content_type') != 'series':
        return None
    text = getattr(message, 'raw_text', '') or ''
    lines = text.splitlines()
    label = lines[0].strip() if lines else ''
    position = re.search(r'(?im)^\s*(\d+) сезон\s+(\d+) серия\s*$', text)
    labels = {plain_label(b.text) for row in message.buttons or [] for b in row
              if getattr(b, 'data', None)}
    if (not position or not video_label(label) or not media_title_matches(label, payload)
            or not {'Озвучки серии', 'Все озвучки'}.issubset(labels)):
        return None
    return dict(label=label, season=int(position[1]), episode=int(position[2]),
                quality=video_label(label)['quality'], message_id=message.id)


async def voice_menu(client, source, card_id, *, series=False):
    from tools.yougen_discovery import menu, click
    current = await message_by_id(client, source, card_id)
    def toggles(card):
        return [b for row in card.buttons or [] for b in row
                if plain_label(b.text) == 'Озвучки серии' and getattr(b, 'data', None)]
    if not toggles(current):
        await menu(client, source, card_id, 'Озвучка')
        current = await message_by_id(client, source, card_id)
    if not series:
        return
    controls = toggles(current)
    if len(controls) != 1:
        raise BridgeError('Источник не подтвердил фильтр озвучек выбранной серии.')
    # This is a checkbox, not a link: clicking the checked control disables it.
    if not controls[0].text.lstrip().startswith('✅'):
        await click(client, source, current, controls[0].text)
        current = await message_by_id(client, source, card_id)
        controls = toggles(current)
    if len(controls) != 1 or not controls[0].text.lstrip().startswith('✅'):
        raise BridgeError('Не удалось включить озвучки только выбранной серии.')
