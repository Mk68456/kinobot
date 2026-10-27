"""Standalone source/download experiment; does not touch the bot database."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import Request, urlopen

if __package__:
    from .zona_source import discover_zona, is_zona_page
else:
    from zona_source import discover_zona, is_zona_page

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "output" / "zona-prototype"
USER_AGENT = "KinoTime-Source-Prototype/0.1"


def request(url, headers=None, method=None):
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("Нужна ссылка http:// или https://")
    return urlopen(Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})}, method=method), timeout=30)


def probe(url, headers=None):
    # Range GET also supports hosts that refuse HEAD. Read only a small prefix.
    with request(url, {**(headers or {}), "Range": "bytes=0-1023"}) as response:
        content_type = response.headers.get_content_type()
        prefix = response.read(1024)
        match = re.fullmatch(r"bytes \d+-\d+/(\d+)", response.headers.get("Content-Range", ""))
        size = int(match[1]) if match else int(response.headers.get("Content-Length", "0"))
        if content_type not in {"video/mp4", "application/octet-stream"} or b"ftyp" not in prefix[:64]:
            raise ValueError("Источник не подтверждён как MP4: " + content_type)
        return {"size": size, "content_type": content_type,
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified")}


class Sources(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base, self.items = base, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in {"video", "source", "a"}:
            return
        value = attrs.get("src") if tag != "a" else attrs.get("href")
        if value and urlsplit(value).path.lower().endswith(".mp4"):
            url = urljoin(self.base, value)
            if url not in [item["url"] for item in self.items]:
                self.items.append({"url": url, "label": attrs.get("label") or attrs.get("title") or media_name(url)})


def discover(url):
    if urlsplit(url).path.lower().endswith(".mp4"):
        return [{"url": url, "label": media_name(url)}]
    try:
        if is_zona_page(url):
            return discover_zona(url, request)
        with request(url) as response:
            data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise ValueError("Страница слишком большая")
            parser = Sources(response.url)
            parser.feed(data.decode(response.headers.get_content_charset() or "utf-8", errors="replace"))
            if not parser.items:
                raise ValueError("На странице нет явных MP4-источников. Скопируйте URL видео из меню плеера.")
            return parser.items
    except HTTPError as exc:
        code = exc.code
        exc.close()
        if code == 403:
            raise ValueError("Сайт отказал в доступе (403). Вставьте URL видео из меню плеера.") from exc
        raise


def media_name(url):
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")[:100]
    return name or "video.mp4"


def save_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


class Cancelled(Exception):
    pass


def download(url, output=DEFAULT_OUTPUT, page_url="", progress=lambda done, total: None, cancel=None,
             headers=None, source_id=None, metadata=None, max_bytes=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    info = probe(url, headers)
    if max_bytes is not None and (not info['size'] or info['size'] > max_bytes):
        raise ValueError('Размер видео неизвестен или превышает лимит загрузки.')
    expected_size = (metadata or {}).get("expected_size")
    if expected_size and info["size"] and int(expected_size) != info["size"]:
        raise ValueError("Размер источника отличается от заявленного: возможно, это превью вместо серии")
    # Stable catalog IDs allow re-resolving expired URLs; validators protect partial files.
    key = hashlib.sha256((source_id or url).encode()).hexdigest()[:16]
    name = media_name("https://local/" + source_id + ".mp4") if source_id else media_name(url)
    target = output / (key + "-" + name)
    partial = target.with_suffix(target.suffix + ".part")
    record = target.with_suffix(target.suffix + ".json")
    if target.exists():
        previous = json.loads(record.read_text(encoding="utf-8")) if record.exists() else {}
        if previous.get("status") == "ready" and target.stat().st_size == info["size"]:
            return target
        raise ValueError("Файл уже существует, но его целостность не подтверждена")
    previous = json.loads(record.read_text(encoding="utf-8")) if record.exists() else {}
    validator = info.get("etag") or info.get("last_modified")
    if partial.exists() and (not validator or previous.get("validator") != validator):
        partial.write_bytes(b"")
    state = {"source_url": url, "page_url": page_url, "status": "downloading",
             "validator": validator, **info, "file": target.name, "metadata": metadata or {}}
    save_json(record, state)
    try:
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > info["size"] and info["size"]:
            partial.write_bytes(b"")
            offset = 0
        if not info["size"] or offset != info["size"]:
            stream_headers = {**(headers or {}), **({"Range": f"bytes={offset}-", "If-Range": validator} if offset else {})}
            with request(url, stream_headers) as response:
                if offset and response.status == 206:
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise ValueError("Сервер вернул неверную позицию продолжения")
                elif offset:
                    offset = 0  # Server ignored Range / entity changed: safely restart.
                with partial.open("ab" if offset else "wb") as stream:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise Cancelled("Загрузка остановлена. Её можно продолжить.")
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        offset += len(chunk)
                        if max_bytes is not None and offset > max_bytes:
                            raise ValueError('Видео превысило лимит загрузки.')
                        stream.write(chunk)
                        progress(offset, info["size"])
            if info["size"] and offset != info["size"]:
                raise ValueError("Получен неполный файл. Повторите загрузку для продолжения.")
        with partial.open("rb") as stream:
            if b"ftyp" not in stream.read(64):
                raise ValueError("Скачанный файл не является MP4")
            stream.seek(0)
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        os.replace(partial, target)
        state.update(status="ready", sha256=digest, downloaded_bytes=target.stat().st_size)
        save_json(record, state)
        return target
    except (Exception, KeyboardInterrupt) as exc:
        state.update(status="paused" if isinstance(exc, (Cancelled, KeyboardInterrupt)) else "error",
                     error=str(exc) or "Загрузка прервана")
        save_json(record, state)
        raise


def download_page(page_url, output=DEFAULT_OUTPUT, progress=lambda done, total: None, cancel=None,
                  status=lambda message: None):
    status("Получаем источник первой серии…")
    items = discover(page_url)
    failures = []
    for item in items:
        if cancel is not None and cancel.is_set():
            raise Cancelled("Получение видео остановлено")
        status("Проверяем: " + item["label"])
        try:
            probe(item["url"], item.get("headers"))
        except (OSError, ValueError) as exc:
            # Do not put signed URLs into the human-readable error.
            failures.append(f'{item["label"]}: {type(exc).__name__}' + (f' {exc.code}' if isinstance(exc, HTTPError) else ''))
            if isinstance(exc, HTTPError):
                exc.close()
            continue
        status("Скачиваем: " + item["label"])
        return download(item["url"], output, page_url, progress, cancel,
                        item.get("headers"), item.get("source_id"), item.get("metadata"))
    raise ValueError("Каталог получен, но видеоисточники недоступны. " + "; ".join(failures))


def gui():
    import tkinter as tk
    from tkinter import ttk

    window = tk.Tk()
    window.title("KinoTime — прототип загрузки Zona 0.2")
    window.geometry("850x460")
    frame = ttk.Frame(window, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Ссылка на страницу фильма или URL видео:").pack(anchor="w")
    address = tk.StringVar(value="https://w1.zona.im/tvseries/dzhek-richer-2022")
    ttk.Entry(frame, textvariable=address).pack(fill="x", pady=8)
    ttk.Label(frame, text="Вставьте страницу Zona и нажмите «Скачать по странице». Для сериала берётся первая серия.").pack(anchor="w")
    choices = tk.Listbox(frame, height=6, exportselection=False)
    choices.pack(fill="both", expand=True, pady=12)
    bar = ttk.Progressbar(frame, maximum=100)
    bar.pack(fill="x")
    status = tk.StringVar(value="Готов. Основной бот и его база не изменяются.")
    ttk.Label(frame, textvariable=status, wraplength=790).pack(anchor="w", pady=8)
    events, cancel = queue.Queue(), threading.Event()
    items, busy = [], False

    def work(action):
        nonlocal busy
        if busy:
            return
        busy = True
        inspect_button.config(state="disabled")
        load_button.config(state="disabled")
        auto_button.config(state="disabled")
        cancel.clear()

        def run():
            try:
                events.put(("result", action()))
            except Exception as exc:
                events.put(("error", str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def inspect():
        url = address.get().strip()
        items.clear()
        choices.delete(0, "end")
        status.set("Ищем и проверяем источники…")
        def action():
            found = discover(url)
            # Probe individual items on download; a dead variant must not hide others.
            return ("sources", found)
        work(action)

    def load():
        selection = choices.curselection()
        if not selection:
            status.set("Сначала найдите источник и выберите его в списке.")
            return
        item, page = items[selection[0]], address.get().strip()
        status.set("Проверяем видео и начинаем загрузку…")
        def action():
            last = [0.0]
            def progress(done, total):
                if time.monotonic() - last[0] > 0.25:
                    events.put(("progress", (done, total)))
                    last[0] = time.monotonic()
            return ("file", str(download(item["url"], page_url=page, progress=progress, cancel=cancel,
                                         headers=item.get("headers"), source_id=item.get("source_id"),
                                         metadata=item.get("metadata"))))
        work(action)

    def automatic():
        page = address.get().strip()
        bar["value"] = 0
        def action():
            last = [0.0]
            def progress(done, total):
                if time.monotonic() - last[0] > 0.25:
                    events.put(("progress", (done, total)))
                    last[0] = time.monotonic()
            path = download_page(page, progress=progress, cancel=cancel,
                                 status=lambda message: events.put(("status", message)))
            return ("file", str(path))
        work(action)

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x")
    auto_button = ttk.Button(buttons, text="Скачать по странице", command=automatic)
    auto_button.pack(side="left", padx=(0, 8))
    inspect_button = ttk.Button(buttons, text="Найти источники", command=inspect)
    inspect_button.pack(side="left")
    load_button = ttk.Button(buttons, text="Скачать / продолжить", command=load)
    load_button.pack(side="left", padx=8)
    ttk.Button(buttons, text="Остановить", command=cancel.set).pack(side="left")
    ttk.Button(buttons, text="Открыть папку", command=lambda: (DEFAULT_OUTPUT.mkdir(parents=True, exist_ok=True), os.startfile(DEFAULT_OUTPUT))).pack(side="right")

    def poll():
        nonlocal busy, items
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == "status":
                    status.set(value)
                    continue
                if kind == "progress":
                    done, total = value
                    bar["value"] = done * 100 / total if total else 0
                    status.set(f"Скачано {done / 1048576:.1f} из {total / 1048576:.1f} МиБ")
                    continue
                busy = False
                inspect_button.config(state="normal")
                load_button.config(state="normal")
                auto_button.config(state="normal")
                if kind == "error":
                    status.set(value)
                elif value[0] == "sources":
                    items = value[1]
                    choices.delete(0, "end")
                    for item in items:
                        choices.insert("end", item["label"])
                    choices.selection_set(0)
                    bar["value"] = 0
                    status.set(f"Найдено источников: {len(items)}. Для сериала выбрана первая серия сезона.")
                else:
                    bar["value"] = 100
                    status.set("Готово: " + value[1])
        except queue.Empty:
            pass
        window.after(100, poll)
    window.after(100, poll)
    window.protocol("WM_DELETE_WINDOW", lambda: (cancel.set(), window.destroy()))
    window.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not args.url:
        gui()
        return
    try:
        if args.download:
            # download_page resolves fresh links and tests candidates in order.
            print(download_page(args.url, args.output, status=lambda message: print(message, flush=True)))
        else:
            items = discover(args.url)
            print(json.dumps([{**item, **(probe(item["url"], item.get("headers")) if args.probe else {})} for item in items], ensure_ascii=False, indent=2))
    except Exception as exc:
        parser.exit(1, f"Ошибка: {exc}\n")


if __name__ == "__main__":
    main()
