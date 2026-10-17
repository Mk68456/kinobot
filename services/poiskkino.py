"""Explicit admin requests only; viewing a bot card never uses this API."""
import asyncio
import json
import os
from datetime import datetime, timezone
import aiohttp
from database.connection import database
from services.ratings import normalize

_lock = asyncio.Lock()


async def fetch(path, params):
    key = os.environ.get('POISKKINO_API_KEY', '').strip()
    if not key:
        raise ValueError('Укажите POISKKINO_API_KEY в настройках сервера.')
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get('https://api.poiskkino.dev/v1.4/' + path,
                                   params=params, headers={'X-API-KEY': key}) as response:
                if response.status == 429:
                    raise ValueError('Лимит ПоискКино исчерпан. Сохранённые данные доступны; повторите обновление позже.')
                if response.status in (401,403):
                    raise ValueError('ПоискКино отклонил ключ или доступ к методу.')
                if response.status != 200:
                    raise ValueError('ПоискКино временно недоступен. Сохранённые оценки не изменены.')
                return await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
        raise ValueError('Не удалось получить данные ПоискКино. Повторите позже.') from None


def convert(item, stamp):
    ratings = item.get('rating') or {}
    votes = item.get('votes') or {}
    age = item.get('ageRating')
    values = {}
    for source, target in (('kp','rating_kp'),('imdb','rating_imdb')):
        value = ratings.get(source)
        # Provider uses zero for missing scores, unlike a manually entered zero.
        if votes.get(source) == 0 or value == 0:
            value = None
        try:values[target] = normalize({target:value})[target]
        except ValueError:values[target] = None
    values['age_rating'] = str(age)+'+' if type(age) is int and age in (0,6,12,16,18) else None
    return {**values, 'rating_source_id':item['id'], 'ratings_fetched_at':stamp,
            'title':str(item.get('name') or item.get('alternativeName') or ''),
            'year':item.get('year'), 'content_type':'series' if item.get('isSeries') or item.get('type') in ('tv-series','animated-series') else 'movie'}


async def lookup(query='', kind='movie', source_id=None, refresh=False):
    if kind not in ('movie','series') or not isinstance(query,str) or len(query)>200:
        raise ValueError('Проверьте название и тип произведения.')
    if source_id is not None and (type(source_id) is not int or not 0<source_id<2**53):
        raise ValueError('Некорректный ID источника.')
    if source_id is None and not query.strip():
        raise ValueError('Введите название произведения.')
    cache_key = f'id:{source_id}' if source_id else 'search:'+query.strip().casefold()
    async with _lock:
        cached=database.execute('SELECT body,fetched_at FROM RatingCache WHERE cache_key=?',(cache_key,)).fetchone()
        if cached and not refresh:
            records=json.loads(cached[0]);stamp=cached[1]
        else:
            raw=await fetch('movie/'+str(source_id) if source_id else 'movie/search',
                            {} if source_id else {'query':query.strip(),'page':1,'limit':10})
            records=[raw] if source_id else raw.get('docs',[])
            if not isinstance(records,list) or any(not isinstance(i,dict) or type(i.get('id')) is not int for i in records):
                raise ValueError('Некорректный ответ ПоискКино.')
            stamp=datetime.now(timezone.utc).isoformat()
            with database.transaction():
                database.execute('INSERT OR REPLACE INTO RatingCache VALUES (?,?,?)',(cache_key,json.dumps(records),stamp))
                for item in records:
                    database.execute('INSERT OR REPLACE INTO RatingCache VALUES (?,?,?)',(f"id:{item['id']}",json.dumps([item]),stamp))
        return {'items':[value for item in records if (value:=convert(item,stamp))['content_type']==kind],
                'cached': bool(cached and not refresh)}


def provenance(body):
    source_id=body.get('rating_source_id')
    if source_id in (None,''):
        return {'rating_source_id':None,'ratings_fetched_at':None}
    if type(source_id) is not int or not 0<source_id<2**53:
        raise ValueError('Некорректный ID источника рейтингов.')
    row=database.execute('SELECT fetched_at FROM RatingCache WHERE cache_key=?',(f'id:{source_id}',)).fetchone()
    if not row:raise ValueError('Сначала получите данные выбранного произведения.')
    return {'rating_source_id':source_id,'ratings_fetched_at':row[0]}
