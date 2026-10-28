"""Render a fast Dockerfile only with an explicitly pinned base-image digest."""

import argparse
from pathlib import Path
import re


def render(base_image):
    if not re.fullmatch(r'ghcr\.io/[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9._-]*@sha256:[0-9a-f]{64}', base_image):
        raise ValueError('Нужен ghcr.io/...@sha256:... из успешно завершённой сборки базы.')
    template = (Path(__file__).resolve().parents[1] / 'deploy/Dockerfile.bothost-fast.in').read_text(encoding='utf-8')
    if template.count('@@BASE_IMAGE@@') != 1:
        raise ValueError('Неизвестный шаблон Dockerfile.')
    return template.replace('@@BASE_IMAGE@@', base_image)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image', required=True)
    parser.add_argument('--output', type=Path, default=Path('output/Dockerfile.bothost-fast'))
    args = parser.parse_args()
    contents = render(args.base_image)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(contents)
    print('Fast Dockerfile created:', args.output)


if __name__ == '__main__':
    main()
