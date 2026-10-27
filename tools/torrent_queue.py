"""Local qBittorrent ingestion, durable jobs, and bounded disk use."""

import contextlib
import http.cookiejar
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

GIB = 1024**3
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".ts"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class QBitClient:
    def __init__(self, url, username="", password="", *, api_key=""):
        parsed = urllib.parse.urlsplit(url.strip())
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("Адрес qBittorrent должен быть локальным: http://127.0.0.1:8080")
        self.url = url.strip().rstrip("/")
        self.username, self.password = username, password
        self.api_key = api_key.strip()
        if self.api_key and not re.fullmatch(r'qbt_[A-Za-z0-9]{28}', self.api_key):
            raise ValueError('Проверьте API-ключ qBittorrent: нужен полный ключ, начинающийся с qbt_.')
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )

    def request(self, endpoint, data=None, *, multipart=None, params=None):
        if not re.fullmatch(r"[a-zA-Z]+/[a-zA-Z]+", endpoint):
            raise ValueError("Invalid API endpoint")
        headers = {"Referer": self.url + "/", "Origin": self.url}
        if self.api_key:
            if endpoint.startswith('auth/'):
                raise ValueError('В режиме API-ключа вход по логину и паролю не используется.')
            headers['Authorization'] = 'Bearer ' + self.api_key
        body = None
        if multipart:
            body, content_type = multipart
            headers["Content-Type"] = content_type
        elif data is not None:
            body = urllib.parse.urlencode(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
        url = self.url + "/api/v2/" + endpoint
        if params is not None:
            url += '?' + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, data=body, headers=headers)
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                if self.api_key:
                    raise ValueError('qBittorrent не принял API-ключ. Проверьте полный ключ, '
                                     'сохранение настроек Web UI и версию 5.2 или новее.') from None
                raise ValueError('qBittorrent отказал в доступе. Проверьте данные Web UI; '
                                 'после нескольких неудачных входов адрес может быть временно заблокирован.') from None
            raise ValueError(f"qBittorrent: HTTP {error.code}. Задача сохранена.") from None
        except OSError:
            raise ValueError(
                "qBittorrent недоступен. Запустите клиент и включите локальный Web UI."
            ) from None
        if raw.lstrip().startswith(("[", "{")):
            return json.loads(raw)
        return raw.strip()

    def login(self):
        if not self.api_key:
            if self.request("auth/login", {"username": self.username, "password": self.password}) != "Ok.":
                raise ValueError('qBittorrent не принял логин и пароль Web UI. '
                                 'Можно выбрать вход по API-ключу из настроек qBittorrent 5.2+.')
        version = self.request("app/version")
        if not re.match(r"v?5\.", version):
            raise ValueError("Для очереди нужен qBittorrent 5.x.")
        if self.api_key and int(re.match(r'v?5\.(\d+)', version)[1]) < 2:
            raise ValueError('Для входа по API-ключу нужен qBittorrent 5.2 или новее.')
        return version

    def files(self, torrent_hash):
        return self.query("torrents/files", {"hash": torrent_hash})

    def query(self, endpoint, params):
        # GET requests are required for read-only qBittorrent APIs.
        value = self.request(endpoint, params=params)
        if not isinstance(value, (list, dict)):
            raise ValueError("Не удалось прочитать состояние qBittorrent. Задача сохранена.") from None
        return value

    def info(self, torrent_hash):
        rows = self.query("torrents/info", {"hashes": torrent_hash})
        if len(rows) != 1:
            raise ValueError("Раздача не найдена в qBittorrent. Добавьте её заново.")
        return rows[0]

    def stop(self, torrent_hash):
        self.request("torrents/stop", {"hashes": torrent_hash})

    def start(self, torrent_hash):
        self.request("torrents/start", {"hashes": torrent_hash})

    def priorities(self, torrent_hash, ids, priority):
        if ids:
            self.request(
                "torrents/filePrio",
                {"hash": torrent_hash, "id": "|".join(map(str, ids)), "priority": priority},
            )

    def add(self, source, savepath, tag):
        fields = {
            "savepath": str(savepath),
            "tags": tag,
            "autoTMM": "false",
            "useDownloadPath": "false",
            "contentLayout": "Original",
            "stopped": "true",
            "stopCondition": "MetadataReceived",
        }
        if source.startswith("magnet:"):
            if "\n" in source or "\r" in source:
                raise ValueError("Добавляйте одну magnet-ссылку за раз.")
            fields["urls"] = source
            result = self.request("torrents/add", fields)
        else:
            path = Path(source)
            if path.suffix.lower() != ".torrent" or path.stat().st_size > 10 * 1024**2:
                raise ValueError("Выберите .torrent размером до 10 МБ.")
            boundary = "KinoTime" + uuid.uuid4().hex
            parts = []
            for key, value in fields.items():
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
                )
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="torrents"; filename="source.torrent"\r\nContent-Type: application/x-bittorrent\r\n\r\n'.encode()
                + path.read_bytes()
                + b"\r\n"
            )
            parts.append(f"--{boundary}--\r\n".encode())
            result = self.request(
                "torrents/add", multipart=(b"".join(parts), "multipart/form-data; boundary=" + boundary)
            )
        if result != "Ok.":
            raise ValueError("qBittorrent не добавил раздачу. Возможно, она уже есть в клиенте.")


