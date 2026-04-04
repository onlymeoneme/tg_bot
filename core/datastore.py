"""Потокобезопасное хранилище лицензий на базе GitHub Gist."""

import copy
import csv
import io
import json
import logging
import threading

from config import (
    GITHUB_TOKEN, GIST_FILENAME,
    READ_SOURCES, WRITE_ENDPOINTS,
)
from core.network import do_request

log = logging.getLogger(__name__)


class DataStore:
    """Единственный источник истины о лицензиях."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._db: dict = {"users": []}

    # ── чтение ───────────────────────────────────────────────

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._db)

    def users(self) -> list[dict]:
        return self.snapshot().get("users", [])

    def find(self, device_id: str) -> dict | None:
        return next((u for u in self.users() if u["device_id"] == device_id), None)

    # ── мутации ───────────────────────────────────────────────

    def upsert_user(self, user: dict) -> None:
        with self._lock:
            self._db["users"] = [
                u for u in self._db["users"]
                if u["device_id"] != user["device_id"]
            ]
            self._db["users"].append(user)

    def update_user(self, user: dict) -> None:
        with self._lock:
            self._db["users"] = [
                user if u["device_id"] == user["device_id"] else u
                for u in self._db["users"]
            ]

    def revoke(self, device_ids: set[str]) -> None:
        with self._lock:
            for u in self._db["users"]:
                if u["device_id"] in device_ids:
                    u["status"] = "REVOKED"

    def restore(self, device_ids: set[str]) -> None:
        with self._lock:
            for u in self._db["users"]:
                if u["device_id"] in device_ids:
                    u["status"] = "ACTIVE"

    def delete(self, device_ids: set[str]) -> None:
        with self._lock:
            self._db["users"] = [
                u for u in self._db["users"]
                if u["device_id"] not in device_ids
            ]

    # ── сеть ─────────────────────────────────────────────────

    def load(self) -> tuple[bool, str]:
        for url, is_api in READ_SOURCES:
            try:
                hdrs = {"Authorization": f"token {GITHUB_TOKEN}"} if is_api else {}
                _, raw = do_request("GET", url, headers=hdrs)
                data  = json.loads(raw.decode())
                db    = (
                    json.loads(data["files"][GIST_FILENAME]["content"])
                    if is_api else data
                )
                with self._lock:
                    self._db = db
                host = url.split("/")[2]
                log.info("Загружено с %s", host)
                return True, host
            except Exception as exc:
                log.warning("[READ] %s : %s", url, exc)
        return False, ""

    def save(self) -> bool:
        with self._lock:
            content = json.dumps(self._db, ensure_ascii=False, indent=2)
        payload = {"files": {GIST_FILENAME: {"content": content}}}
        hdrs = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Content-Type":  "application/json",
        }
        for url in WRITE_ENDPOINTS:
            try:
                status, _ = do_request("PATCH", url, headers=hdrs, data=payload)
                if status in (200, 201):
                    log.info("Сохранено на %s", url)
                    return True
            except Exception as exc:
                log.warning("[WRITE] %s : %s", url, exc)
        return False

    # ── экспорт ───────────────────────────────────────────────

    def export_csv(self) -> str:
        users = self.users()
        if not users:
            return ""
        fields = ["device_id", "name", "model", "os", "status", "expires_at", "license_key"]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(users)
        return buf.getvalue()
