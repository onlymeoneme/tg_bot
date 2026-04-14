#!/usr/bin/env python3
"""
run.py — запускает всё в одном процессе:
  1. FastAPI (uvicorn) в фоновом потоке
  2. cloudflared tunnel → получает публичный HTTPS-URL
  3. Telegram бот (polling) в главном потоке
"""

import logging
import os
import re
import subprocess
import sys
import threading
import time

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

API_PORT = 8000


def _start_api():
    import uvicorn
    from api import app
    uvicorn.run(app, host="0.0.0.0", port=API_PORT, log_level="warning")


def start_api_thread():
    t = threading.Thread(target=_start_api, daemon=True, name="api")
    t.start()
    log.info("API сервер запускается на порту %d…", API_PORT)
    time.sleep(2)  # даём uvicorn время стартовать
    return t


def start_cloudflared() -> str:
    """
    Запускает `cloudflared tunnel --url http://localhost:<PORT>`
    и возвращает публичный URL вида https://xxxx.trycloudflare.com.
    Возвращает пустую строку если cloudflared не найден.
    """
    try:
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://localhost:{API_PORT}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError:
        log.warning("cloudflared не найден — Mini App URL не будет установлен")
        return ""

    log.info("cloudflared запускается…")
    url = ""
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        m = re.search(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com", line)
        if m:
            url = m.group(0)
            log.info("✅ Tunnel URL: %s", url)
            break

    if not url:
        log.warning("Не удалось получить tunnel URL")
        return ""

    # Держим туннель живым в фоне
    threading.Thread(target=proc.wait, daemon=True, name="cloudflared").start()
    return url


def main():
    # 1. Запускаем API
    start_api_thread()

    # 2. Запускаем туннель
    tunnel_url = start_cloudflared()
    if tunnel_url:
        os.environ["MINI_APP_URL"] = tunnel_url
        log.info("Mini App URL установлен: %s", tunnel_url)
    else:
        log.warning("Mini App URL не установлен; кнопка в боте будет скрыта")

    # 3. Запускаем бота (блокирующий вызов)
    from bot import main as bot_main
    bot_main()


if __name__ == "__main__":
    main()
