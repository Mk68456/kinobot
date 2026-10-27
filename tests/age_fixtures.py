"""Explicitly reviewed age metadata for pre-existing publication scenarios."""
from webpanel import imports, repository


def reviewed_import(*args, **kwargs):
    current = imports.create(*args, **kwargs)
    if current['status'] == 'draft':
        current = repository.save_draft(args[0], {**current['body'], 'age_rating': '0+'}, current['id'])
    return current


def reviewed_group(admin_id, selections, body):
    return repository.group_files(admin_id, selections, {'age_rating': '0+', **body})
