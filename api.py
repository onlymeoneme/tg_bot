"""
VScan Admin API — FastAPI бэкенд для Mini App.
Запускается через run.py вместе с ботом.
"""

import datetime
import hashlib
import hmac
import json
import logging
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import ADMIN_IDS, BOT_TOKEN
from core.license import create_key
from shared_store import store

log = logging.getLogger(__name__)

app = FastAPI(title="VScan Admin API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth ──────────────────────────────────────────────────────

def _validate_init_data(init_data: str) -> Optional[dict]:
    """Проверяет подпись Telegram WebApp initData."""
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = data.pop("hash", None)
        if not received_hash:
            return None
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed   = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, received_hash):
            return None
        return json.loads(data.get("user", "{}"))
    except Exception as exc:
        log.warning("initData validation error: %s", exc)
        return None


def require_admin(x_init_data: str = Header(default="")):
    # В режиме разработки без initData — разрешаем если ADMIN_IDS пустой
    if not x_init_data:
        if not ADMIN_IDS:
            return {"id": 0, "first_name": "Dev"}
        raise HTTPException(status_code=401, detail="Missing initData")

    user = _validate_init_data(x_init_data)
    if not user:
        raise HTTPException(status_code=403, detail="Invalid initData")
    if ADMIN_IDS and user.get("id") not in ADMIN_IDS:
        raise HTTPException(status_code=403, detail="Not an admin")
    return user


# ── Startup ───────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    ok, host = store.load()
    if ok:
        log.info("API: загружено %d лицензий с %s", len(store.users()), host)
    else:
        log.warning("API: данные не загружены при старте")


# ── Stats ─────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats(_user=Depends(require_admin)):
    users = store.users()
    total  = len(users)
    active = sum(1 for u in users if u.get("status") == "ACTIVE")
    today  = datetime.date.today().isoformat()
    expired = sum(
        1 for u in users
        if u.get("status") == "ACTIVE" and u.get("expires_at", "9999") < today
    )
    expiring = sum(
        1 for u in users
        if u.get("status") == "ACTIVE" and u.get("expires_at") and
        0 <= (datetime.date.fromisoformat(u["expires_at"]) - datetime.date.today()).days <= 7
    )
    return {
        "total":         total,
        "active":        active,
        "revoked":       total - active,
        "expired":       expired,
        "expiring_soon": expiring,
    }


# ── Users CRUD ────────────────────────────────────────────────

@app.get("/api/users")
async def list_users(q: str = "", _user=Depends(require_admin)):
    users = store.users()
    if q:
        ql = q.lower()
        users = [
            u for u in users
            if ql in (u.get("device_id","") + u.get("name","") + u.get("model","")).lower()
        ]
    return users


class NewLicense(BaseModel):
    device_id: str
    name:      str
    model:     str = "Unknown"
    os:        str = "—"
    days:      int = 30


@app.post("/api/users", status_code=201)
async def create_license(body: NewLicense, _user=Depends(require_admin)):
    expires_at = (datetime.date.today() + datetime.timedelta(days=body.days)).isoformat()
    new_user = {
        "device_id":   body.device_id,
        "name":        body.name,
        "model":       body.model,
        "os":          body.os,
        "status":      "ACTIVE",
        "expires_at":  expires_at,
        "license_key": create_key(body.device_id, expires_at),
    }
    store.upsert_user(new_user)
    if not store.save():
        raise HTTPException(status_code=500, detail="Ошибка сохранения в Gist")
    return new_user


@app.post("/api/users/{device_id}/revoke")
async def revoke(device_id: str, _user=Depends(require_admin)):
    if not store.find(device_id):
        raise HTTPException(status_code=404, detail="Не найден")
    store.revoke({device_id})
    store.save()
    return store.find(device_id)


@app.post("/api/users/{device_id}/restore")
async def restore(device_id: str, _user=Depends(require_admin)):
    if not store.find(device_id):
        raise HTTPException(status_code=404, detail="Не найден")
    store.restore({device_id})
    store.save()
    return store.find(device_id)


@app.delete("/api/users/{device_id}")
async def delete(device_id: str, _user=Depends(require_admin)):
    if not store.find(device_id):
        raise HTTPException(status_code=404, detail="Не найден")
    store.delete({device_id})
    store.save()
    return {"ok": True}


class ExtendBody(BaseModel):
    days: int


@app.post("/api/users/{device_id}/extend")
async def extend(device_id: str, body: ExtendBody, _user=Depends(require_admin)):
    u = store.find(device_id)
    if not u:
        raise HTTPException(status_code=404, detail="Не найден")
    try:
        base = datetime.date.fromisoformat(u["expires_at"])
        if base < datetime.date.today():
            base = datetime.date.today()
    except Exception:
        base = datetime.date.today()
    new_exp = (base + datetime.timedelta(days=body.days)).isoformat()
    u["expires_at"]  = new_exp
    u["license_key"] = create_key(u["device_id"], new_exp)
    store.update_user(u)
    store.save()
    return u


# ── Static (Mini App) — must be last ─────────────────────────

app.mount("/", StaticFiles(directory="mini_app", html=True), name="static")
