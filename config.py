"""VScan Bot — конфигурация."""

import os
import sys
import logging

log = logging.getLogger(__name__)

# ── Лицензирование ────────────────────────────────────────────
_secret_env = os.environ.get("VSCAN_SECRET_KEY")

if _secret_env:
    SECRET_KEY = _secret_env.encode()
else:
    print("ОШИБКА: Секрет VSCAN_SECRET_KEY не найден в переменных окружения!")
    sys.exit(1)

KEY_TOTAL_CHARS = 20
KEY_SEGMENT_LEN = 5

# ── GitHub Gist ───────────────────────────────────────────────
GITHUB_TOKEN  = os.environ.get("GIST_TOKEN", "")
GIST_ID       = os.environ.get("GIST_ID", "")
GIST_FILENAME = "vscan_licenses.json"

# ── Telegram ──────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = [
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

# ── Mini App ──────────────────────────────────────────────────
# Устанавливается автоматически при запуске через run.py
MINI_APP_URL = os.environ.get("MINI_APP_URL", "")

# ── Сеть ──────────────────────────────────────────────────────
REQUEST_TIMEOUT = 10
USE_FAKE_SNI    = os.environ.get("USE_FAKE_SNI", "false").lower() == "true"
FAKE_SNI        = os.environ.get("FAKE_SNI", "google.com")

_RAW_BASE = (
    f"https://gist.githubusercontent.com/raw/{GIST_ID}/{GIST_FILENAME}"
    if GIST_ID else ""
)

READ_SOURCES: list[tuple[str, bool]] = [
    (f"https://api.github.com/gists/{GIST_ID}", True),
    (_RAW_BASE, False),
    (f"https://ghproxy.com/{_RAW_BASE}", False),
] if GIST_ID else []

WRITE_ENDPOINTS: list[str] = (
    [f"https://api.github.com/gists/{GIST_ID}"] if GIST_ID else []
)
