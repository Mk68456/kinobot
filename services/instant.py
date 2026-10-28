"""User delivery is separate from review/publication of a catalogue draft."""

from aiogram import types
from database import instant_jobs as jobs
from database.connection import database
from database.age_access import preferences
from services.access import is_admin

STATUS = {'queued': 'В очереди', 'working': 'Ищу и подготавливаю файл', 'ready': 'Файл готов',
          'sending': 'Отправляю', 'sent': 'Отправлено', 'uncertain': 'Проверьте чат перед повтором',
          'review': 'Нужна проверка администратора'}


def recover():
    database.execute("UPDATE InstantJobs SET status='uncertain',error='bot_restarted' WHERE status='sending'")
    database.commit()


def enabled(user_id):
    from data import settings
    return settings.INSTANT_ENABLED and (settings.INSTANT_PUBLIC or is_admin(user_id))


def markup(job):
    keyboard = types.InlineKeyboardMarkup()
    if job['status'] in {'ready', 'sent', 'uncertain'}:
        title = {'ready': 'Получить файл', 'sent': 'Получить ещё раз',
                 'uncertain': 'Проверил чат — повторить'}[job['status']]
        keyboard.add(types.InlineKeyboardButton(title, callback_data=f"instant:get:{job['id']}:{job['generation']}"))
    keyboard.add(types.InlineKeyboardButton('Обновить статус', callback_data='instant:status:' + job['id']))
    return keyboard


def label(job):
    p = job['payload']
    series = f" · S{p['season']:02d}E{p['episode']:02d}" if p['content_type'] == 'series' else ''
    return f"{p['title']}{series} · {p['voice']} · {p['quality']}\n{STATUS[job['status']]}"


async def deliver(bot, job_id, user_id, *, repeat=False, expected_generation=None):
    from services.subscriptions import check_subscription
    job = jobs.get(job_id, user_id)
    if not job or not enabled(user_id) or preferences(user_id)['max_age'] != 18:
        return False
    if not await check_subscription(user_id):
        return False
    with database.transaction():
        current = jobs.get(job_id, user_id)
        allowed = {'ready', 'sent', 'uncertain'} if repeat else {'ready'}
        if (current['status'] not in allowed or not current['file_id']
                or expected_generation is not None and current['generation'] != expected_generation):
            return False
        jobs.update(job_id, status='sending', generation=current['generation'] + 1)
    try:
        message = await bot.send_video(user_id, current['file_id'], caption=label(current).split('\n')[0], parse_mode='')
    except Exception as error:
        jobs.update(job_id, status='uncertain', error=type(error).__name__)
        return False
    jobs.update(job_id, status='sent', sent_message_id=message.message_id, error=None)
    return True


async def notify(bot, job_id):
    job = jobs.get(job_id)
    if job['status'] == 'ready' and await deliver(bot, job_id, job['user_id']):
        job = jobs.get(job_id)
    try:
        await bot.send_message(job['user_id'], label(job), parse_mode='', reply_markup=markup(job))
    except Exception:
        # The file remains available through an ownership-checked status command.
        pass
