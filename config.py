"""
VScan Bot — конфигурация.
Все чувствительные значения берутся из переменных окружения (GitHub Secrets).
"""

import os
import logging

log = logging.getLogger(__name__)

# ── Лицензирование ────────────────────────────────────────────
# SECRET_KEY должен совпадать с ключом в клиентском приложении VScan.
# Берётся из GitHub Secret VSCAN_SECRET_KEY.
# Если не задан — используется значение из оригинального config.py.
_secret_env = os.environ.get("VSCAN_SECRET_KEY", "")
SECRET_KEY = (
    _secret_env.encode()
    if _secret_env
    else b"8e4d38088f5863581679a21bc7777881ec4eff0f842e0ed93ee2ce3d55764263"
)

KEY_TOTAL_CHARS = 20
KEY_SEGMENT_LEN = 5

# ── GitHub Gist ───────────────────────────────────────────────
GITHUB_TOKEN  = os.environ.get("GIST_TOKEN", "")
GIST_ID       = os.environ.get("GIST_ID", "")
GIST_FILENAME = "vscan_licenses.json"

# ── Telegram ──────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
# Список Telegram user_id администраторов через запятую: "123456,789012"
ADMIN_IDS = [
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

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
