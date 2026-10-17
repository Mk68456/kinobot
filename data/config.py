"""Bot credentials and administrators; keep the original server bot token."""
import os

# Paste the token of YOUR EXISTING bot here, or set BOT_TOKEN in the hosting environment.
# Leaving this empty cannot connect to a different bot by accident.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip() or ""  # Enter the existing bot token here if not using environment variables.

# Existing administrator IDs recovered from this project.
ADMIN = [6887112241]
# Optional comma/space/semicolon-separated administrator IDs from the hosting environment.
_admin_override = os.environ.get("ADMIN_IDS", "").strip()
if _admin_override:
    ADMIN = [int(value) for value in _admin_override.replace(",", " ").replace(";", " ").split()]
