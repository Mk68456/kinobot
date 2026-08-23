FROM python:3.11-slim

WORKDIR /app

# Устанавливаем uv
RUN pip install --no-cache-dir uv && pip cache purge || true

# Создаём venv вне /app — чтобы COPY . . не перезаписал его
RUN uv venv /opt/venv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV UV_PYTHON_PREFERENCE=only-system
ENV PATH="/opt/venv/bin:$PATH"

# Копируем pyproject.toml и uv.lock (если есть), устанавливаем зависимости через uv
COPY pyproject.toml .
COPY uv.lock .
RUN uv sync --frozen --no-dev --no-install-project 2>/dev/null || uv sync --no-dev --no-install-project

# Проверяем aiogram 2.25.x (уже должен быть установлен через uv sync)
RUN python -c 'from aiogram.client.default import DefaultBotProperties; print("✅ aiogram 2.25.x установлен")' 2>/dev/null || \
    (uv pip install --python /opt/venv/bin/python "aiogram==2.25.0" && echo '✅ aiogram 2.25.x установлен через uv')
RUN (uv cache prune 2>/dev/null || pip cache purge 2>/dev/null) || true

# Очищаем pip кеш
RUN pip cache purge || true

# Копируем код приложения
COPY . .
# Проверка: пакеты в /opt/venv должны быть доступны после COPY
RUN python -c 'import sys; print("Python:", sys.version[:20]); print("PATH:", __import__("os").environ.get("PATH","")[:80])' && \
    python -c 'import site; print("site-packages:", site.getsitepackages())'


# Директория для постоянных данных: БД, файлы состояния, логи.
# Монтируется как Docker volume — данные сохраняются при перезапуске.
# В коде бота используйте: import os; DATA_DIR = os.getenv('DATA_DIR', '/app/data')
ENV DATA_DIR=/app/data
RUN mkdir -p /app/data && chmod 777 /app/data
RUN chown -R $(id -u):$(id -g) /app/data 2>/dev/null || chown -R 1000:1000 /app/data || true


# Определяем точку входа
CMD ["/opt/venv/bin/python", "app.py"]