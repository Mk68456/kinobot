"""Supervise KinoTime + loopback Bot API on a single-container hosting platform.

No Docker CLI dependency. Explicit opt-in for cloud logOut. Secrets are never
included in process arguments or exception messages emitted by this supervisor.
"""

from __future__ import annotations

import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
import uuid


class StartupError(Exception):
    pass


def configure_storage(environment, root, legacy_root=None):
    """Keep explicit DB paths, copy an existing root DB only into an absent persistent DB."""
    env = dict(environment)
    data = Path(env.get('DATA_DIR') or '/app/data').resolve()
    if data == Path(data.anchor):
        raise StartupError('DATA_DIR должен указывать на отдельную папку данных.')
    explicit = env.get('DATABASE_PATH')
    target = Path(explicit).resolve() if explicit else data / 'bot.sqlite'
    if not target.exists():
        source = (legacy_root or root) / 'bot.sqlite'
        if explicit and env.get('BOTHOST_ALLOW_EMPTY_DB') != '1':
            raise StartupError('Файл DATABASE_PATH не найден. Укажите путь к существующей базе.')
        if not explicit and source.is_file() and source.resolve() != target.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name('bothost-import-' + uuid.uuid4().hex + '.sqlite')
            try:
                with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original:
                    if original.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise StartupError('Проверка существующей базы не пройдена; импорт остановлен.')
                    with closing(sqlite3.connect(temporary)) as destination:
                        original.backup(destination)
                # Startup is single-process; the live bot has not been launched yet.
                if target.exists():
                    raise StartupError('База появилась во время импорта; автоматическая замена запрещена.')
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        elif env.get('BOTHOST_ALLOW_EMPTY_DB') != '1':
            raise StartupError('База не найдена. Укажите DATABASE_PATH. Для нового пустого бота: BOTHOST_ALLOW_EMPTY_DB=1.')
    if target.exists():
        with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)) as connection:
            if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise StartupError('База повреждена; запуск остановлен.')
    data.mkdir(parents=True, exist_ok=True)
    env['DATA_DIR'], env['DATABASE_PATH'] = str(data), str(target)
    env.setdefault('BACKUP_DIR', str(data / 'backups'))
    if env.get('WEB_ENABLED') == '1':
        env.setdefault('WEB_HOST', '0.0.0.0')
        env.setdefault('WEB_PORT', env.get('PORT') or '8088')
    return env


def validate_credentials(env):
    if (not re.fullmatch(r'[1-9][0-9]{0,9}', env.get('TELEGRAM_API_ID', ''))
            or int(env['TELEGRAM_API_ID']) >= 2 ** 31):
        raise StartupError('Добавьте TELEGRAM_API_ID в переменные Bothost.')
    if not re.fullmatch(r'[a-fA-F0-9]{32}', env.get('TELEGRAM_API_HASH', '')):
        raise StartupError('Добавьте TELEGRAM_API_HASH: 32 шестнадцатеричных символа.')


def api_command(directory):
    return ['/usr/local/bin/telegram-bot-api', '--local', '--http-ip-address=127.0.0.1',
            '--http-port=8081', '--verbosity=0', '--dir=' + str(directory)]


def write_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data), encoding='utf-8')
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def marker_ready(directory, token):
    marker = directory / 'kinotime-migration.json'
    identity = directory / 'kinotime-instance.json'
    if not marker.exists():
        return False
    try:
        data = json.loads(marker.read_text(encoding='utf-8'))
        instance = json.loads(identity.read_text(encoding='utf-8'))
        return (data['token_sha256'] == hashlib.sha256(token.encode()).hexdigest()
                and data['instance'] == instance['instance'] and data.get('cloud_logout_completed') is True)
    except (OSError, ValueError, KeyError):
        return False


def save_marker(directory, token):
    identity = directory / 'kinotime-instance.json'
    if identity.exists():
        instance = json.loads(identity.read_text(encoding='utf-8'))['instance']
    else:
        instance = uuid.uuid4().hex
        write_json(identity, {'instance': instance})
    write_json(directory / 'kinotime-migration.json', {
        'version': 1, 'instance': instance, 'token_sha256': hashlib.sha256(token.encode()).hexdigest(),
        'cloud_logout_completed': True})


def telegram_json(base, token, method):
    # Ignore outbound proxy variables for loopback traffic; never send local tokens through a proxy.
    opener = build_opener(ProxyHandler({})) if base.startswith('http://127.0.0.1:') else build_opener()
    try:
        request = Request(base + '/bot' + token + '/' + method, data=b'', method='POST')
        with opener.open(request, timeout=30) as response:
            data = json.loads(response.read(65536))
        if not data.get('ok'):
            raise StartupError('Telegram отклонил ' + method + '. Проверьте настройки и состояние переноса.')
        return data.get('result')
    except StartupError:
        raise
    except Exception as exc:
        if isinstance(exc, HTTPError):
            exc.close()
        raise StartupError('Запрос ' + method + ' не выполнен (' + type(exc).__name__ + ').') from None


def migrate_cloud(directory, token, enabled, call=telegram_json):
    if marker_ready(directory, token):
        return False
    if (directory / 'kinotime-migration.json').exists():
        raise StartupError('Отметка переноса не соответствует токену или данным API. '
                           'Автоматический повтор logOut запрещён; проверьте восстановление папки API.')
    if not enabled:
        raise StartupError('Первый переход не подтверждён. Остановите прежний экземпляр бота и задайте '
                           'BOTHOST_MIGRATE_CLOUD=1. Если бот уже на другом локальном API, сначала перенесите его данные.')
    if call('https://api.telegram.org', token, 'logOut') is not True:
        raise StartupError('Облачный logOut не подтверждён. Бот не запущен.')
    # Remember successful logout even if local getMe subsequently times out.
    save_marker(directory, token)
    return True


