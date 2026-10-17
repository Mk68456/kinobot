"""Transactional merging of published series; preview never changes the catalog."""
import hashlib
import json
import secrets
from database.connection import database
from database.categories import episode_order


def snapshot(code):
    movie = database.execute('SELECT * FROM Movies WHERE movie_number=?', (code,)).fetchone()
    if not movie:
        raise ValueError('Карточка не найдена.')
    info = database.execute('SELECT movie_title,content_type,catalog_section FROM Movies WHERE movie_number=?', (code,)).fetchone()
    if info[1] != 'series':
        raise ValueError('Этот инструмент объединяет сериалы и мультсериалы.')
    if database.execute("SELECT 1 FROM Movies WHERE movie_number=? AND COALESCE(movie_file,'')!=''", (code,)).fetchone():
        raise ValueError('У сериала есть отдельный основной файл вне сезонов. Сначала перенесите его в сезон.')
    folders = []
    for cid, name, number in database.execute('SELECT id,name,season_number FROM MovieCategories WHERE movie_number=? ORDER BY id', (code,)).fetchall():
        episodes = []
        for eid, title, file_id, file_type in database.execute('SELECT id,name,file_id,file_type FROM MovieSubcategories WHERE category_id=? ORDER BY id', (cid,)).fetchall():
            choices = database.execute('SELECT quality,voice,file_id,file_type FROM EpisodeVariants WHERE episode_id=? ORDER BY id', (eid,)).fetchall()
            if file_id and not any(row[2] == file_id for row in choices):
                choices.append(('Исходное', '', file_id, file_type))
            episodes.append({'id':eid,'name':title,'choices':choices})
        folders.append({'id':cid,'name':name,'number':number,'episodes':episodes})
    return {'code':code,'title':info[0],'section':info[2],'movie':movie,'folders':folders}


def folder_key(folder):
    return ('number', folder['number']) if folder['number'] else ('name', folder['name'].strip().casefold())


def episode_key(episode):
    order = episode_order((episode['id'], episode['name']))
    return ('number', order[1]) if order[0] == 0 else ('name', episode['name'].strip().casefold())


