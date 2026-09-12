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

# Static gosu binary: lets the entrypoint start as root (to fix volume
# permissions) and then drop privileges to the "bot" user before exec'ing
# the app, without pulling in a full sudo/su stack.
ADD https://github.com/tianon/gosu/releases/download/1.17/gosu-amd64 /usr/local/bin/gosu
RUN chmod +x /usr/local/bin/gosu

COPY --chown=bot:bot . .
# Keep startup outside /app, which hosting platforms may replace with a mount.
# Invoke via sh so startup does not depend on the script's executable bit.
COPY entrypoint.sh /usr/local/bin/kinobot-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/kinobot-entrypoint.sh \
    && chmod 644 /usr/local/bin/kinobot-entrypoint.sh
VOLUME ["/app/runtime"]
ENTRYPOINT ["/bin/sh", "/usr/local/bin/kinobot-entrypoint.sh"]
CMD ["/opt/venv/bin/python", "app.py"]
