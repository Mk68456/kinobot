"""Poster menus and bounded, non-spamming queue message updates."""
import asyncio
from aiogram.utils.exceptions import BadRequest, RetryAfter


async def banner(message, data, text, keyboard):
    selected = data['request_selected']
    title = str(selected.get('title') or '')[:240]
    if data.get('request_kind') == 'series':
        title += f" · Сезон {data.get('request_season', 0)}, серия {data.get('request_episode', 0)}"
    if data.get('instant_voice'):
        title += '\n' + str(data['instant_voice'])[:120]
    caption = title + '\n\n' + text
    poster = selected.get('poster_url')
    if poster:
        try:
            return await message.answer_photo(poster, caption=caption, parse_mode='', reply_markup=keyboard)
        except BadRequest:
            # A missing/unsupported poster must not make the menu unavailable.
            pass
    return await message.answer(caption, parse_mode='', reply_markup=keyboard)


async def edit_status(message, text, keyboard=None):
    try:
        await message.edit_text(text, parse_mode='', reply_markup=keyboard)
        return True
    except RetryAfter as error:
        await asyncio.sleep(error.timeout)
    except BadRequest:
        pass
    return False


async def watch_job(message, job_id, user_id):
    from database import instant_jobs
    from services import instant
    previous = None
    # All state is durable; after a bot restart /instant_status still works.
    for _ in range(2880):
        job = instant_jobs.get(job_id, user_id)
        if not job:
            return
        text = instant.label(job)
        if text != previous:
            if not await edit_status(message, text, instant.markup(job)):
                return
            previous = text
        if job['status'] not in {'queued', 'working', 'sending'}:
            return
        await asyncio.sleep(30)
