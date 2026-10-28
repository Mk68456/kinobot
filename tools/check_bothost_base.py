"""Check public pull access without sending any GitHub or bot credentials."""

import argparse
import hashlib
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from tools.render_bothost_fast import render


def check(reference):
    render(reference)  # Same strict syntax check as the Dockerfile generator.
    repository, digest = reference.removeprefix('ghcr.io/').split('@')
    query = urlencode({'service': 'ghcr.io', 'scope': f'repository:{repository}:pull'})
    with urlopen('https://ghcr.io/token?' + query, timeout=20) as response:
        auth = json.loads(response.read(65536))
    token = auth.get('token') or auth['access_token']
    request = Request('https://ghcr.io/v2/' + repository + '/manifests/' + digest,
                      headers={'Authorization': 'Bearer ' + token,
                               'Accept': 'application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'})
    with urlopen(request, timeout=20) as response:
        manifest = response.read(2 * 1024**2 + 1)
    if len(manifest) > 2 * 1024**2 or 'sha256:' + hashlib.sha256(manifest).hexdigest() != digest:
        raise ValueError('Registry manifest digest did not match')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image', required=True)
    args = parser.parse_args()
    try:
        check(args.base_image)
        print('Базовый образ доступен без входа; digest подтверждён.')
    except HTTPError as error:
        print(f'Образ недоступен без входа: HTTP {error.code}. Не переключайте Bothost.')
        raise SystemExit(1)
    except Exception as error:
        print('Проверка остановлена: ' + type(error).__name__ + '. Не переключайте Bothost.')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
