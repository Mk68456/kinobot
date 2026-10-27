"""Repackage the verified update with project folders at the ZIP root."""
import hashlib
import json
import zipfile

from package_update import FILES, ROOT


def main():
    source = ROOT / 'KinoTime-Episode-Cards-2026-10-08.zip'
    target = ROOT / 'KinoTime-Episode-Cards-Server-2026-10-08.zip'
    with zipfile.ZipFile(source) as previous:
        if previous.testzip() is not None:
            raise RuntimeError('The verified source ZIP is damaged.')
        manifest = json.loads(previous.read('FILES-SHA256.json'))
        contents = {name: previous.read('Server/' + name) for name in FILES}
        for name, data in contents.items():
            if hashlib.sha256(data).hexdigest() != manifest['Server/' + name]:
                raise RuntimeError(f'Invalid source hash: {name}')
            if data != (ROOT / name).read_bytes():
                raise RuntimeError(f'Source changed since verification: {name}')
        validation = previous.read('Verification/VALIDATION.txt')
    instructions = (
        'Обновление карточек выбора качества и озвучки · 08.10.2026\n\n'
        'На скриншоте отсутствует модуль services.episode_cards. '
        'Этот архив включает новый файл services/episode_cards.py и связанные обработчики.\n\n'
        '1. Остановите бот.\n'
        '2. Распакуйте архив в корень проекта на сервере, рядом с app.py, с заменой файлов.\n'
        '   Папки handlers, services и database должны объединиться с существующими папками.\n'
        '   Не распаковывайте в дополнительную папку Server или папку с названием архива.\n'
        '3. Проверьте наличие services/episode_cards.py рядом с services/ratings.py.\n'
        '4. Запустите бот снова.\n\n'
        'В database обновляется только Python-файл episode_variants.py. '
        'Файлов базы данных, токенов, настроек и медиа в архиве нет.\n'
        'Лаунчер обновлять не требуется.\n'
    )
    records = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
        archive.writestr('UPDATE-INSTALL.txt', instructions)
        archive.writestr('UPDATE-VALIDATION.txt', validation)
        archive.writestr('UPDATE-SHA256.json', json.dumps(records, indent=2))
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('ZIP CRC verification failed.')
        for name, digest in records.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise RuntimeError(f'Archive content mismatch: {name}')
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
    print(f'{target.name}: {len(FILES)} project files; {target.stat().st_size} bytes; source hashes and CRC OK')


if __name__ == '__main__':
    main()
