"""MemeFlow desktop launcher: local batch composition with a visual preview."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from dataclasses import asdict

from PIL import Image, ImageTk, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from services.meme_render import MemeSettings, inspect_media, preview_frame, render_meme
from services.local_clips import Cancelled, check_cancel, run_media

STATE = ROOT / "runtime" / "memeflow"
BG = "#0a1527"
CARD = "#12233b"
FIELD = "#1a304c"
INK = "#eef7ff"
MUTED = "#9bb3ce"
BLUE = "#68c7ff"


class MemeFlow:
    def __init__(self, root):
        self.root = root
        self.root.title("MemeFlow · мастерская Shorts")
        self.root.configure(bg=BG)
        icon = Image.new("RGBA", (64, 64))
        icon_draw = ImageDraw.Draw(icon)
        icon_draw.ellipse((2, 2, 62, 62), fill=BLUE)
        icon_draw.polygon((24, 16, 47, 32, 24, 48), fill="#092747")
        self.app_icon = ImageTk.PhotoImage(icon)
        self.root.iconphoto(True, self.app_icon)
        width = min(1220, root.winfo_screenwidth()-60)
        height = min(900, root.winfo_screenheight()-70)
        self.window_height = height
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(990, 650)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.preview_cancel = threading.Event()
        self.preview_generation = 0
        self.preview_active = False
        self.preview_pending = False
        self.preview_job = None
        self.busy = False
        self.demo_active = False
        self.items = []
        self.durations = {}
        self.outputs = []
        self.errors = {}
        self.controls = []
        self.image = None
        self.vars = {key: tk.StringVar(value=value) for key, value in {
            "background": "", "banner": str(ROOT / "dist" / "Telegram-Stars-banner.mp4"),
            "output": str(STATE / "готовые"), "shape": "circle", "resolution": "720",
        }.items()}
        self.size = tk.DoubleVar(value=.76)
        self.center_y = tk.DoubleVar(value=.48)
        self.crop_x = tk.DoubleVar(value=.5)
        self.crop_y = tk.DoubleVar(value=.5)
        self.banner_enabled = tk.BooleanVar(value=True)
        self.banner_chromakey = tk.BooleanVar(value=False)
        self.smooth_intro = tk.BooleanVar(value=True)
        self.at = tk.DoubleVar(value=0)
        self.load_settings()
        self.style()
        self.build()
        self.root.after(120, self.poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD)
        s.configure("TLabel", background=CARD, foreground=INK, font=("Segoe UI", 10))
        s.configure("Hint.TLabel", foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Section.TLabel", font=("Segoe UI", 12, "bold"))
        s.configure("TButton", background=FIELD, foreground=INK, borderwidth=0,
                    padding=(12, 8), font=("Segoe UI", 10))
        s.map("TButton", background=[("active", "#294765"), ("disabled", "#17273c")],
              foreground=[("disabled", "#637b97")])
        s.configure("Primary.TButton", background=BLUE, foreground="#082544", font=("Segoe UI", 11, "bold"), padding=(22, 11))
        s.map("Primary.TButton", background=[("active", "#97dcff"), ("disabled", "#243b52")])
        s.configure("TEntry", fieldbackground=FIELD, foreground=INK, insertcolor=INK, borderwidth=0, padding=8)
        s.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=INK, arrowcolor=BLUE, padding=6)
        s.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", INK)])
        s.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=INK,
                    rowheight=31, borderwidth=0, bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD, font=("Segoe UI", 9))
        s.configure("Treeview.Heading", background=CARD, foreground=MUTED, font=("Segoe UI", 9), padding=6,
                    bordercolor=CARD, lightcolor=CARD, darkcolor=CARD)
        s.map("Treeview", background=[("selected", "#245479")])
        s.configure("TCheckbutton", background=CARD, foreground=INK, font=("Segoe UI", 10))
        s.map("TCheckbutton", background=[("active", CARD)])
        s.configure("Horizontal.TProgressbar", background=BLUE, troughcolor=FIELD, borderwidth=0)
        s.configure("Horizontal.TScale", background=BLUE, troughcolor=FIELD, borderwidth=0)
        s.configure("Horizontal.TScale", bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD)

    def button(self, parent, text, command, primary=False, tracked=True):
        b = ttk.Button(parent, text=text, command=command, style="Primary.TButton" if primary else "TButton")
        if tracked:
            self.controls.append(b)
        return b

    def section(self, parent, title, subtitle):
        frame = ttk.Frame(parent, style="Card.TFrame", padding=16)
        frame.pack(fill="x", pady=(0, 12))
        ttk.Label(frame, text=title, style="Section.TLabel").pack(anchor="w")
        ttk.Label(frame, text=subtitle, style="Hint.TLabel", wraplength=630).pack(anchor="w", pady=(3, 12))
        return frame

    def build(self):
        header = tk.Frame(self.root, bg=BG, padx=24, pady=18)
        header.pack(fill="x")
        mark = tk.Canvas(header, width=48, height=48, bg=BG, highlightthickness=0)
        mark.pack(side="left", padx=(0, 13))
        mark.create_oval(2, 2, 46, 46, fill=BLUE, outline="")
        mark.create_polygon(18, 13, 35, 24, 18, 35, fill="#092747")
        names = tk.Frame(header, bg=BG)
        names.pack(side="left")
        tk.Label(names, text="MemeFlow", font=("Segoe UI", 23, "bold"), fg=INK, bg=BG).pack(anchor="w")
        tk.Label(names, text="Ваш мем. Ваш стиль. Готовый Shorts.", font=("Segoe UI", 10), fg=MUTED, bg=BG).pack(anchor="w")
        tk.Label(header, text="●  ЛОКАЛЬНО", font=("Segoe UI", 9, "bold"), fg=BLUE, bg=BG).pack(side="right")

        body = ttk.Frame(self.root, padding=(24, 0, 24, 0))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0)
        body.rowconfigure(0, weight=1)
        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        self.scroll = tk.Canvas(left, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.scroll.yview)
        scrollbar.pack(side="right", fill="y")
        self.scroll.pack(side="left", fill="both", expand=True)
        self.scroll.configure(yscrollcommand=scrollbar.set)
        inner = ttk.Frame(self.scroll)
        inner_window = self.scroll.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: self.scroll.configure(scrollregion=self.scroll.bbox("all")))
        self.scroll.bind("<Configure>", lambda e: self.scroll.itemconfigure(inner_window, width=e.width))
        self.scroll.bind_all("<MouseWheel>", self.wheel)

        queue_card = self.section(inner, "01  Добавьте мемы", "Выберите один или несколько коротких видео. Для всех будет использован один стиль.")
        toolbar = ttk.Frame(queue_card, style="Card.TFrame")
        toolbar.pack(fill="x", pady=(0, 10))
        self.button(toolbar, "+ Добавить видео", self.add_files).pack(side="left")
        self.button(toolbar, "Убрать выбранные", self.remove_files).pack(side="left", padx=7)
        self.button(toolbar, "Тестовый пример", self.demo).pack(side="right")
        self.tree = ttk.Treeview(queue_card, columns=("name", "status"), show="headings", height=4)
        self.tree.heading("name", text="Видео")
        self.tree.heading("status", text="Состояние")
        self.tree.column("name", width=360, stretch=True)
        self.tree.column("status", width=145, stretch=False)
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", self.selected)
        self.tree.bind("<Double-1>", self.show_details)
        self.detail = tk.StringVar(value="Выбранное видео появится в предпросмотре справа.")
        ttk.Label(queue_card, textvariable=self.detail, style="Hint.TLabel", wraplength=600).pack(anchor="w", pady=(8, 0))

        design = self.section(inner, "02  Настройте оформление", "Фон начинается с начала каждого ролика и повторяется до конца мема.")
        self.file_row(design, "Фоновое видео", "background", "video")
        banner_toggle = ttk.Checkbutton(design, text="Баннер сверху на протяжении всего видео", variable=self.banner_enabled,
                                       command=self.changed)
        banner_toggle.pack(anchor="w", pady=(13, 4))
        self.controls.append(banner_toggle)
        self.file_row(design, "GIF, видео или изображение", "banner", "banner")
        key_toggle = ttk.Checkbutton(design, text="Убрать зелёный фон баннера", variable=self.banner_chromakey,
                                    command=self.changed)
        key_toggle.pack(anchor="w", pady=(6, 0))
        self.controls.append(key_toggle)
        ttk.Label(design, text="Форма мема", style="Hint.TLabel").pack(anchor="w", pady=(13, 5))
        shapes = ttk.Frame(design, style="Card.TFrame")
        shapes.pack(fill="x")
        self.circle_button = self.button(shapes, "●  Круг", lambda: self.set_shape("circle"))
        self.rounded_button = self.button(shapes, "▰  Скруглённый прямоугольник", lambda: self.set_shape("rounded"))
        self.circle_button.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.rounded_button.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.sliders = []
        self.slider(design, "Размер окна", self.size, .4, .94)
        self.slider(design, "Выше / ниже на фоне", self.center_y, .3, .73)
        self.slider(design, "Кадр внутри окна: влево / вправо", self.crop_x, 0, 1)
        self.slider(design, "Кадр внутри окна: вверх / вниз", self.crop_y, 0, 1)
        intro_toggle = ttk.Checkbutton(design, text="Плавное появление: 1 с фона + 0,6 с появления",
                                      variable=self.smooth_intro, command=self.changed)
        intro_toggle.pack(anchor="w", pady=(8, 0))
        self.controls.append(intro_toggle)
        ttk.Label(design, text="Звук сохраняется из мема. Фон и баннер звучать не будут.",
                  style="Hint.TLabel", wraplength=600).pack(anchor="w", pady=(8, 0))

        export = self.section(inner, "03  Сохраните Shorts", "Каждое видео станет отдельным MP4. Исходные файлы сохраняются.")
        self.file_row(export, "Папка готовых видео", "output", "folder")
        resolution = ttk.Frame(export, style="Card.TFrame")
        resolution.pack(fill="x", pady=(10, 0))
        ttk.Label(resolution, text="Качество").pack(side="left")
        box = ttk.Combobox(resolution, values=("720", "1080"), textvariable=self.vars["resolution"], state="readonly", width=7)
        box.pack(side="left", padx=12)
        box.bind("<<ComboboxSelected>>", lambda e: self.changed())
        self.controls.append(box)
        ttk.Label(resolution, text="720 — быстрее · 1080 — больше деталей", style="Hint.TLabel").pack(side="left")

        right = ttk.Frame(body, style="Card.TFrame", padding=14)
        right.grid(row=0, column=1, sticky="ns")
        ttk.Label(right, text="Ваш Shorts", style="Section.TLabel").pack(anchor="w")
        self.preview_status = tk.StringVar(value="Добавьте мем и выберите фон")
        ttk.Label(right, textvariable=self.preview_status, style="Hint.TLabel", wraplength=310).pack(anchor="w", pady=(4, 10))
        available = max(240, min(520, self.window_height-435))
        self.preview_h = available
        self.preview_w = int(available*9/16)
        self.preview = tk.Canvas(right, width=self.preview_w, height=self.preview_h, bg="#07101d", highlightthickness=0)
        self.preview.pack(padx=(310-self.preview_w)//2 if self.preview_w < 310 else 0)
        self.placeholder()
        self.timeline = ttk.Scale(right, from_=0, to=1, orient="horizontal",
                                  variable=self.at, command=lambda v: self.schedule_preview())
        self.timeline.pack(fill="x", pady=(10, 2))
        self.time_label = tk.StringVar(value="Кадр 00:00")
        ttk.Label(right, textvariable=self.time_label, style="Hint.TLabel").pack(anchor="w")
        self.button(right, "Обновить предпросмотр", self.schedule_preview).pack(fill="x", pady=(7, 0))
        self.button(right, "Открыть выбранный результат", self.open_result, tracked=False).pack(fill="x", pady=(7, 0))

        footer = tk.Frame(self.root, bg=BG, padx=24, pady=16)
        footer.pack(fill="x")
        actions = tk.Frame(footer, bg=BG)
        actions.pack(side="right", padx=(18, 0))
        self.start_button = self.button(actions, "Создать Shorts", self.start, primary=True)
        self.start_button.pack(side="left")
        self.button(actions, "Стоп", self.stop, tracked=False).pack(side="left", padx=7)
        self.button(actions, "Папка", self.open_folder, tracked=False).pack(side="left")
        progress_area = tk.Frame(footer, bg=BG)
        progress_area.pack(fill="x", expand=True)
        self.status = tk.StringVar(value="Готов к работе. Начните с добавления видео.")
        tk.Label(progress_area, textvariable=self.status, fg=INK, bg=BG, font=("Segoe UI", 10), anchor="w").pack(fill="x")
        self.progress = ttk.Progressbar(progress_area, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(8, 0))
        self.refresh_shapes()

    def placeholder(self):
        self.preview.delete("all")
        cx, cy = self.preview_w/2, self.preview_h/2
        self.preview.create_oval(cx-30, cy-30, cx+30, cy+30, outline="#315b80", width=2)
        self.preview.create_polygon(cx-9, cy-16, cx+17, cy, cx-9, cy+16, fill=BLUE)
        self.preview.create_text(cx, cy+60, text="Здесь будет ваш ролик", fill=MUTED, font=("Segoe UI", 10))

    def file_row(self, parent, label, key, kind):
        ttk.Label(parent, text=label, style="Hint.TLabel").pack(anchor="w", pady=(0, 4))
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x")
        entry = ttk.Entry(row, textvariable=self.vars[key])
        entry.pack(side="left", fill="x", expand=True, padx=(0, 7))
        entry.bind("<FocusOut>", lambda e: self.changed())
        self.controls.append(entry)
        self.button(row, "Выбрать", lambda: self.pick(key, kind)).pack(side="right")

    def slider(self, parent, label, var, low, high):
        ttk.Label(parent, text=label, style="Hint.TLabel").pack(anchor="w", pady=(10, 0))
        scale = ttk.Scale(parent, from_=low, to=high, variable=var,
                          orient="horizontal", command=lambda v: self.changed())
        scale.pack(fill="x")
        self.sliders.append(scale)

    def wheel(self, event):
        try:
            widget = self.root.winfo_containing(event.x_root, event.y_root)
            if widget is not None and str(widget).startswith(str(self.scroll)):
                self.scroll.yview_scroll(-int(event.delta/120), "units")
        except tk.TclError:
            pass

    def settings(self):
        return MemeSettings(shape=self.vars["shape"].get(), size=self.size.get(), center_y=self.center_y.get(),
                            crop_x=self.crop_x.get(), crop_y=self.crop_y.get(),
                            resolution=int(self.vars["resolution"].get()), banner_enabled=self.banner_enabled.get(),
                             banner_chromakey=self.banner_chromakey.get(), smooth_intro=self.smooth_intro.get())

    def load_settings(self):
        try:
            saved = json.loads((STATE / "settings.json").read_text(encoding="utf-8"))
            settings = MemeSettings(**saved["settings"])
            settings.validate()
            for key in ("background", "banner", "output"):
                self.vars[key].set(saved.get(key, self.vars[key].get()))
            self.vars["shape"].set(settings.shape)
            self.vars["resolution"].set(str(settings.resolution))
            self.banner_enabled.set(settings.banner_enabled)
            self.banner_chromakey.set(settings.banner_chromakey)
            self.smooth_intro.set(saved["settings"].get("smooth_intro", True))
            for name in ("size", "center_y", "crop_x", "crop_y"):
                getattr(self, name).set(getattr(settings, name))
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def save_settings(self):
        STATE.mkdir(parents=True, exist_ok=True)
        data = {key: self.vars[key].get() for key in ("background", "banner", "output")}
        data["settings"] = asdict(self.settings())
        path = STATE / "settings.json"
        partial = path.with_suffix(".tmp")
        partial.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        partial.replace(path)

    def changed(self):
        if not self.busy:
            self.schedule_preview()

    def pick(self, key, kind):
        if self.busy:
            return
        if kind == "folder":
            path = filedialog.askdirectory(title="Папка готовых Shorts")
        else:
            types = [("Видео", "*.mp4 *.mov *.mkv *.avi *.webm")]
            if kind == "banner":
                types = [("Баннер", "*.mp4 *.gif *.webm *.mov *.png *.jpg *.jpeg *.webp")]
            path = filedialog.askopenfilename(title="Выберите файл", filetypes=types + [("Все файлы", "*.*")])
        if path:
            self.vars[key].set(path)
            if key == "background":
                self.preview_status.set("Фон выбран. Обновляю кадр…")
            self.changed()

    def add_files(self):
        if self.busy:
            return
        files = filedialog.askopenfilenames(title="Выберите мемы", filetypes=[("Видео", "*.mp4 *.mov *.mkv *.avi *.webm"), ("Все файлы", "*.*")])
        self.add_paths(files)

    def add_paths(self, files):
        existing = {str(p).casefold() for p in self.items}
        for path in files:
            path = Path(path).resolve()
            if str(path).casefold() not in existing:
                self.items.append(path)
                existing.add(str(path).casefold())
        self.refresh_queue()
        if self.items:
            self.tree.selection_set(str(len(self.items)-1))
        self.start_button.configure(text=f"Создать Shorts · {len(self.items)}")

    def refresh_queue(self):
        self.tree.delete(*self.tree.get_children())
        for n, path in enumerate(self.items):
            self.tree.insert("", "end", iid=str(n), values=(path.name, "В очереди"))

    def remove_files(self):
        if self.busy:
            return
        for index in sorted((int(i) for i in self.tree.selection()), reverse=True):
            self.items.pop(index)
        self.refresh_queue()
        self.start_button.configure(text=f"Создать Shorts · {len(self.items)}")
        if self.items:
            self.tree.selection_set("0")
        else:
            self.placeholder()

    def selected(self, event=None):
        if self.tree.selection():
            path = self.items[int(self.tree.selection()[0])]
            error = self.errors.get(str(path))
            self.detail.set((error if len(error) < 240 else "Не удалось обработать видео. Двойной щелчок по строке — подробности.")
                            if error else str(path))
            self.at.set(0)
            if not self.busy:
                self.schedule_preview()

    def show_details(self, event=None):
        if self.tree.selection():
            path = self.items[int(self.tree.selection()[0])]
            if str(path) in self.errors:
                messagebox.showerror(path.name, self.errors[str(path)])

    def set_shape(self, value):
        if not self.busy:
            self.vars["shape"].set(value)
            self.refresh_shapes()
            self.changed()

    def refresh_shapes(self):
        self.circle_button.configure(style="Primary.TButton" if self.vars["shape"].get() == "circle" else "TButton")
        self.rounded_button.configure(style="Primary.TButton" if self.vars["shape"].get() == "rounded" else "TButton")

    def schedule_preview(self):
        if self.busy:
            return
        if self.preview_job:
            self.root.after_cancel(self.preview_job)
        self.preview_job = self.root.after(350, self.request_preview)

    def request_preview(self):
        self.preview_job = None
        if self.busy or not self.tree.selection():
            return
        if not self.vars["background"].get().strip():
            self.preview_status.set("Выберите фон или нажмите «Тестовый пример»")
            return
        if self.preview_active:
            self.preview_pending = True
            self.preview_cancel.set()
            return
        meme = self.items[int(self.tree.selection()[0])]
        settings = self.settings()
        background, banner = self.vars["background"].get().strip(), self.vars["banner"].get().strip()
        at = self.at.get()
        self.preview_generation += 1
        generation = self.preview_generation
        cancel = threading.Event()
        self.preview_cancel = cancel
        self.preview_active = True
        self.preview_status.set("Обновляю кадр…")
        STATE.mkdir(parents=True, exist_ok=True)
        destination = STATE / f"preview_{generation}.png"
        def work():
            try:
                info = self.durations.get(str(meme)) or inspect_media(meme, cancel)
                self.durations[str(meme)] = info
                duration = info.duration + (1 if settings.smooth_intro else 0)
                moment = min(at, max(0, duration-.15))
                preview_frame(meme, background, banner, settings, destination, cancel, at=moment)
                self.events.put(("preview", (generation, destination, duration, moment)))
            except Cancelled:
                destination.unlink(missing_ok=True)
            except Exception as exc:
                self.events.put(("preview_error", str(exc)))
            finally:
                self.events.put(("preview_done", None))
        threading.Thread(target=work, daemon=True).start()

    def start(self):
        if self.busy:
            return
        if not self.items:
            messagebox.showinfo("MemeFlow", "Добавьте хотя бы один мем")
            return
        background, banner = self.vars["background"].get().strip(), self.vars["banner"].get().strip()
        if not Path(background).is_file():
            messagebox.showinfo("MemeFlow", "Выберите фоновое видео")
            return
        settings = self.settings()
        if settings.banner_enabled and not Path(banner).is_file():
            messagebox.showinfo("MemeFlow", "Выберите баннер или отключите его")
            return
        if not self.vars["output"].get().strip():
            messagebox.showinfo("MemeFlow", "Выберите папку результатов")
            return
        output = Path(self.vars["output"].get()).resolve()
        self.save_settings()
        files = list(self.items)
        self.busy = True
        self.preview_cancel.set()
        self.cancel.clear()
        self.errors.clear()
        self.progress["value"] = 0
        for control in self.controls:
            control.configure(state="disabled")
        for slider in self.sliders:
            slider.configure(state="disabled")
        self.timeline.configure(state="disabled")
        def work():
            success = 0
            failures = 0
            try:
                for n, meme in enumerate(files):
                    check_cancel(self.cancel)
                    self.events.put(("item", (n, "Обработка…")))
                    def progress(percent, index=n):
                        self.events.put(("progress", (index, percent, len(files))))
                    try:
                        result = render_meme(meme, background, banner, settings, output, self.cancel, progress)
                        self.events.put(("result", (n, meme, result)))
                        success += 1
                    except Cancelled:
                        self.events.put(("item", (n, "Отменено")))
                        raise
                    except Exception as exc:
                        failures += 1
                        self.events.put(("failure", (n, meme, str(exc))))
                self.events.put(("status", f"Готово: {success} · ошибок: {failures}. Результаты в выбранной папке."))
            except Cancelled:
                self.events.put(("status", f"Остановлено. Готовые видео сохранены: {success}."))
            finally:
                self.events.put(("done", None))
        threading.Thread(target=work, daemon=True).start()

    def demo(self):
        if self.busy or self.demo_active:
            return
        self.status.set("Подготавливаю тестовый мем и фон…")
        self.demo_active = True
        folder = STATE / "demo"
        folder.mkdir(parents=True, exist_ok=True)
        def work():
            try:
                background, meme = folder / "Тестовый фон.mp4", folder / "Пример мема.mp4"
                cancel = threading.Event()
                if not background.exists():
                    run_media(["-y", "-f", "lavfi", "-i",
                               "color=c=0x123c62:s=240x426:r=30,geq=r='22+10*sin(X/40+N/15)':g='65+25*sin(Y/60+N/18)':b='135+35*sin((X+Y)/90+N/20)'",
                               "-t", "2", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(background.resolve())], cancel)
                if not meme.exists():
                    poster = folder / "demo-meme.png"
                    picture = Image.new("RGB", (480, 360), "#d9efff")
                    draw = ImageDraw.Draw(picture)
                    draw.rounded_rectangle((50, 24, 430, 336), radius=38, fill="#f4fbff")
                    draw.ellipse((159, 65, 321, 227), fill="#68c7ff")
                    draw.ellipse((200, 113, 215, 131), fill="#0c294a")
                    draw.ellipse((265, 113, 280, 131), fill="#0c294a")
                    draw.arc((193, 119, 287, 192), start=0, end=180, fill="#0c294a", width=6)
                    face = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 27)
                    draw.text((240, 270), "ВАШ МЕМ — ЗДЕСЬ", font=face, fill="#153c62", anchor="mm")
                    picture.save(poster)
                    run_media(["-y", "-loop", "1", "-i", str(poster.resolve()),
                               "-vf", "zoompan=z='1+0.0005*on':d=1:s=480x360:fps=30",
                               "-t", "4", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(meme.resolve())], cancel)
                self.events.put(("demo", (background, meme)))
            except Exception as exc:
                self.events.put(("preview_error", str(exc)))
            finally:
                self.events.put(("demo_done", None))
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        if self.busy:
            self.cancel.set()
            self.status.set("Останавливаю обработку…")

    def open_result(self):
        if self.tree.selection():
            path = self.items[int(self.tree.selection()[0])]
            result = next((output for source, output in reversed(self.outputs) if source == path), None)
            if result and result.exists():
                os.startfile(str(result))
                return
        messagebox.showinfo("MemeFlow", "Выберите видео, которое уже обработано")

    def open_folder(self):
        folder = Path(self.vars["output"].get())
        if folder.is_dir():
            os.startfile(str(folder.resolve()))
        else:
            messagebox.showinfo("MemeFlow", "Папка появится после первого экспорта")

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get()
            if kind == "preview":
                generation, path, duration, at = value
                if not self.preview_pending and generation == self.preview_generation and not self.busy:
                    with Image.open(path) as picture:
                        self.image = ImageTk.PhotoImage(picture.resize((self.preview_w, self.preview_h), Image.Resampling.LANCZOS))
                    self.preview.delete("all")
                    self.preview.create_image(0, 0, image=self.image, anchor="nw")
                    self.timeline.configure(to=max(0, duration-.15))
                    self.time_label.set(f"Кадр {int(at)//60:02}:{int(at)%60:02} · {duration:.1f} с")
                    self.preview_status.set("Кадр предпросмотра · перемещайте ползунок")
                path.unlink(missing_ok=True)
            elif kind == "preview_done":
                self.preview_active = False
                if self.preview_pending:
                    self.preview_pending = False
                    self.schedule_preview()
            elif kind == "preview_error":
                self.preview_status.set(value[:180])
            elif kind == "item":
                n, state = value
                self.tree.set(str(n), "status", state)
            elif kind == "progress":
                n, percent, total = value
                self.progress["value"] = (n+percent/100)/total*100
                self.status.set(f"Видео {n+1} из {total} · {percent}%")
            elif kind == "result":
                n, source, path = value
                self.outputs.append((source, path))
                self.tree.set(str(n), "status", "✓ Готово")
            elif kind == "failure":
                n, source, error = value
                self.errors[str(source)] = error
                self.tree.set(str(n), "status", "Ошибка · выберите строку")
            elif kind == "status":
                self.status.set(value)
            elif kind == "done":
                self.busy = False
                for control in self.controls:
                    control.configure(state="readonly" if isinstance(control, ttk.Combobox) else "normal")
                for slider in self.sliders:
                    slider.configure(state="normal")
                self.timeline.configure(state="normal")
                self.save_settings()
            elif kind == "demo":
                background, meme = value
                self.vars["background"].set(str(background.resolve()))
                self.add_paths([meme])
                self.status.set("Тестовый фон готов. Позже замените его своим игровым видео.")
            elif kind == "demo_done":
                self.demo_active = False
        self.root.after(120, self.poll)

    def close(self):
        if self.busy:
            self.stop()
            self.status.set("Остановка… Закройте окно повторно после завершения.")
            return
        self.preview_cancel.set()
        self.save_settings()
        self.root.destroy()


if __name__ == "__main__":
    try:
        window = tk.Tk()
        MemeFlow(window)
        window.mainloop()
    except Exception:
        import traceback
        STATE.mkdir(parents=True, exist_ok=True)
        log = STATE / "launcher-error.log"
        log.write_text(traceback.format_exc(), encoding="utf-8")
        messagebox.showerror("MemeFlow", "Не удалось открыть программу. Подробности сохранены в " + str(log))
