FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.32
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_PREFERENCE=only-system \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/app/runtime

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
RUN /opt/venv/bin/python -c "import aiogram; assert aiogram.__version__ == '2.25.2'"

RUN useradd --uid 10001 --create-home bot && mkdir -p /app/runtime && chown bot:bot /app/runtime
COPY --chown=bot:bot . .
USER bot
VOLUME ["/app/runtime"]
CMD ["/opt/venv/bin/python", "app.py"]
