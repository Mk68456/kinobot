"""Durable upload reservations; an uncertain Send is never retried automatically."""
import time

from database.connection import database
from webpanel import imports, repository
from webpanel.launcher_control import valid_instance


def operate(admin_id, instance, draft_id, asset_id, action, verified_not_sent=False):
    if not valid_instance(instance):
        raise ValueError('Обновите лаунчер для безопасной отправки с нескольких компьютеров.')
    with database.transaction():
        current = imports.device_draft(admin_id, draft_id, instance)
        if current['status'] not in ('draft', 'revision'):
            raise ValueError('Черновик закрыт. Передача остановлена.')
        if asset_id not in {i['asset_id'] for i in current['body']['items']}:
            raise ValueError('Файл не принадлежит этой карточке.')
        asset = repository.asset(asset_id, admin_id)
        if not asset or asset['source'] != 'desktop':
            raise ValueError('Файл недоступен.')
        if action not in ('claim', 'sent', 'release'):
            raise ValueError('Неизвестная операция передачи.')
        if asset['status'] == 'ready':
            return {'status': 'ready'}
        digest = database.execute('SELECT content_sha256 FROM WebAssets WHERE id=?', (asset_id,)).fetchone()[0]
        key = str(admin_id) + ':' + (digest or asset_id)
        if action == 'claim' and digest:
            ready = database.execute("""SELECT file_id,file_type,id FROM WebAssets
                WHERE admin_id=? AND content_sha256=? AND size=? AND status='ready' AND file_id IS NOT NULL LIMIT 1""",
                (admin_id, digest, asset['size'])).fetchone()
            if ready:
                database.execute("""UPDATE WebAssets SET status='ready',error=NULL,received=size,file_id=?,
                    file_type=?,reused_asset_id=? WHERE id=?""", (*ready, asset_id))
                return {'status': 'ready', 'reused': True}
        now = time.time()
        lease = database.execute('SELECT asset_id,instance_id,status,expires_at FROM LauncherTransfers WHERE content_key=?', (key,)).fetchone()
        own = lease and lease[0] == asset_id and lease[1] == instance
        if action == 'claim':
            if lease and (lease[2] == 'sent' or lease[3] > now) and not own:
                name = database.execute('SELECT name FROM LauncherAgents WHERE admin_id=? AND instance_id=?', (admin_id, lease[1])).fetchone()
                raise ValueError('Этот файл уже отправляется с компьютера «' + (name[0] if name else lease[1]) + '». Дождитесь подтверждения бота.')
            if own and lease[2] == 'sent':
                return {'status': 'sent'}
            database.execute('''INSERT INTO LauncherTransfers VALUES (?,?,?,?,?,?,?) ON CONFLICT(content_key) DO UPDATE SET
                asset_id=excluded.asset_id,instance_id=excluded.instance_id,status=excluded.status,
                expires_at=excluded.expires_at,updated_at=excluded.updated_at''',
                (key, admin_id, asset_id, instance, 'claimed', now + 90, now))
            return {'status': 'claimed'}
        if not own:
            raise ValueError('Разрешение на отправку принадлежит другому компьютеру или истекло.')
        if action == 'sent':
            if lease[2] == 'sent':
                # A lost HTTP response must not start a second upload.
                raise ValueError('Отправка уже отмечена. Проверьте подтверждение бота; автоматический повтор запрещён.')
            if lease[3] <= now:
                raise ValueError('Разрешение на отправку истекло. Запустите передачу снова.')
            database.execute("UPDATE LauncherTransfers SET status='sent',updated_at=? WHERE content_key=?", (now, key))
            return {'status': 'sent'}
        if lease[2] == 'sent' and verified_not_sent is not True:
            raise ValueError('Сначала подтвердите, что файл не отправлен и не загружается в Telegram.')
        database.execute('DELETE FROM LauncherTransfers WHERE content_key=?', (key,))
        return {'status': 'released'}


def reset(admin_id, instance, asset_ids, verified_not_sent):
    if verified_not_sent is not True or not valid_instance(instance) or not isinstance(asset_ids, list) or len(asset_ids) > 1000:
        raise ValueError('Подтвердите отсутствие отправки перед повтором.')
    changed = 0
    with database.transaction():
        for asset_id in asset_ids:
            if not isinstance(asset_id, str):
                raise ValueError('Некорректная метка файла.')
            # Switching import modes creates a different asset for identical bytes.
            # Explicitly verified retries may release this device's old reservation
            # for those bytes, while reservations of other devices remain intact.
            asset = database.execute('SELECT content_sha256 FROM WebAssets WHERE id=? AND admin_id=?',
                                     (asset_id, admin_id)).fetchone()
            digest = asset[0] if asset else None
            changed += database.execute('''DELETE FROM LauncherTransfers WHERE admin_id=? AND instance_id=?
                AND (asset_id=? OR content_key=?)''',
                (admin_id, instance, asset_id, str(admin_id) + ':' + (digest or asset_id))).rowcount
    return {'released': changed}
