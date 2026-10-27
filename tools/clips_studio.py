"""Standalone local desktop UI; does not import bot settings or open its database."""
import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from dataclasses import asdict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from services.local_clips import (Candidate, Cancelled, Caption, Cue, audio_tracks, check_cancel, export_clip,
                                  make_captions, media_duration, parse_srt, refresh_project_srt,
                                  select_candidates, transcribe)


class Studio:
    def __init__(self, root):
        self.root = root
        root.title("KinoTime Clips — локальная мастерская")
        root.geometry("1100x820")
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.project = None
        self.candidates = []
        self.files = []
        self.vars = {key: tk.StringVar(value=value) for key, value in {
            "source": "", "srt": "", "model": str(ROOT / "runtime" / "clips" / "models" / "whisper-large-v3-turbo"), "title": "KinoTime",
            "cta": "Хочешь посмотреть фильм полностью?\nПереходи в бота!",
            "output": str(ROOT / "runtime" / "clips"), "min": "25", "max": "55",
            "start": "0", "end": "0",
            "banner": str(ROOT / "dist" / "KinoTime-banner-profile.mp4")
            if (ROOT / "dist" / "KinoTime-banner-profile.mp4").is_file() else "",
        }.items()}
        self.crop = tk.BooleanVar(value=False)
        self.style = tk.StringVar(value="reference")
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="KinoTime Clips", font=("Segoe UI", 21, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Локальная обработка • фильм выбираете вы • без платных API").pack(anchor="w", pady=(0, 12))
        form = ttk.Frame(frame)
        form.pack(fill="x")
        for row, (key, label, mode) in enumerate([
            ("source", "Фильм", "video"), ("srt", "Субтитры SRT (если есть)", "srt"),
            ("model", "Локальная модель (если нет SRT)", "dir"),
            ("output", "Папка результатов", "dir"),
            ("banner", "Рекламный баннер GIF/MP4 (если нужен)", "banner"),
            ("title", "Надпись сверху", None), ("cta", "Призыв в конце", None),
        ]):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(form, textvariable=self.vars[key]).grid(row=row, column=1, sticky="ew", padx=10)
            if mode:
                ttk.Button(form, text="Выбрать", command=lambda k=key, m=mode: self.pick(k, m)).grid(row=row, column=2)
        form.columnconfigure(1, weight=1)
        speech = ttk.Frame(frame)
        speech.pack(fill="x", pady=4)
        self.language = tk.StringVar(value="ru")
        self.audio_track = tk.StringVar(value="0: Первая аудиодорожка")
        ttk.Label(speech, text="Язык речи:").pack(side="left")
        ttk.Combobox(speech, textvariable=self.language, values=("ru", "auto", "en"),
                     state="readonly", width=6).pack(side="left", padx=5)
        ttk.Label(speech, text="Озвучка:").pack(side="left")
        self.track_box = ttk.Combobox(speech, textvariable=self.audio_track, state="readonly", width=50)
        self.track_box.pack(side="left", padx=5)
        ttk.Button(speech, text="Обновить дорожки", command=self.update_tracks).pack(side="left")
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=10)
        ttk.Label(controls, text="Длина ролика, секунды: от").pack(side="left")
        ttk.Entry(controls, width=5, textvariable=self.vars["min"]).pack(side="left", padx=6)
        ttk.Label(controls, text="до").pack(side="left")
        ttk.Entry(controls, width=5, textvariable=self.vars["max"]).pack(side="left", padx=6)
        self.analyze_button = ttk.Button(controls, text="Найти фрагменты", command=self.analyze)
        self.analyze_button.pack(side="left", padx=12)
        self.load_button = ttk.Button(controls, text="Открыть проект", command=self.load)
        self.load_button.pack(side="left")
        ttk.Button(controls, text="Отменить", command=self.cancel.set).pack(side="right")
        ttk.Label(frame, text="Оценка по текстовым признакам, не прогноз просмотров. Действия в кадре и спойлеры проверьте сами.",
                  wraplength=1000).pack(anchor="w", pady=5)
        self.tree = ttk.Treeview(frame, columns=("start", "end", "score", "reason"), show="headings", height=6)
        for key, label, width in [("start", "Начало, с", 85), ("end", "Конец, с", 85),
                                   ("score", "Балл", 55), ("reason", "Почему выбран", 700)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, stretch=key == "reason")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.selected)
        self.text = tk.Text(frame, height=3, wrap="word", font=("Segoe UI", 10))
        self.text.pack(fill="x", pady=8)
        edit = ttk.Frame(frame)
        edit.pack(fill="x")
        for key, label in [("start", "Начало, с"), ("end", "Конец, с")]:
            ttk.Label(edit, text=label).pack(side="left")
            ttk.Entry(edit, textvariable=self.vars[key], width=10).pack(side="left", padx=5)
        self.edit_button = ttk.Button(edit, text="Сохранить границы", command=self.edit)
        self.edit_button.pack(side="left", padx=8)
        self.subtitle_button = ttk.Button(edit, text="Править субтитры", command=self.edit_subtitles)
        self.subtitle_button.pack(side="left", padx=8)
        self.recognize_button = ttk.Button(edit, text="Распознать сцену заново", command=self.recognize_scene)
        self.recognize_button.pack(side="left")
        styles = ttk.Frame(frame)
        styles.pack(fill="x", pady=6)
        ttk.Label(styles, text="Стиль монтажа:").pack(side="left")
        ttk.Radiobutton(styles, text="По примеру: широкий кадр и крупные субтитры",
                        variable=self.style, value="reference").pack(side="left", padx=8)
        ttk.Radiobutton(styles, text="Обычный", variable=self.style, value="classic").pack(side="left")
        ttk.Checkbutton(frame, text="Обрезать по центру 9:16 (может обрезать героев). По умолчанию: весь кадр на размытом фоне.",
                        variable=self.crop).pack(anchor="w", pady=9)
        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        self.export_button = ttk.Button(actions, text="Смонтировать выбранные", command=self.export)
        self.export_button.pack(side="left")
        ttk.Button(actions, text="Открыть готовое видео", command=self.open_video).pack(side="left", padx=8)
        ttk.Button(actions, text="Папка результатов", command=self.open_folder).pack(side="left")
        self.status = tk.StringVar(value="Выберите фильм и SRT либо заранее скачанную модель. Ctrl/Shift — выбор нескольких сцен.")
        ttk.Label(frame, textvariable=self.status, wraplength=1000).pack(anchor="w", pady=10)
        self.bar = ttk.Progressbar(frame, mode="indeterminate")
        self.bar.pack(fill="x")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(150, self.poll)

    def pick(self, key, mode):
        if self.busy:
            return
        path = (filedialog.askdirectory() if mode == "dir" else filedialog.askopenfilename(
            filetypes=[("Субтитры", "*.srt"), ("Все файлы", "*.*")] if mode == "srt"
            else [("Баннер", "*.gif *.mp4 *.webm *.mov"), ("Все файлы", "*.*")] if mode == "banner"
            else [("Видео", "*.mp4 *.mkv *.mov *.avi *.webm"), ("Все файлы", "*.*")]))
        if path:
            self.vars[key].set(path)
            if key == "source":
                self.update_tracks()

    def update_tracks(self, force=False):
        if self.busy and not force:
            return
        try:
            tracks = audio_tracks(Path(self.vars["source"].get()))
            self.track_box.configure(values=[t["label"] for t in tracks])
            if tracks:
                preferred = next((t for t in tracks if ': rus ' in t["label"]), tracks[0])
                self.audio_track.set(preferred["label"])
        except Exception as exc:
            messagebox.showerror("Аудиодорожки", str(exc))

    def speech_options(self):
        return {"language": self.language.get(), "audio_index": int(self.audio_track.get().split(":")[0])}

    def task(self, work):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        self.bar.start()
        for button in (self.analyze_button, self.export_button, self.load_button,
                       self.edit_button, self.subtitle_button, self.recognize_button):
            button.configure(state="disabled")
        def runner():
            try:
                work()
            except Cancelled:
                self.events.put(("status", "Отменено. Уже готовые файлы сохранены."))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("done", None))
        threading.Thread(target=runner, daemon=True).start()

    def progress(self, message):
        self.events.put(("status", message))

    def analyze(self):
        values = {key: var.get().strip() for key, var in self.vars.items()}
        speech_options = self.speech_options()
        def work():
            source = Path(values["source"])
            if not source.is_file():
                raise ValueError("Выберите существующий видеофайл")
            minimum, maximum = float(values["min"]), float(values["max"])
            if not 5 <= minimum <= maximum <= 180:
                raise ValueError("Длительность: от 5 до 180 секунд; минимум не больше максимума")
            output = Path(values["output"]).resolve()
            if not values["output"]:
                raise ValueError("Выберите папку результатов")
            self.progress("Проверка видео…")
            duration = media_duration(source.resolve(), self.cancel)
            if values["srt"]:
                cues = parse_srt(Path(values["srt"]).read_text(encoding="utf-8-sig"))
            else:
                cues = transcribe(source.resolve(), values["model"], output / "cache", self.cancel,
                                  self.progress, **speech_options)
            check_cancel(self.cancel)
            candidates = select_candidates(cues, duration, minimum, maximum)
            if not candidates:
                raise ValueError("Подходящих блоков речи нет. Уменьшите минимальную длину или проверьте SRT.")
            project = {"version": 1, "source": str(source.resolve()), "duration": duration,
                       "cues": [asdict(c) for c in cues], "candidates": [asdict(c) for c in candidates],
                       "caption_edits": {}, "speech_options": speech_options}
            if values["srt"]:
                project = refresh_project_srt(project, values["srt"])
            output.mkdir(parents=True, exist_ok=True)
            import uuid
            path = output / ("project_" + uuid.uuid4().hex[:10] + ".json")
            path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
            self.events.put(("project", (project, path)))
            self.progress(f"Найдено {len(candidates)} фрагментов. Выберите сцены и запустите монтаж.")
        self.task(work)

    def load(self):
        path = filedialog.askopenfilename(filetypes=[("Проект Clips", "*.json")])
        if not path:
            return
        try:
            project = json.loads(Path(path).read_text(encoding="utf-8"))
            if project.get("version") != 1 or not Path(project["source"]).is_file():
                raise ValueError("Неподдерживаемый проект или исходный фильм перемещён")
            [Cue(**c) for c in project["cues"]]
            [Candidate(**c) for c in project["candidates"]]
            self.set_project(project, Path(path))
        except Exception as exc:
            messagebox.showerror("Не удалось открыть проект", str(exc))

    def set_project(self, project, path):
        project.setdefault("caption_edits", {})
        self.project, self.project_path = project, path
        self.candidates = [Candidate(**c) for c in project["candidates"]]
        self.vars["source"].set(project["source"])
        self.vars["srt"].set(project.get("subtitle_source", {}).get("path", ""))
        self.vars["output"].set(str(path.parent))
        self.update_tracks(force=True)
        options = project.get("speech_options", {})
        self.language.set(options.get("language", "ru"))
        index = options.get("audio_index", 0)
        labels = self.track_box.cget("values")
        self.audio_track.set(next((label for label in labels if label.startswith(f"{index}:")),
                                 f"{index}: Дорожка {index + 1}"))
        self.refresh()

    def recognize_scene(self):
        selection = self.tree.selection()
        if len(selection) != 1 or not self.project:
            messagebox.showinfo("Распознавание", "Выберите одну сцену. Её текст будет распознан заново.")
            return
        import copy
        project = copy.deepcopy(self.project)
        index = int(selection[0])
        candidate = Candidate(**project["candidates"][index])
        options = self.speech_options()
        model = self.vars["model"].get().strip()
        path = self.project_path
        def work():
            cues = transcribe(Path(project["source"]), model, path.parent / "cache",
                              self.cancel, self.progress, start=candidate.start, end=candidate.end,
                              force=True, **options)
            check_cancel(self.cancel)
            captions = make_captions(cues, candidate.start, candidate.end)
            if not captions:
                raise ValueError("Не удалось получить реплики. Прежние субтитры сохранены.")
            import uuid
            backup = path.with_name(path.stem + ".before-recognition-" + uuid.uuid4().hex[:8] + ".json")
            backup.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
            project["caption_edits"][str(index)] = [asdict(c) for c in captions]
            project["candidates"][index]["text"] = " ".join(c.text for c in cues)
            project["speech_options"] = options
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
            self.events.put(("project", (project, path)))
            self.progress("Сцена распознана заново. Проверьте текст через «Править субтитры» и повторите монтаж.")
        self.task(work)

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for index, c in enumerate(self.candidates):
            self.tree.insert("", "end", iid=str(index), values=(round(c.start, 2), round(c.end, 2), c.score, c.reason))

    def selected(self, event=None):
        selection = self.tree.selection()
        if selection:
            c = self.candidates[int(selection[0])]
            self.vars["start"].set(str(round(c.start, 2)))
            self.vars["end"].set(str(round(c.end, 2)))
            self.text.delete("1.0", "end")
            self.text.insert("1.0", c.text)

    def edit(self):
        selection = self.tree.selection()
        if len(selection) != 1:
            messagebox.showinfo("Границы", "Выберите один фрагмент")
            return
        try:
            start, end = float(self.vars["start"].get()), float(self.vars["end"].get())
            if not 0 <= start < end <= self.project["duration"] or end-start > 180:
                raise ValueError("Границы должны быть внутри фильма; длина — не более 180 секунд")
            c = self.candidates[int(selection[0])]
            c.start, c.end = start, end
            c.text = " ".join(cue["text"] for cue in self.project["cues"] if cue["end"] > start and cue["start"] < end)
            c.reason = "Границы изменены вручную; балл относится к исходному фрагменту"
            self.project["candidates"] = [asdict(c) for c in self.candidates]
            self.project["caption_edits"].pop(selection[0], None)
            self.project_path.write_text(json.dumps(self.project, ensure_ascii=False, indent=2), encoding="utf-8")
            self.refresh()
            self.tree.selection_set(selection[0])
        except Exception as exc:
            messagebox.showerror("Границы", str(exc))

    def get_captions(self, index):
        saved = self.project.get("caption_edits", {}).get(str(index))
        if saved is not None:
            return [Caption(**row) for row in saved]
        c = self.candidates[index]
        return make_captions([Cue(**row) for row in self.project["cues"]], c.start, c.end)

    def sync_subtitles(self):
        path = self.vars["srt"].get().strip()
        if not path:
            return True
        try:
            updated = refresh_project_srt(self.project, path)
            if updated is not self.project:
                import uuid
                backup = self.project_path.with_name(self.project_path.stem + ".before-srt-" + uuid.uuid4().hex[:8] + ".json")
                backup.write_text(json.dumps(self.project, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary = self.project_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(self.project_path)
                self.project = updated
                self.candidates = [Candidate(**c) for c in updated["candidates"]]
                self.selected()
                self.status.set("Новый SRT загружен. Прежние правки сохранены в резервной копии проекта.")
            return True
        except Exception as exc:
            messagebox.showerror("Не удалось обновить субтитры", str(exc))
            return False

    def edit_subtitles(self):
        selection = self.tree.selection()
        if len(selection) != 1 or not self.project:
            messagebox.showinfo("Субтитры", "Выберите один фрагмент")
            return
        if not self.sync_subtitles():
            return
        index = int(selection[0])
        candidate = self.candidates[index]
        rows = self.get_captions(index)
        dialog = tk.Toplevel(self.root)
        dialog.title("Субтитры — секунды от начала фильма")
        dialog.geometry("980x600")
        dialog.transient(self.root)
        dialog.grab_set()
        top = ttk.Frame(dialog, padding=12)
        top.pack(fill="both", expand=True)
        ttk.Label(top, text="Исправьте распознавание, время фразы и слово для цветового акцента. "
                  "Для SRT время отдельных слов приблизительное.", wraplength=930).pack(anchor="w", pady=5)
        table = ttk.Treeview(top, columns=("start", "end", "text", "accent", "color"),
                             show="headings", height=14)
        for key, label, size in [("start", "Начало", 80), ("end", "Конец", 80),
                                  ("text", "Текст", 535), ("accent", "Акцент", 115),
                                  ("color", "Цвет", 85)]:
            table.heading(key, text=label)
            table.column(key, width=size, stretch=key == "text")
        table.pack(fill="both", expand=True)
        values = {name: tk.StringVar() for name in ("start", "end", "text", "accent", "color")}
        editing = {"index": None, "original": None}
        def commit_pending():
            if editing["index"] is not None and tuple(v.get() for v in values.values()) != editing["original"]:
                rows[editing["index"]] = read_row()
                row = rows[editing["index"]]
                table.item(str(editing["index"]), values=(round(row.start, 2), round(row.end, 2),
                           row.text, row.accent, row.color))
                editing["original"] = tuple(v.get() for v in values.values())
        def refresh():
            editing["index"] = None
            table.delete(*table.get_children())
            for n, row in enumerate(rows):
                table.insert("", "end", iid=str(n), values=(round(row.start, 2), round(row.end, 2),
                             row.text, row.accent, row.color))
        def chosen(event=None):
            if table.selection():
                if int(table.selection()[0]) == editing["index"]:
                    return
                try:
                    commit_pending()
                except ValueError as exc:
                    messagebox.showerror("Субтитры", str(exc), parent=dialog)
                    table.selection_set(str(editing["index"]))
                    return
                row = rows[int(table.selection()[0])]
                for key in values:
                    values[key].set(str(getattr(row, key)))
                editing["index"] = int(table.selection()[0])
                editing["original"] = tuple(v.get() for v in values.values())
        table.bind("<<TreeviewSelect>>", chosen)
        refresh()
        form = ttk.Frame(top)
        form.pack(fill="x", pady=8)
        for column, (key, label, size) in enumerate([
            ("start", "Начало", 9), ("end", "Конец", 9), ("text", "Текст", 45),
            ("accent", "Выделить слово", 17),
        ]):
            ttk.Label(form, text=label).grid(row=0, column=column, sticky="w")
            ttk.Entry(form, textvariable=values[key], width=size).grid(row=1, column=column, sticky="ew", padx=(0, 8))
        ttk.Label(form, text="Цвет").grid(row=0, column=4, sticky="w")
        ttk.Combobox(form, textvariable=values["color"], values=("green", "yellow"),
                     state="readonly", width=9).grid(row=1, column=4)
        form.columnconfigure(2, weight=1)

        def read_row():
            start, end = float(values["start"].get()), float(values["end"].get())
            text = values["text"].get().strip()
            if not candidate.start <= start < end <= candidate.end or not text:
                raise ValueError("Время должно быть внутри фрагмента, а текст — непустым")
            return Caption(start, end, text, values["accent"].get().strip(),
                           values["color"].get() or "green")
        def replace():
            if len(table.selection()) != 1:
                messagebox.showinfo("Субтитры", "Выберите строку")
                return
            try:
                rows[int(table.selection()[0])] = read_row()
                refresh()
            except Exception as exc:
                messagebox.showerror("Субтитры", str(exc))
        def add():
            try:
                rows.append(read_row())
                rows.sort(key=lambda row: row.start)
                refresh()
            except Exception as exc:
                messagebox.showerror("Субтитры", str(exc))
        def remove():
            if len(table.selection()) == 1:
                rows.pop(int(table.selection()[0]))
                refresh()
        def save():
            try:
                commit_pending()
            except ValueError as exc:
                messagebox.showerror("Субтитры", str(exc), parent=dialog)
                return
            self.project["caption_edits"][str(index)] = [asdict(c) for c in rows]
            self.project_path.write_text(json.dumps(self.project, ensure_ascii=False, indent=2), encoding="utf-8")
            dialog.destroy()
        buttons = ttk.Frame(top)
        buttons.pack(fill="x", pady=10)
        ttk.Button(buttons, text="Применить к строке", command=replace).pack(side="left")
        ttk.Button(buttons, text="Добавить строку", command=add).pack(side="left", padx=6)
        ttk.Button(buttons, text="Удалить строку", command=remove).pack(side="left")
        ttk.Button(buttons, text="Сохранить субтитры", command=save).pack(side="right")

    def export(self):
        selection = self.tree.selection()
        if not selection or not self.project:
            messagebox.showinfo("Монтаж", "Сначала найдите и выберите фрагменты")
            return
        if not self.sync_subtitles():
            return
        source = Path(self.project["source"])
        cues = [Cue(**c) for c in self.project["cues"]]
        candidates = [Candidate(**asdict(self.candidates[int(i)])) for i in selection]
        output = Path(self.vars["output"].get()).resolve()
        title, cta, crop = self.vars["title"].get(), self.vars["cta"].get(), self.crop.get()
        banner = self.vars["banner"].get().strip() or None
        style = self.style.get()
        audio_index = self.speech_options()["audio_index"]
        caption_groups = [self.get_captions(int(i)) if style == "reference"
                          or str(i) in self.project.get("caption_edits", {}) else None for i in selection]
        def work():
            for index, (c, captions) in enumerate(zip(candidates, caption_groups), 1):
                check_cancel(self.cancel)
                self.progress(f"Монтаж {index}/{len(candidates)}. Можно отменить; готовые ролики останутся.")
                path = export_clip(source, cues, c, output, title, cta, self.cancel, crop, banner,
                                   style, captions, audio_index=audio_index)
                self.events.put(("file", path))
            self.progress(f"Готово: {len(candidates)} видео. Посмотрите их перед публикацией.")
        self.task(work)

    def open_video(self):
        if self.files:
            os.startfile(str(self.files[-1]))
        else:
            messagebox.showinfo("Видео", "Сначала смонтируйте хотя бы один ролик")

    def open_folder(self):
        folder = Path(self.vars["output"].get())
        if folder.is_dir():
            os.startfile(str(folder.resolve()))

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get()
            if kind == "status":
                self.status.set(value)
            elif kind == "error":
                self.status.set("Операция не завершена. Подробности в сообщении.")
                messagebox.showerror("KinoTime Clips", value)
            elif kind == "project":
                self.set_project(*value)
            elif kind == "file":
                self.files.append(value)
            elif kind == "done":
                self.busy = False
                self.bar.stop()
                for button in (self.analyze_button, self.export_button, self.load_button,
                               self.edit_button, self.subtitle_button, self.recognize_button):
                    button.configure(state="normal")
        self.root.after(150, self.poll)

    def close(self):
        if self.busy:
            self.cancel.set()
            self.status.set("Отмена… Дождитесь остановки и закройте окно повторно.")
        else:
            self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    Studio(window)
    window.mainloop()