def safe_path(root, relative):
    if (
        not relative
        or PureWindowsPath(relative).is_absolute()
        or PureWindowsPath(relative).drive
        or ":" in relative
        or any(p in ("..", "") for p in relative.replace("\\", "/").split("/"))
    ):
        raise ValueError("В раздаче найден недопустимый путь файла.")
    root = Path(root).resolve()
    result = root.joinpath(*relative.replace("\\", "/").split("/"))
    if not result.resolve().is_relative_to(root):
        raise ValueError("Файл находится за пределами рабочей папки.")
    current = result
    while current != root:
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("Ссылки на внешние папки не поддерживаются.")
        current = current.parent
    return result


def used_bytes(root):
    total, seen = 0, set()
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = [name for name in folders if not Path(directory, name).is_symlink()]
        for name in files:
            path = Path(directory, name)
            try:
                if path.is_symlink():
                    continue
                stat = path.stat()
            except FileNotFoundError:
                # Atomic journal replacement and completed media renames can
                # remove a directory entry after os.walk has listed it.
                continue
            identity = (stat.st_dev, stat.st_ino)
            if identity not in seen:
                seen.add(identity)
                total += stat.st_size
    return total


class TorrentQueue:
    def __init__(self, root, quota_gib=100, reserve_gib=20):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "torrent-queue.json"
        self.quota = float(quota_gib) * GIB
        self.reserve = float(reserve_gib) * GIB
        if self.quota <= 0 or self.reserve < 0:
            raise ValueError("Лимит места должен быть положительным, резерв — неотрицательным.")
        self.jobs = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        if not isinstance(self.jobs, list):
            raise ValueError("Повреждён журнал торрент-очереди.")
        for job in self.jobs:
            if not re.fullmatch(r"[a-f0-9]{32}", job.get("id", "")):
                raise ValueError("Повреждён идентификатор задачи.")
            if job.get("status") in ("downloading", "preparing", "metadata"):
                job.update(status="paused", error="Возобновите задачу после перезапуска.")

    def save(self):
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.jobs, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def check_space(self, extra=0):
        used = used_bytes(self.root)
        free = shutil.disk_usage(self.root).free
        if used + extra > self.quota:
            raise ValueError("Достигнут лимит рабочей папки. Освободите место или измените лимит.")
        if free - extra < self.reserve:
            raise ValueError("Недостаточно свободного места с учётом резерва. Очередь приостановлена.")

    def folder(self, job):
        return self.root / job["id"]

    def verify(self, client, job):
        info = client.info(job["hash"])
        if job["tag"] not in [tag.strip() for tag in info.get("tags", "").split(",")]:
            raise ValueError("Раздача не принадлежит этой очереди.")
        if Path(info["save_path"]).resolve() != (self.folder(job) / "download").resolve():
            raise ValueError("Папка раздачи изменена в qBittorrent. Задача остановлена.")
        return info

    def add(self, client, source, stop, report):
        self.check_space()
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "tag": "kinotime-" + job_id,
            "title": "Получение списка файлов",
            "status": "metadata",
            "files": [],
            "selected": [],
            "error": "",
            "mode": "batch",
            "catalog_section": "movies",
        }
        folder = self.folder(job) / "download"
        folder.mkdir(parents=True)
        self.jobs.append(job)
        self.save()
        owned = False
        try:
            client.add(source, folder, job["tag"])
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if stop.is_set():
                    raise InterruptedError("Получение списка файлов остановлено.")
                rows = client.query("torrents/info", {"tag": job["tag"]})
                if rows:
                    job["hash"] = rows[0]["hash"]
                    self.save()
                    self.verify(client, job)
                    owned = True
                    files = client.files(job["hash"])
                    if files:
                        client.stop(job["hash"])
                        client.priorities(job["hash"], [f["index"] for f in files], 0)
                        for file in files:
                            safe_path(folder, file["name"])
                        job.update(title=rows[0]["name"], files=files, status="choose_files")
                        self.save()
                        return job
                    # MetadataReceived stops magnets before content download.
                    if not job.get("metadata_started"):
                        client.start(job["hash"])
                        job["metadata_started"] = True
                        self.save()
                report("Получаю список файлов раздачи…")
                if stop.wait(1):
                    raise InterruptedError("Получение списка файлов остановлено.")
            raise ValueError("Нет списка файлов. Для magnet без доступных пиров попробуйте .torrent.")
        except Exception as error:
            if job.get("hash") and owned:
                with contextlib.suppress(Exception):
                    client.stop(job["hash"])
            job.update(status="error", error=str(error))
            self.save()
            raise

    def select(self, job, ids, *, mode, section, movie_number=None, season_number=None):
        if job.get("import_folder"):
            raise ValueError("Файлы уже подготовлены для импорта. Создайте новую задачу для другого выбора.")
        videos = {f["index"]: f for f in job["files"] if Path(f["name"]).suffix.lower() in VIDEO_EXTENSIONS}
        if not ids or len(ids) != len(set(ids)) or any(i not in videos for i in ids):
            raise ValueError("Выберите хотя бы один видеофайл.")
        if mode not in ("batch", "single", "season", "project"):
            raise ValueError("Неизвестный режим импорта.")
        if mode == "season" and (
            not isinstance(movie_number, int)
            or movie_number <= 0
            or not isinstance(season_number, int)
            or season_number <= 0
        ):
            raise ValueError("Для сезона укажите код существующего сериала и номер сезона.")
        if mode not in ('season', 'project') and section in ("series", "animated_series"):
            raise ValueError("Для сериалов выберите режим «Сезон существующего сериала».")
        job.update(
            selected=list(ids),
            mode=mode,
            catalog_section=section,
            movie_number=movie_number,
            season_number=season_number,
            status="queued",
            error="",
        )
        self.save()

    def stage_files(self, job, files):
        destination = self.folder(job) / "import"
        destination.mkdir(exist_ok=True)
        selected = [f for f in files if f["index"] in job["selected"]]
        names = [Path(f["name"].replace("\\", "/")).name.casefold() for f in selected]
        for file in selected:
            source = safe_path(self.folder(job) / "download", file["name"])
            if not source.is_file() or source.stat().st_size != file["size"]:
                raise ValueError("Скачанный файл не найден или имеет неверный размер.")
            name = Path(file["name"].replace("\\", "/")).name
            if names.count(name.casefold()) > 1:
                name = f"{file['index']:04d} - {name}"
            # Preserve nested season/voice directories in single-project imports.
            relative = file["name"] if job["mode"] != "batch" else name
            target = safe_path(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if not os.path.samefile(source, target):
                    raise ValueError("В папке импорта найден другой файл с тем же именем.")
            else:
                try:
                    os.link(source, target)
                except OSError:
                    raise ValueError(
                        "Рабочая папка должна поддерживать жёсткие ссылки (NTFS). "
                        "Копирование больших файлов автоматически не выполняется."
                    ) from None
        return destination

    def download(self, client, job, stop, report, changed, limit_mib=5):
        self.verify(client, job)
        client.stop(job["hash"])
        files = client.files(job["hash"])
        selected = [f for f in files if f["index"] in job["selected"]]
        if not selected or len(selected) != len(job["selected"]):
            raise ValueError("Выбранные файлы больше не соответствуют раздаче.")
        total = sum(f["size"] for f in selected)
        remaining = sum(f["size"] * (1 - f["progress"]) for f in selected)
        if float(limit_mib) <= 0:
            raise ValueError("Укажите положительную скорость скачивания.")
        self.check_space(remaining)
        client.priorities(job["hash"], [f["index"] for f in files], 0)
        client.priorities(job["hash"], job["selected"], 1)
        client.request(
            "torrents/setDownloadLimit",
            {"hashes": job["hash"], "limit": max(1, int(float(limit_mib) * 1024**2))},
        )
        job.update(status="downloading", error="")
        self.save()
        try:
            client.start(job["hash"])
            started = time.monotonic()
            while not stop.is_set():
                info = self.verify(client, job)
                if info["state"] in ("error", "missingFiles"):
                    raise ValueError("qBittorrent сообщил об ошибке файлов или диска.")
                current = client.files(job["hash"])
                active = [f for f in current if f["index"] in job["selected"]]
                if len(active) != len(selected):
                    raise ValueError("Изменился список файлов раздачи.")
                self.check_space()
                percent = sum(f["size"] * f["progress"] for f in active) / max(1, total) * 100
                job["progress"] = round(percent, 1)
                changed()
                report(f"{job['title']}: скачивание {percent:.1f}%")
                if all(f["progress"] >= 1 for f in active):
                    client.stop(job["hash"])
                    destination = self.stage_files(job, active)
                    job.update(status="downloaded", import_folder=str(destination))
                    self.save()
                    return destination
                if info["state"].startswith("stopped"):
                    # qBittorrent applies start asynchronously; don't misread its old state.
                    if time.monotonic() - started >= 10:
                        raise InterruptedError("Раздача остановлена в qBittorrent. Возобновите очередь.")
                stop.wait(3)
            raise InterruptedError("Скачивание приостановлено.")
        finally:
            client.stop(job["hash"])
            if job["status"] == "downloading":
                job["status"] = "paused"
                self.save()

    def reprepare(self, job):
        """Reuse downloaded sources; leave media and existing web drafts intact."""
        if job not in self.jobs or job['status'] not in ('prepared', 'downloaded', 'paused', 'error'):
            raise ValueError('Сначала завершите скачивание или остановите подготовку этой задачи.')
        folder = Path(job.get('import_folder') or '')
        if folder.resolve() != (self.folder(job) / 'import').resolve() or not folder.is_dir():
            raise ValueError('Скачанные исходники задачи не найдены. Повторите скачивание.')
        job.update(status='downloaded', error='')
        self.save()
        return folder

    def prepare(self, job, stop, prepare_callback, changed=lambda: None):
        self.check_space()
        folder = Path(job["import_folder"])
        if folder.resolve() != (self.folder(job) / "import").resolve():
            raise ValueError("Недопустимая папка импорта.")
        job.update(status="preparing", error="")
        self.save()
        changed()
        monitor_done = threading.Event()
        errors = []

        def monitor():
            while not monitor_done.wait(1):
                try:
                    self.check_space()
                except Exception as error:
                    errors.append(str(error))
                    stop.preparation_error = str(error)
                    stop.set()
                    return

        watchdog = threading.Thread(target=monitor, daemon=True)
        watchdog.start()
        try:
            result = prepare_callback(job, folder)
            if errors:
                raise ValueError(errors[0])
            if stop.is_set():
                raise InterruptedError("Подготовка приостановлена.")
            self.check_space()
            if isinstance(result, dict) and result.get('queued_project_id'):
                job.update(status='project_queued', project_id=result['queued_project_id'], manifest=None, error='')
            else:
                job.update(status="prepared", manifest=result, error="")
            self.save()
        except Exception:
            if errors:
                raise InterruptedError(errors[0]) from None
            raise
        finally:
            monitor_done.set()
            watchdog.join(timeout=2)
            stop.preparation_error = ""

    def run(self, client, stop, report, changed, prepare_callback, limit_mib=5, *, job_ids=None):
        with queue_lock(self.root):
            failure = ""
            projects_added = False
            for job in self.jobs:
                if job_ids is not None and job['id'] not in job_ids:
                    continue
                if stop.is_set():
                    break
                if not job.get("selected") or job["status"] in ("prepared", "cancelled", "project_queued"):
                    continue
                try:
                    if job["status"] != "downloaded" and not job.get("import_folder"):
                        self.download(client, job, stop, report, changed, limit_mib)
                    self.prepare(job, stop, prepare_callback, changed)
                    projects_added = projects_added or job['status'] == 'project_queued'
                except Exception as error:
                    job.update(status="paused" if stop.is_set() else "error", error=str(error))
                    self.save()
                    failure = str(error)
                    # Disk/network/errors require review; never keep downloading blindly.
                    break
                finally:
                    changed()
            report(
                "Очередь приостановлена: " + failure
                if failure
                else "Очередь остановлена."
                if stop.is_set()
                else 'Скачивание завершено. Исходники переданы в очередь проектов.'
                if projects_added
                else "Очередь завершена. Готовые задачи ждут ручной отправки в Telegram."
            )


@contextlib.contextmanager
def queue_lock(root, name="torrent-queue.lock"):
    # OS releases the advisory lock after a crash; no stale lock deletion required.
    path = Path(root) / name
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("Эта торрент-очередь уже открыта в другом лаунчере.") from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
