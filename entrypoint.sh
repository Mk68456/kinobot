#!/bin/sh
set -e

if [ "$(id -u)" = "0" ] && { [ "${2:-}" = "app.py" ] || [ "${2:-}" = "/app/app.py" ]; }; then
    # Honor DATABASE_PATH as well as DATA_DIR. Bothost may keep the DB in /app.
    # Do not hide permission failures or recursively change source ownership.
    /opt/venv/bin/python /app/tools/prepare_storage.py
    exec gosu bot "$@"
fi

# Already running as a non-root user (e.g. platform forces a fixed uid) -
# nothing to chown, just run the command as-is.
exec "$@"
