"""Shared hardware selectors for the queue and processing pages."""
HARDWARE_LABELS = {'Автоматически': 'auto', 'NVIDIA NVENC': 'nvidia', 'Intel Quick Sync': 'intel',
                   'AMD AMF': 'amd', 'Процессор': 'cpu'}
LOAD_LABELS = {'Экономный': 'eco', 'Сбалансированный': 'balanced', 'Максимальная скорость': 'performance'}


class HardwareControls:
    def __init__(self, parent, hardware_value, load_value, reason, changed, check, details):
        from tkinter import ttk
        self.frame = ttk.Frame(parent, style='Card.TFrame', name='hardware_controls')
        self.frame.pack(fill='x', padx=17, pady=(0, 10))
        row = ttk.Frame(self.frame, style='Card.TFrame')
        row.pack(fill='x')
        ttk.Label(row, text='Кодирование:', style='Card.TLabel').pack(side='left', padx=(0, 6))
        self.hardware = ttk.Combobox(row, name='hardware_picker', textvariable=hardware_value,
                                    values=tuple(HARDWARE_LABELS), state='readonly', width=20)
        self.hardware.pack(side='left', padx=(0, 12))
        ttk.Label(row, text='Нагрузка:', style='Card.TLabel').pack(side='left', padx=(0, 6))
        self.load = ttk.Combobox(row, name='load_picker', textvariable=load_value,
                                values=tuple(LOAD_LABELS), state='readonly', width=20)
        self.load.pack(side='left', padx=(0, 10))
        actions = ttk.Frame(self.frame, style='Card.TFrame')
        actions.pack(fill='x', pady=(6, 0))
        self.check = ttk.Button(actions, name='hardware_check', text='Проверить', command=check)
        self.check.pack(side='left')
        self.details = ttk.Button(actions, text='Подробнее', command=details)
        self.details.pack(side='left', padx=(6, 0))
        self.reason = ttk.Label(self.frame, textvariable=reason, style='CardMuted.TLabel', wraplength=780)
        self.reason.pack(fill='x', pady=(6, 0))
        self.frame.bind('<Configure>', lambda event: self.reason.configure(wraplength=max(300, event.width - 12)))
        for selector in (self.hardware, self.load):
            selector.bind('<<ComboboxSelected>>', lambda event: changed())

    def set_busy(self, busy):
        for selector in (self.hardware, self.load):
            selector.configure(state='disabled' if busy else 'readonly')
        self.check.configure(state='disabled' if busy else 'normal')
