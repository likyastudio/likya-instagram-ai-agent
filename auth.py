"""Yönetim paneli girişi: Google hesabıyla giriş + imzalı oturum çerezi.

- Tarayıcı, Google'ın "Google ile oturum aç" düğmesinden bir kimlik belgesi (ID token) alır.
- Sunucu bu belgeyi Google'a doğrulatır; e-posta `ADMIN_EMAILS` listesindeyse oturum açar.
- Oturum, `SESSION_SECRET` ile HMAC imzalı, HttpOnly + Secure + SameSite=Lax bir çerezdir.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx

SESSION_COOKIE = "likya_session"
SESSION_DAYS = 7
GOOGLE_TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_session(email: str, secret: str, now: float | None = None) -> str:
    payload = {"email": email, "exp": int((time.time() if now is None else now) + SESSION_DAYS * 86400)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_session(token: str, secret: str, allowed: set[str], now: float | None = None) -> str | None:
    """Geçerli ve süresi dolmamış oturumun e-postasını döndürür; değilse None."""
    if not token or not secret or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except Exception:
        return None
    if payload.get("exp", 0) < (time.time() if now is None else now):
        return None
    email = str(payload.get("email", "")).lower()
    return email if email in allowed else None


async def verify_google_credential(credential: str, client_id: str, allowed: set[str]) -> str:
    """Google ID token'ını doğrular; izinli e-postayı döndürür, aksi halde ValueError fırlatır."""
    if not credential or not client_id:
        raise ValueError("Eksik giriş bilgisi.")
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(GOOGLE_TOKENINFO, params={"id_token": credential})
    if r.status_code != 200:
        raise ValueError("Google girişi doğrulanamadı.")
    info = r.json()
    if info.get("aud") != client_id or info.get("iss") not in GOOGLE_ISSUERS:
        raise ValueError("Google girişi bu panel için verilmemiş.")
    if int(info.get("exp", 0)) < time.time():
        raise ValueError("Google girişinin süresi dolmuş.")
    if str(info.get("email_verified")).lower() != "true":
        raise ValueError("Google e-postası doğrulanmamış.")
    email = str(info.get("email", "")).lower()
    if email not in allowed:
        raise ValueError("Bu Google hesabının panele erişim izni yok.")
    return email
