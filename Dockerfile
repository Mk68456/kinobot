# Bothost: one managed container, no Docker daemon or Compose inside it.
FROM debian:bookworm-slim AS api-build
ARG BOT_API_REVISION=e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git cmake g++ gperf libssl-dev zlib1g-dev make \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init && git remote add origin https://github.com/tdlib/telegram-bot-api.git \
    && git fetch --depth 1 origin ${BOT_API_REVISION} && git checkout FETCH_HEAD \
    && git submodule update --init --recursive --depth 1
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local \
    && cmake --build build --target install --parallel 1

FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libssl3 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home bot
RUN pip install --no-cache-dir uv==0.11.32
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_PREFERENCE=only-system \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    KINOTIME_APP_ROOT=/opt/kinotime \
    DATA_DIR=/app/data \
    BOTHOST_LOCAL_API=0
WORKDIR /opt/kinotime
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY --from=api-build /usr/local/bin/telegram-bot-api /usr/local/bin/telegram-bot-api
COPY --chown=bot:bot . .
COPY --chown=bot:bot deploy/bothost_config.py /opt/kinotime/data/config.py
# Bothost can replace /app with a bind mount. Keep the supervisor outside it.
COPY deploy/bothost_supervisor.py /usr/local/bin/kinotime-bothost.py
RUN mkdir -p /app/data && chown bot:bot /app/data
ENTRYPOINT ["/opt/venv/bin/python", "/usr/local/bin/kinotime-bothost.py"]
CMD ["/opt/venv/bin/python", "/opt/kinotime/app.py"]
