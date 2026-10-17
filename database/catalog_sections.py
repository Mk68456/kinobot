from database.connection import database


def all_sections():
    return [{'key': row[0], 'title': row[1], 'emoji': row[2],
             'required_content_type': row[3], 'enabled': bool(row[4]), 'sort_order': row[5]}
            for row in database.execute('''SELECT section_key,title,emoji,required_content_type,enabled,sort_order
                FROM CatalogSections ORDER BY sort_order,section_key''').fetchall()]


def enabled_sections():
    return [section for section in all_sections() if section['enabled']]


def get_section(key):
    return next((section for section in all_sections() if section['key'] == key), None)


def default_section(content_type):
    return 'series' if content_type == 'series' else 'movies'


def validate_section(key, content_type):
    section = get_section(key)
    if not section:
        raise ValueError('Выберите раздел каталога.')
    required = section['required_content_type']
    if required and required != content_type:
        raise ValueError(f'Раздел «{section["title"]}» не поддерживает выбранный формат.')
    return section


def set_enabled(key, enabled):
    if not get_section(key):
        raise ValueError('Раздел каталога не найден.')
    with database.transaction():
        database.execute('UPDATE CatalogSections SET enabled=? WHERE section_key=?',
                         (1 if enabled else 0, key))
    return get_section(key)