def plan(source, target):
    if type(source) is not int or type(target) is not int or not 1 <= source <= 2**53-1 or not 1 <= target <= 2**53-1 or source == target:
        raise ValueError('Выберите две разные карточки.')
    src, dst = snapshot(source), snapshot(target)
    if src['section'] != dst['section']:
        raise ValueError('Карточки должны находиться в одном разделе каталога.')
    token = hashlib.sha256(json.dumps([src,dst],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    known = {}
    conflicts, moves, pairs, folders = [], [], [], []
    for folder in dst['folders']:
        key = folder_key(folder)
        if key in known:
            conflicts.append('В основной карточке несколько сезонов с одним номером или названием.')
        known[key] = folder
    source_keys = set()
    for folder in src['folders']:
        key = folder_key(folder)
        if key in source_keys:
            conflicts.append('В переносимой карточке несколько сезонов с одним номером или названием.')
        source_keys.add(key)
        existing = known.get(key)
        if not existing:
            moves.append(folder['id'])
            continue
        folders.append((folder['id'],existing['id']))
        by_episode = {}
        for episode in existing['episodes']:
            key_ep = episode_key(episode)
            if key_ep in by_episode:
                conflicts.append(f"{existing['name']}: несколько записей одной серии в основной карточке.")
            by_episode[key_ep] = episode
        incoming_keys = set()
        for episode in folder['episodes']:
            key_ep = episode_key(episode)
            if key_ep in incoming_keys:
                conflicts.append(f"{folder['name']}: несколько записей одной серии в переносимой карточке.")
            incoming_keys.add(key_ep)
            previous = by_episode.get(key_ep)
            if not previous:
                continue
            labels = {(q.casefold(),v.casefold()):(fid,typ) for q,v,fid,typ in previous['choices']}
            for quality,voice,fid,typ in episode['choices']:
                label = (quality.casefold(),voice.casefold())
                if label in labels and labels[label] != (fid,typ):
                    conflicts.append(f"{folder['name']} · {episode['name']}: разные файлы для {quality} {voice}.")
            pairs.append((episode,previous))
    return src,dst,{'token':token,'source':{'code':source,'title':src['title']},'target':{'code':target,'title':dst['title']},
                   'seasons':len(src['folders']),'episodes':sum(len(f['episodes']) for f in src['folders']),
                   'merged_episodes':len(pairs),'conflicts':conflicts,'can_merge':not conflicts},moves,folders,pairs


def preview(source,target):
    return plan(source,target)[2]


def merge(source,target,token):
    if not isinstance(token,str):
        raise ValueError('Сначала проверьте объединение.')
    with database.transaction():
        src,dst,result,moves,folders,pairs = plan(source,target)
        if not secrets.compare_digest(token,result['token']):
            raise ValueError('Карточки изменились. Проверьте объединение снова.')
        if result['conflicts']:
            raise ValueError('Есть конфликты файлов. Исправьте их перед объединением.')
        category_map = dict(folders)
        for incoming,existing in pairs:
            eid = existing['id']
            # Materialize legacy primary files before deleting a duplicate episode.
            for quality,voice,fid,typ in existing['choices'] + incoming['choices']:
                duplicate = database.execute('SELECT 1 FROM EpisodeVariants WHERE episode_id=? AND lower(quality)=lower(?) AND lower(voice)=lower(?)', (eid,quality,voice)).fetchone()
                if not duplicate:
                    database.execute('INSERT INTO EpisodeVariants (episode_id,quality,voice,file_id,file_type) VALUES (?,?,?,?,?)', (eid,quality,voice,fid,typ))
            database.execute('INSERT OR IGNORE INTO EpisodeProgress(user_id,episode_id,watched_at) SELECT user_id,?,watched_at FROM EpisodeProgress WHERE episode_id=?', (eid,incoming['id']))
            database.execute('DELETE FROM MovieSubcategories WHERE id=?', (incoming['id'],))
        for old,new in folders:
            database.execute('UPDATE MovieSubcategories SET category_id=? WHERE category_id=?', (new,old))
            database.execute('DELETE FROM MovieCategories WHERE id=?', (old,))
        for cid in moves:
            database.execute('UPDATE MovieCategories SET movie_number=? WHERE id=?', (target,cid))
        database.execute('UPDATE MovieTorrents SET movie_number=? WHERE movie_number=?', (target,source))
        database.execute('UPDATE WatchEvents SET movie_number=?,movie_title=? WHERE movie_number=?', (target,dst['title'],source))
        database.execute('UPDATE ContentRequests SET movie_number=? WHERE movie_number=?', (target,source))
        database.execute('INSERT OR IGNORE INTO MovieShareAccess(user_id,movie_number) SELECT user_id,? FROM MovieShareAccess WHERE movie_number=?', (target,source))
        database.execute('DELETE FROM MovieShareAccess WHERE movie_number=?', (source,))
        # Preserve the source share token when the target has no token yet.
        if database.execute('SELECT 1 FROM MovieShareLinks WHERE movie_number=?', (target,)).fetchone():
            database.execute('DELETE FROM MovieShareLinks WHERE movie_number=?', (source,))
        else:
            database.execute('UPDATE MovieShareLinks SET movie_number=? WHERE movie_number=?', (target,source))
        for did,raw,published_code in database.execute('SELECT id,body,movie_number FROM WebDrafts').fetchall():
            body = json.loads(raw)
            changed = False
            if body.get('movie_number') == source:
                body['movie_number'] = target
                changed = True
            if body.get('category_id') in category_map:
                body['category_id'] = category_map[body['category_id']]
                changed = True
            if changed or published_code == source:
                database.execute('UPDATE WebDrafts SET body=?,movie_number=? WHERE id=?',
                                 (json.dumps(body,ensure_ascii=False),target if published_code == source else published_code,did))
                database.execute('DELETE FROM PublicationPackage WHERE draft_id=?', (did,))
        database.execute('DELETE FROM Movies WHERE movie_number=?', (source,))
    return {'movie_number':target,'merged':True,'episodes':result['episodes']}
