"""Генерация лицензионных ключей VScan."""

import hmac
import hashlib
import base64

from config import SECRET_KEY, KEY_TOTAL_CHARS, KEY_SEGMENT_LEN


def create_key(device_id: str, expires_at: str) -> str:
    """Создаёт лицензионный ключ VSCAN-XXXXX-XXXXX-XXXXX-XXXXX."""
    payload = f"{device_id}:{expires_at}".encode()
    sig     = hmac.new(SECRET_KEY, payload, hashlib.sha256).digest()
    sig_b32 = base64.b32encode(sig).decode()[:KEY_TOTAL_CHARS]
    segments = [
        sig_b32[i : i + KEY_SEGMENT_LEN]
        for i in range(0, KEY_TOTAL_CHARS, KEY_SEGMENT_LEN)
    ]
    return "VSCAN-" + "-".join(segments)
