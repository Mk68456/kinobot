"""Project queue operations shared by remote control and its tests."""

def snapshot(manager):
    result = []
    for project in manager.snapshots(details=True):
        variants = []
        for task in project['tasks']:
            quality = task['plan']['qualities'][0]
            for voice in ([a['name'] for a in task['plan']['audio'] if a['enabled']] or ['Без звука']):
                progress = task.get('progress', {}).get(f'{quality} / {voice}', {})
                variants.append({'source': task['entry']['path'], 'quality': quality, 'voice': voice,
                                 'state': task['state'], 'detail': progress.get('detail', ''),
                                 'status': progress.get('status', '')})
        result.append({**{k: project[k] for k in ('id', 'title', 'folder', 'state', 'message', 'priority')},
                       'ready': sum(t['count'] for t in project['tasks'] if t['state'] == 'done'),
                       'total': sum(t['count'] for t in project['tasks']), 'variants': variants})
    return result


def control(manager, action, payload, stop):
    """Immediate operations; validate IDs before requesting a pause."""
    if action == 'project_parallelism':
        manager.set_parallelism(payload.get('parallelism'))
    elif action == 'project_pause_all':
        manager.pause()
        if any(p['state'] in ('sending', 'registering') for p in manager.snapshots()):
            stop.set()
    else:
        project_id = payload.get('project_id')
        project = manager.get(project_id)
        if action == 'project_pause':
            manager.pause(project_id)
            if project['state'] in ('sending', 'registering'):
                stop.set()
        elif action == 'project_resume':
            manager.resume(project_id)
        elif action == 'project_priority':
            manager.prioritize(project_id)
        else:
            raise ValueError('Неизвестная команда проекта.')


def transfer(manager, project_id, client, stop, report, sending=False):
    try:
        from . import desktop_launcher as launcher
        from .launcher_projects import register_or_send
    except ImportError:
        import desktop_launcher as launcher
        from launcher_projects import register_or_send

    def connect(data):
        desktop = launcher.TelegramDesktop(data['bot_username'], data['bot_name'])
        desktop.open()
        report('Отправка проекта начнётся через 8 секунд.')
        if stop.wait(8):
            raise InterruptedError('Отправка проекта приостановлена.')
        try:
            desktop.bind()
        except Exception as error:
            raise launcher.BeforeSendError(str(error)) from error
        return desktop

    register_or_send(manager, project_id, client, stop, report, connect, sending)
