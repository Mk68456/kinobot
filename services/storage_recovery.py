"""Restore Storage references from an audited inventory without downloading media."""
import re
import uuid
from collections import defaultdict

from database.connection import database
from webpanel import repository


def restore(inventory, admin_id):
    videos = inventory['videos']
    channel = inventory['summary']['channel_id']
    if type(channel) is not int or channel >= 0 or type(admin_id) is not int or admin_id <= 0:
        raise ValueError('Invalid channel or administrator ID')
    groups = defaultdict(list)
    added = 0
    seen = set()
    with database.transaction():
        for index, video in enumerate(videos):
            message = video['message_id']
            size = video['size']
            if (type(message) is not int or message <= 0 or message in seen or
                    type(size) is not int or size <= 0 or video['channel_id'] != channel):
                raise ValueError('Invalid or repeated Storage message')
            seen.add(message)
            marker = f'storage:{channel}:{message}'
            asset_id = uuid.uuid5(uuid.NAMESPACE_URL, f'kinotime:{admin_id}:{marker}').hex
            existing = database.execute('SELECT id,admin_id,file_id FROM WebAssets WHERE source_chat_id=? AND source_message_id=?',
                                        (channel, message)).fetchone()
            if existing:
                if existing[1] != admin_id:
                    raise ValueError('Storage message belongs to another administrator')
                asset_id = existing[0]
                marker = existing[2] or marker
                database.execute("UPDATE WebAssets SET file_id=?,status='ready',received=size WHERE id=?", (marker, asset_id))
            else:
                database.execute('''INSERT INTO WebAssets
                    (id,admin_id,source,filename,size,file_type,file_id,status,received,source_chat_id,source_message_id)
                    VALUES (?,?,'telegram',?,?,'video',?,'ready',?,?,?)''',
                    (asset_id, admin_id, video['filename'][:240], size, marker, size, channel, message))
                added += 1
            database.execute('''INSERT OR IGNORE INTO TelegramStorageFiles
                (file_id,file_type,storage_chat_id,storage_message_id,status) VALUES (?,'video',?,?,'ready')''',
                (marker, channel, message))
            match = re.search(r'(?:S(\d{1,3})E(\d{1,3})|(\d{1,3})x(\d{1,3}))', video['filename'], re.I)
            candidates = video.get('journal_candidates', [])
            season = episode = None
            voice = ''
            if len(candidates) == 1:
                c = candidates[0]
                title = c['source_title']
                name = c['episode_name']
                match = re.search(r'(?:S(\d{1,3})E(\d{1,3})|(\d{1,3})x(\d{1,3}))', name, re.I)
                if match:
                    season, episode = int(match[1] or match[3]), int(match[2] or match[4])
                quality, voice = c.get('quality', ''), c.get('voice', '')
                section = 'series'
            elif match:
                title = video['filename'][:match.start()].strip(' ._-').replace('.', ' ') or 'Сериал — уточнить название'
                season, episode = int(match[1] or match[3]), int(match[2] or match[4])
                name = f'Серия {episode}'
                quality = ''
                section = 'series'
            else:
                title = f'Storage — разобрать файлы, пакет {index // 100 + 1}'
                name = f"Storage №{message} — {video['filename']}"
                quality = ''
                section = 'movies'
            if not quality:
                # Keep real dimensions rather than guessing a quality for cropped media.
                quality = f"{video['height']}p" if video.get('height') in (360,480,720,1080,2160) else ''
            item = {'asset_id': asset_id, 'name': name[:200], 'quality': quality[:30],
                    'voice': voice[:80], 'season_number': season or None,
                    'category': quality or 'Из Storage'}
            groups[(section, title[:180], season)].append(item)
        drafts = 0
        for (section, title, season), items in groups.items():
            for offset in range(0, len(items), 500):
                subset = items[offset:offset+500]
                identity = f'kinotime-recovery:{admin_id}:{channel}:{section}:{title}:{season}:{offset}'
                draft_id = uuid.uuid5(uuid.NAMESPACE_URL, identity).hex
                # Never overwrite edits or reopen a published draft on a repeated restore.
                if database.execute('SELECT 1 FROM WebDrafts WHERE id=?', (draft_id,)).fetchone():
                    continue
                body = {'title': title, 'content_type': 'series' if section == 'series' else 'movie',
                        'catalog_section': section, 'season_number': season or None,
                        'season_name': f'Сезон {season}' if season else '', 'items': subset,
                        'description': 'Восстановлено из Storage. Проверьте названия, качества, озвучки и возраст перед публикацией.'}
                draft = repository.save_draft(admin_id, body)
                # save_draft creates a random ID; retain deterministic identity for safe reruns.
                database.execute('UPDATE WebDrafts SET id=? WHERE id=?', (draft_id, draft['id']))
                drafts += 1
        database.execute('''INSERT OR IGNORE INTO TelegramStorage(admin_id,chat_id,title,enabled)
            VALUES (?,?,?,1)''', (admin_id, channel, inventory['summary'].get('channel_name','KinoTime Storage')))
    return {'video_messages':len(videos),'assets_added':added,'drafts_created':drafts,'channel_id':channel}