def wait_api(process, stopped, timeout=60, opener=None):
    opener = opener or build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stopped.is_set():
            raise StartupError('Запуск остановлен.')
        if process.poll() is not None:
            raise StartupError('Bot API завершился при запуске. Проверьте ресурсы и реквизиты.')
        try:
            with opener.open('http://127.0.0.1:8081/', timeout=2) as response:
                data = json.loads(response.read(4096))
                if isinstance(data, dict) and 'ok' in data:
                    return
        except HTTPError as exc:
            try:
                data = json.loads(exc.read(4096))
                if exc.code == 404 and data.get('error_code') == 404 and data.get('ok') is False:
                    return
            except (ValueError, AttributeError):
                pass
            finally:
                exc.close()
        except (OSError, ValueError):
            pass
        stopped.wait(.25)
    raise StartupError('Bot API не начал отвечать за отведённое время.')


def stop_process(process, timeout):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def monitor(api, bot, stopped):
    while not stopped.wait(.2):
        if api.poll() is not None:
            print('Bot API завершился. Останавливаю бота.', flush=True)
            return 1
        if bot.poll() is not None:
            print('Бот завершился. Останавливаю Bot API.', flush=True)
            return bot.returncode or 1
    return 0


def read_token(root):
    sys.path.insert(0, str(root))
    try:
        from data.config import BOT_TOKEN
    except (ValueError, ImportError):
        raise StartupError('Проверьте BOT_TOKEN и ADMIN_IDS в настройках Bothost.') from None
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', BOT_TOKEN):
        raise StartupError('Проверьте токен бота в data/config.py.')
    return BOT_TOKEN


def prepare_permissions(env, root, directory):
    directory.mkdir(parents=True, exist_ok=True)
    if hasattr(os, 'getuid') and os.getuid() == 0:
        subprocess.run([sys.executable, str(root / 'tools/prepare_storage.py')], env=env, cwd=root, check=True)
        os.chown(directory, 10001, 10001)
        import pwd
        account = pwd.getpwnam('bot')
        os.initgroups(account.pw_name, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
        env['HOME'], env['USER'] = account.pw_dir, account.pw_name


def main(argv=None):
    root = Path(os.environ.get('KINOTIME_APP_ROOT', '/app')).resolve()
    command = list(sys.argv[1:] if argv is None else argv) or [sys.executable, str(root / 'app.py')]
    if len(command) != 2 or Path(command[1]).name != 'app.py':
        raise StartupError('Сборка Bothost запускает app.py. Уберите нестандартную команду запуска в панели.')
    # The platform may pass /app/app.py even though /app is replaced by a source mount.
    # Run the exact code baked into this image together with its matching env configuration.
    command = [sys.executable, str(root / 'app.py')]
    env = configure_storage(os.environ, root, Path('/app'))
    if env.get('BOTHOST_LOCAL_API', '0') not in {'0', '1'}:
        raise StartupError('BOTHOST_LOCAL_API должен быть 0 или 1.')
    local = env.get('BOTHOST_LOCAL_API') == '1'
    if local:
        validate_credentials(env)
        if env.get('WEB_ENABLED') == '1' and env.get('WEB_PORT') == '8081':
            raise StartupError('Порт 8081 занят встроенным Bot API. Укажите другой WEB_PORT для панели.')
        # A second listener must never make the readiness check succeed for the wrong process.
        import socket
        with socket.socket() as listener:
            try:
                listener.bind(('127.0.0.1', 8081))
            except OSError:
                raise StartupError('Порт 8081 уже занят. Второй Bot API не запускается.') from None
    directory = Path(env['DATA_DIR']) / 'telegram-bot-api'
    prepare_permissions(env, root, directory)
    if not local:
        print('Встроенный Bot API выключен: BOTHOST_LOCAL_API=0.', flush=True)
        os.execvpe(command[0], command, env)
        return 0
    token = read_token(root)
    if not marker_ready(directory, token) and env.get('BOTHOST_MIGRATE_CLOUD') != '1':
        raise StartupError('Для первого перехода на встроенный API задайте BOTHOST_MIGRATE_CLOUD=1 '
                           'после остановки прежнего экземпляра бота.')
    env['BOT_API_BASE'] = 'http://127.0.0.1:8081'
    stopped = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda signum, frame: stopped.set())
    api = bot = None
    try:
        api = subprocess.Popen(api_command(directory), cwd=directory, env=env)
        wait_api(api, stopped)
        print('Встроенный Bot API отвечает на 127.0.0.1:8081.', flush=True)
        if migrate_cloud(directory, token, env.get('BOTHOST_MIGRATE_CLOUD') == '1'):
            print('Первый переход с облачного API выполнен и сохранён.', flush=True)
        if stopped.is_set():
            return 0
        result = telegram_json(env['BOT_API_BASE'], token, 'getMe')
        if not isinstance(result, dict) or result.get('id') != int(token.split(':')[0]):
            raise StartupError('Локальный Bot API не подтвердил нужного бота.')
        if stopped.is_set() or api.poll() is not None:
            return 1
        print('Локальный getMe подтверждён. Запускаю KinoTime.', flush=True)
        bot = subprocess.Popen(command, cwd=root, env=env)
        return monitor(api, bot, stopped)
    finally:
        # Give the application time to close SQLite and join its download thread before the API goes away.
        stop_process(bot, 40)
        stop_process(api, 15)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except StartupError as error:
        print('Запуск остановлен: ' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
    except Exception as error:
        print('Запуск остановлен (' + type(error).__name__ + '). Проверьте пути, права и ресурсы.',
              file=sys.stderr, flush=True)
        raise SystemExit(1)
