"""Bound disk usage by transferring each completed variant before rendering more."""
import hashlib
import json
import os
from pathlib import Path

try:
    from . import disk_space, launcher_device
except ImportError:
    import disk_space
    import launcher_device


def run(folder, client, stop, report, connect, *, qualities=('source', '720p'), section='movies'):
    try:
        from . import desktop_launcher as launcher
    except ImportError:
        import desktop_launcher as launcher
    safe = launcher.RetryingClient(client, stop, report)
    session = safe.request('session')
    if session.get('capabilities', {}).get('streaming_import') != 1 or not safe.instance_id:
        raise ValueError('Сначала обновите серверную часть для поточной подготовки и подключите компьютер.')
    original, plans = launcher.plan_folder_qualities(folder, qualities, stop)
    expected = sum(len(p['qualities']) * max(1, sum(a['enabled'] for a in p['audio'])) for p in plans)
    signature = {'source': original, 'plans': plans, 'section': section,
                 'server': client.url, 'instance': safe.instance_id}
    key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:24]
    source_root = Path(original['root'])
    root = source_root.parent / 'KinoTime-Ready' / ('stream-' + key)
    root.mkdir(parents=True, exist_ok=True)
    journal = root / '.pipeline-state.json'
    header = {'version': 1, 'root': str(root), 'title': original['title'], 'catalog_section': section}
    desktop = None
    source_entries = {str((source_root / p['entry']['path']).resolve()): p['entry'] for p in plans}

    def persist():
        temp = journal.with_suffix('.tmp')
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(journal)

    def snapshot():
        if not state['draft_id']:
            return set()
        response = safe.request('import/' + state['draft_id'])
        if response['draft_status'] != 'draft':
            raise ValueError('Поточная карточка закрыта или на доработке. Верните её на проверку.')
        return {f['id'] for f in response['files'] if f['status'] == 'ready'}

    def append_and_send(entry):
        nonlocal desktop
        data = safe.request('stream', {**header, 'stream_expected': expected, 'files': [entry]})
        if state['draft_id'] and data['draft']['id'] != state['draft_id']:
            raise ValueError('Панель вернула другую карточку. Очередь остановлена.')
        state['draft_id'] = data['draft']['id']
        # Remember server IDs before Send so a restart checks the receipt first.
        state['assets'] = {i['relative_path']: i['asset_id'] for i in data['draft']['body']['items']}
        persist()
        manifest = {**header, 'files': [entry]}
        sending = {**data, 'draft': {**data['draft'], 'body': {**data['draft']['body'],
            'items': [i for i in data['draft']['body']['items'] if i['relative_path'] == entry['path']]}}}
        desktop = launcher.send_with_recovery(safe, sending, manifest, desktop, stop, report,
                                              connect, launcher.transfer)
        accepted = snapshot()
        if state['assets'].get(entry['path']) not in accepted:
            raise ValueError('Не все готовые файлы подтверждены ботом. Копии сохранены; повторите этот режим.')
        for path, asset in state['assets'].items():
            if asset not in accepted:
                continue
            metadata = state['files'][path]
            disk_space.confirm(root / path, metadata)
            disk_space.release_confirmed(root / path)
        report(f"Принято ботом: {len(accepted)}/{expected}. Подтверждённые копии очищены; продолжаю подготовку.")

    # A project lock prevents two launcher processes racing the same transfer journal.
    with launcher.queue_lock(root), launcher.keep_awake(), disk_space.session():
        state = (json.loads(journal.read_text(encoding='utf-8')) if journal.exists()
                 else {'signature': signature, 'draft_id': '', 'files': {}, 'assets': {}})
        if state.get('signature') != signature:
            raise ValueError('Локальный план проекта изменился. Проверьте исходники и настройки.')
        accepted = snapshot()
        # Recover a crash after registration or Send before creating any more files.
        for path, entry in list(state['files'].items()):
            if state['assets'].get(path) not in accepted:
                if not (root / path).is_file():
                    raise ValueError('Неподтверждённая копия отсутствует: ' + path)
                append_and_send(entry)
                accepted = snapshot()
            else:
                disk_space.confirm(root / path, entry)
                disk_space.release_confirmed(root / path)
        disk_space.protect(root)

        def relative(source, quality, filename):
            return (Path(quality) / launcher.episode_output_directory(source_entries[str(Path(source).resolve())]['path']) /
                    filename).as_posix()

        def skip(source, quality, filename):
            path = relative(source, quality, filename)
            return path in state['files'] and state['assets'].get(path) in accepted

        def ready(source, destination, entry):
            nonlocal accepted
            source_entry = source_entries[str(Path(source).resolve())]
            info = Path(source).stat()
            if info.st_size != source_entry['size'] or str(info.st_mtime_ns) != source_entry['mtime']:
                raise ValueError('Исходник изменился во время подготовки: ' + str(source))
            generated = Path(destination) / entry['path']
            path = relative(source, entry['quality'], generated.name)
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                try:
                    os.link(generated, target)
                except OSError:
                    disk_space.copy(generated, target, stop, report)
            info = target.stat()
            if info.st_size != entry['size'] or str(info.st_mtime_ns) != entry['mtime']:
                raise ValueError('Готовая копия изменилась: ' + path)
            disk_space.register(target, alias=generated)
            entry = {**entry, 'path': path, 'name': Path(source_entry['path']).stem,
                     'voice': entry['voice'][:80]}
            entry = launcher_device.hash_manifest({**header, 'files': [entry]}, report)['files'][0]
            if path in state['files'] and state['files'][path] != entry:
                raise ValueError('Ранее подготовленный вариант изменился: ' + path)
            state['files'][path] = entry
            persist()  # Durable before a network request that may succeed without a response.
            append_and_send(entry)
            accepted = snapshot()
            update = getattr(report, 'variant', lambda *args: None)
            update(f"{source_entry['path']} / {entry['quality']} / {entry['voice'] or 'Без звука'}",
                   'Принят', 'Подтверждённая копия очищена')

        previous = {key: stop.__dict__.get(key) for key in ('variant_ready', 'variant_skip')}
        stop.variant_ready, stop.variant_skip = ready, skip
        try:
            launcher.prepare_media(folder, report, stop, qualities=qualities or
                                   list(dict.fromkeys(q for p in plans for q in p['qualities'])))
            if stop.is_set():
                raise InterruptedError('Поточная подготовка остановлена. Прогресс сохранён.')
            result = safe.request(f"stream/{state['draft_id']}/finish", {})
            report(f'Готово: {expected}/{expected} файлов принято. Оформите и опубликуйте карточку в WEB-панели.')
            return result
        finally:
            for key, value in previous.items():
                if value is None:
                    stop.__dict__.pop(key, None)
                else:
                    setattr(stop, key, value)
