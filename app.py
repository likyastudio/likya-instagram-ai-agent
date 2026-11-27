"""
Likya Studios — Instagram Otomasyon & Yönetim Sistemi (v4)
==========================================================
Akış:
1. Bir gönderiye yorum gelir (Meta webhook).
2. Yoruma her zaman panelde belirlenen *standart yanıt* yazılır.
3. Yorum yapan kişiye DM gider: DM metni + o videonun herkese açık sayfa linki
   (link.likyastudios.live/<sayfa>). Sayfası olmayan videolarda genel kaynak sayfası.
4. Doğrudan gelen DM'lere Gemini yanıt verir (anahtar yoksa genel sayfa linki).

Panel (panel.likyastudios.live): Google hesabıyla giriş, gönderiler (Instagram'dan
otomatik gelir), video sayfası düzenleyici, mesaj ayarları, hareketler.
Veri: DATA_BUCKET tanımlıysa Google Cloud Storage'da kalıcı (store.py).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

import auth
import public_pages
import site_pages
import video_import
from store import DataStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("instagram-agent")

load_dotenv()

# NO_PROXY içinde [::1] temizliği (httpx uyumluluğu)
for var in ("NO_PROXY", "no_proxy"):
    if var in os.environ and "[::1]" in os.environ[var]:
        os.environ[var] = ",".join(
            p.strip() for p in os.environ[var].split(",") if p.strip() not in ("[::1]", "::1")
        )

# ---------------------------------------------------------------- Ayarlar (ortam değişkenleri)
IG_PAGE_ACCESS_TOKEN = os.getenv("IG_PAGE_ACCESS_TOKEN", "")
IG_ACCOUNT_ID = os.getenv("IG_ACCOUNT_ID", "17841425616032125")
IG_USERNAME = os.getenv("IG_USERNAME", "likyastudios").lower()
IG_APP_SECRET = os.getenv("IG_APP_SECRET", "")
IG_VERIFY_TOKEN = os.getenv("IG_VERIFY_TOKEN", "likya_verify_token_2026_secure")
IG_GRAPH_VERSION = os.getenv("IG_GRAPH_VERSION", "v21.0")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Giriş: Google (önerilen) veya PIN (Google ayarlanmamışsa yedek)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
ADMIN_PIN = os.getenv("ADMIN_PIN", "likya2026")

# Herkese açık sayfaların adresi, ör. https://link.likyastudios.live (boşsa servis adresi + /p)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
DATA_BUCKET = os.getenv("DATA_BUCKET", "")

DEFAULT_ADMIN_PIN = "likya2026"
DEFAULT_VERIFY_TOKEN = "likya_verify_token_2026_secure"

BASE_DIR = Path(__file__).parent
PROMPT_FILE = BASE_DIR / "system-prompt.txt"
DASHBOARD_FILE = BASE_DIR / "static" / "dashboard.html"
SEED_FILE = BASE_DIR / "seed-data.json"
LOCAL_DATA_FILE = BASE_DIR / "data.local.json"

RESERVED_SLUGS = {"p", "api", "auth", "brand", "webhook", "health", "static", "docs", "openapi.json", "favicon.ico", "robots.txt"}
GENERAL_KEY = "__general__"

# Bellek içi kuyruklar
DM_HISTORY: dict[str, deque] = defaultdict(lambda: deque(maxlen=15))
SEEN_EVENT_IDS: deque = deque(maxlen=1000)
RECENT_LOGS: deque = deque(maxlen=50)

STARTED_AT = datetime.now(timezone.utc)
STATS: dict = {
    "webhook_events": 0, "webhook_rejected": 0, "last_webhook_at": None, "last_rejected_at": None,
    "media_ok": None, "media_error": "", "media_checked_at": None,
}
RUNTIME: dict = {"service_base": ""}   # gelen isteklerden öğrenilen servis adresi

PIN_FAIL_WINDOW = 600
PIN_FAIL_LIMIT = 10
PIN_FAILURES: dict[str, deque] = defaultdict(lambda: deque(maxlen=PIN_FAIL_LIMIT))


def _load_seed() -> dict:
    try:
        seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    except Exception:
        seed = {}
    seed.setdefault("settings", {})
    seed.setdefault("pages", {})
    return seed


STORE = DataStore(DATA_BUCKET, "instagram-agent/data.json", LOCAL_DATA_FILE, _load_seed())

app = FastAPI(title="Likya Studios Instagram Automation", version="4.1.0")
BRAND_DIR = BASE_DIR / "static" / "brand"
app.mount("/brand", StaticFiles(directory=BRAND_DIR), name="brand")   # Likya Studios logosu ve türevleri


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(BRAND_DIR / "favicon.ico", headers={"Cache-Control": "public, max-age=86400"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@app.middleware("http")
async def remember_service_base(request: Request, call_next):
    # DM'deki linkler için, PUBLIC_BASE_URL tanımlı değilken servisin kendi adresini kullan
    host = request.headers.get("host", "")
    if host and not RUNTIME["service_base"]:
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        RUNTIME["service_base"] = f"{scheme}://{host}"
    return await call_next(request)


# ---------------------------------------------------------------- Giriş / yetki

def auth_mode() -> str:
    return "google" if (GOOGLE_CLIENT_ID and ADMIN_EMAILS and SESSION_SECRET) else "pin"


def _check_same_origin(request: Request) -> None:
    """Çerezle yapılan değiştirme isteklerinde başka siteden gelen istekleri reddeder (CSRF)."""
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(status_code=403, detail="Bu istek başka bir siteden geldi.")


def verify_pin(request: Request) -> None:
    if not ADMIN_PIN:
        return
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    fails = PIN_FAILURES[client_ip]
    while fails and now - fails[0] > PIN_FAIL_WINDOW:
        fails.popleft()
    if len(fails) >= PIN_FAIL_LIMIT:
        raise HTTPException(status_code=429, detail="Çok fazla hatalı deneme. Lütfen birkaç dakika sonra tekrar deneyin.")
    pin = request.headers.get("X-Admin-Pin", "")
    if not hmac.compare_digest(pin.encode(), ADMIN_PIN.encode()):
        fails.append(now)
        raise HTTPException(status_code=401, detail="Geçersiz Yönetim Şifresi (PIN)")
    fails.clear()


def require_admin(request: Request) -> str:
    """Yönetim API'lerine erişimi doğrular; giriş yapan kişinin kimliğini döndürür."""
    if auth_mode() == "google":
        email = auth.read_session(request.cookies.get(auth.SESSION_COOKIE, ""), SESSION_SECRET, ADMIN_EMAILS)
        if not email:
            raise HTTPException(status_code=401, detail="Lütfen Google hesabınızla giriş yapın.")
        if request.method not in ("GET", "HEAD"):
            _check_same_origin(request)
        return email
    verify_pin(request)
    return "pin"


# ---------------------------------------------------------------- Veri yardımcıları

TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜâîûÂÎÛ", "cgiosuCGIOSUaiuAIU")


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").translate(TR_MAP).lower()).strip("-")
    return s[:60].strip("-")


def public_base() -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    return (RUNTIME["service_base"] or "") + "/p"


def page_url(page: dict | None) -> str:
    """DM'de gönderilecek link: harici sayfa (ör. likyastudios.live/prompt/...) > video sayfası > genel sayfa."""
    if page and page.get("external_url"):
        return page["external_url"]
    base = public_base()
    return f"{base}/{page['slug']}" if page and page.get("slug") else base


def render_dm(template: str, link: str, title: str = "") -> str:
    text = (template or "").strip() or "Merhaba! İstediğin bağlantılar bu sayfada:\n{link}"
    if "{link}" not in text:
        text += "\n{link}"
    return text.replace("{link}", link).replace("{title}", title)[:1000]


def load_file_prompt() -> str:
    if PROMPT_FILE.exists():
        return PROMPT_FILE.read_text(encoding="utf-8").strip()
    return "Sen Likya Studios (@likyastudios) Instagram asistanısın. Samimi, enerjik ve Türkçe yanıt ver."


# ---------------------------------------------------------------- Gemini

async def call_gemini(user_prompt: str, system_prompt: str, history: list | None = None) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    contents = []
    for item in history or []:
        role = "user" if item["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": item["content"]}]})
    contents.append({"role": "user", "parts": [{"text": user_prompt}]})
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 300},
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, json=payload, headers={"x-goog-api-key": GEMINI_API_KEY})
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


# ---------------------------------------------------------------- Instagram API

MEDIA_CACHE: dict = {"at": 0.0, "items": []}
MEDIA_TTL = 120


async def fetch_instagram_media(limit: int = 30, fresh: bool = False) -> list[dict]:
    """Hesaptaki son gönderileri (küçük resimleriyle) çeker; 2 dk önbellekte tutar."""
    if not fresh and MEDIA_CACHE["items"] and time.monotonic() - MEDIA_CACHE["at"] < MEDIA_TTL:
        return MEDIA_CACHE["items"]
    STATS["media_checked_at"] = _now_iso()
    if not IG_PAGE_ACCESS_TOKEN:
        STATS["media_ok"], STATS["media_error"] = False, "IG_PAGE_ACCESS_TOKEN tanımlı değil"
        return []
    target_id = IG_ACCOUNT_ID or "me"
    url = f"https://graph.facebook.com/{IG_GRAPH_VERSION}/{target_id}/media"
    params = {
        "fields": "id,caption,media_type,permalink,timestamp,thumbnail_url,media_url",
        "limit": str(limit),
        "access_token": IG_PAGE_ACCESS_TOKEN,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params=params)
        if r.status_code == 200:
            result = []
            for item in r.json().get("data", []):
                caption = (item.get("caption") or "").strip()
                mtype = item.get("media_type", "VIDEO")
                result.append({
                    "id": str(item.get("id")),
                    "title": (caption.split("\n")[0][:90] or "Başlıksız gönderi"),
                    "caption": caption[:2200],
                    "media_type": mtype,
                    "permalink": item.get("permalink", ""),
                    "timestamp": item.get("timestamp", ""),
                    "thumb": item.get("thumbnail_url") if mtype == "VIDEO" else item.get("media_url"),
                })
            STATS["media_ok"], STATS["media_error"] = True, ""
            MEDIA_CACHE.update(at=time.monotonic(), items=result)
            return result
        try:
            meta_msg = r.json().get("error", {}).get("message", "")
        except Exception:
            meta_msg = ""
        STATS["media_ok"], STATS["media_error"] = False, f"Meta API {r.status_code}: {meta_msg}"[:300]
        logger.error(f"Medya listesi çekme hatası ({r.status_code}): {r.text[:300]}")
    except Exception as e:
        STATS["media_ok"], STATS["media_error"] = False, f"Bağlantı hatası: {e}"[:300]
        logger.error(f"Medya listesi çekme hatası: {e}")
    return MEDIA_CACHE["items"]


async def _graph_post(url: str, payload: dict, label: str) -> bool:
    if not IG_PAGE_ACCESS_TOKEN:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json=payload, headers={"Authorization": f"Bearer {IG_PAGE_ACCESS_TOKEN}"})
        if r.status_code >= 400:
            logger.error(f"{label} hatası ({r.status_code}): {r.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"{label} istek hatası: {e}")
        return False


async def send_instagram_dm(recipient_id: str, text: str) -> bool:
    url = f"https://graph.facebook.com/{IG_GRAPH_VERSION}/{IG_ACCOUNT_ID or 'me'}/messages"
    return await _graph_post(url, {"recipient": {"id": recipient_id}, "message": {"text": text[:1000]}}, "DM gönderme")


async def reply_to_comment(comment_id: str, text: str) -> bool:
    url = f"https://graph.facebook.com/{IG_GRAPH_VERSION}/{comment_id}/replies"
    return await _graph_post(url, {"message": text[:300]}, "Yorum yanıtlama")


async def send_private_reply(comment_id: str, text: str) -> bool:
    url = f"https://graph.facebook.com/{IG_GRAPH_VERSION}/{IG_ACCOUNT_ID or 'me'}/messages"
    return await _graph_post(url, {"recipient": {"comment_id": comment_id}, "message": {"text": text[:1000]}},
                             "Yorum sonrası özel DM")


CARD_DEFAULTS = {"dm_format": "card", "card_subtitle": "İstediğin bağlantılar burada 👇", "card_button": "Bağlantıyı aç"}


def card_text(value: str, limit: int) -> str:
    """Kart alanları için: {link} ve fazla boşlukları temizler, Instagram sınırına göre kısaltır."""
    text = re.sub(r"https?://\S+|\{link\}|\{title\}", "", value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def card_image_url(settings: dict) -> str:
    return (settings.get("card_image_url") or "").strip() or f"{public_pages.asset_root(public_base())}/brand/og.png"


def build_card(title: str, subtitle: str, link: str, button: str, image: str) -> dict:
    """Instagram 'generic template': görsel + başlık + kısa açıklama + tek dokunuşla açılan düğme."""
    element = {
        "title": card_text(title, 80) or "Likya Studios",
        "image_url": image,
        "default_action": {"type": "web_url", "url": link},
        "buttons": [{"type": "web_url", "url": link, "title": card_text(button, 20) or "Bağlantıyı aç"}],
    }
    sub = card_text(subtitle, 80)
    if sub:
        element["subtitle"] = sub
    return {"attachment": {"type": "template", "payload": {"template_type": "generic", "elements": [element]}}}


async def send_private_reply_card(comment_id: str, card: dict) -> bool:
    url = f"https://graph.facebook.com/{IG_GRAPH_VERSION}/{IG_ACCOUNT_ID or 'me'}/messages"
    return await _graph_post(url, {"recipient": {"comment_id": comment_id}, "message": card}, "Yorum sonrası kart DM")


# ---------------------------------------------------------------- Taslakların gönderilere otomatik bağlanması

def _log_binding(mid: str, slug: str, how: str) -> None:
    logger.info(f"'{slug}' sayfası gönderiye ({mid}) otomatik bağlandı ({how}).")
    RECENT_LOGS.append({"time": datetime.now(timezone.utc).strftime("%H:%M:%S"), "at": _now_iso(),
                        "type": "SİSTEM", "kind": "system", "user": "Otomatik eşleştirme",
                        "input": f"Yeni gönderi {mid}", "reply": f"“{slug}” sayfası bu gönderiye bağlandı ({how}).",
                        "rule": slug, "matched_rule": slug})


async def _bind_site_pages(bindings: dict[str, dict], how: str) -> list[tuple[str, str]]:
    """Site sayfalarını gönderilere bağlar; aynı adlı bekleyen taslak varsa içeriğiyle birlikte."""
    if not bindings:
        return []
    data = await STORE.load(force=True)
    done = []
    for mid, sp in bindings.items():
        if mid in data["pages"] or site_pages._used(sp, data["pages"]):
            continue
        page = site_pages.to_panel_page(sp, data.get("drafts", {}).pop(sp["slug"], None))
        page.update(auto_bound=True, bound_at=_now_iso(), bound_by=how)
        data["pages"][mid] = page
        done.append((mid, sp["slug"]))
    if done:
        try:
            await STORE.save(data)
        except Exception as e:
            logger.error(f"Otomatik bağlama kaydedilemedi: {e}")
            return []
        for mid, slug in done:
            _log_binding(mid, slug, how)
    return done


async def auto_bind_drafts(media: list[dict]) -> list[tuple[str, str]]:
    """Yeni gönderileri sayfalarına bağlar.

    1) Açıklamadaki "yoruma KELİME yaz" kelimesi sitedeki bir sayfanın kelimesiyse o sayfa (en kesin yol).
    2) Kalanlar için paneldeki hazır taslaklar (açılış saati ve açıklamadaki kelimelerle).
    """
    if not media:
        return []
    data = await STORE.load()
    done = await _bind_site_pages(
        site_pages.find_site_bindings(await site_pages.fetch_site_pages(), media, data["pages"]), "yorum kelimesi")
    data = await STORE.load(force=bool(done))
    if not data.get("drafts"):
        return done
    bindings = video_import.find_bindings(data["drafts"], media, set(data["pages"]))
    if not bindings:
        return done
    data = await STORE.load(force=True)            # kaydetmeden önce en güncel hali al
    drafted = []
    for mid, slug in bindings.items():
        draft = data["drafts"].pop(slug, None)
        if not draft or mid in data["pages"]:
            continue
        draft.update(auto_bound=True, bound_at=_now_iso())
        data["pages"][mid] = draft
        drafted.append((mid, slug))
    if drafted:
        try:
            await STORE.save(data)
        except Exception as e:
            logger.error(f"Otomatik bağlama kaydedilemedi: {e}")
            return done
        for mid, slug in drafted:
            _log_binding(mid, slug, "taslak")
    return done + drafted


# ---------------------------------------------------------------- İşleyiciler

async def handle_comment(comment_id: str, text: str, username: str = "", media_id: str = "") -> None:
    data = await STORE.load()
    settings, pages = data["settings"], data["pages"]
    page = pages.get(str(media_id))
    if page is None and media_id:
        # Yeni videoya ilk yorum: gönderi listesini tazeleyip sitedeki sayfa / hazır taslakla eşleştirmeyi dene
        known = any(m["id"] == str(media_id) for m in MEDIA_CACHE["items"])
        media = await fetch_instagram_media(fresh=not known)
        bound = await auto_bind_drafts(media)
        if not any(mid == str(media_id) for mid, _ in bound):
            # Açıklamada kelime yoksa yorumun kendisi ("SAHNE") sayfayı gösterebilir
            post = next((m for m in media if m["id"] == str(media_id)), None)
            sp = site_pages.page_for_comment(await site_pages.fetch_site_pages(), text, post, pages)
            if sp:
                bound = await _bind_site_pages({str(media_id): sp}, "yorumdaki kelime")
        if bound:
            data = await STORE.load()
            settings, pages = data["settings"], data["pages"]
            page = pages.get(str(media_id))
    if page and not page.get("is_active", True):
        page = None
    link = page_url(page)
    target = page["title"] if page else "Genel kaynak sayfası"

    public_reply = (settings.get("public_reply") or "Teşekkürler! Bağlantıları DM kutunuza gönderdik 📩")
    public_reply = public_reply.replace("{user}", f"@{username}" if username else "")
    # Gönderiye özel DM metni varsa o, yoksa ortak DM metni kullanılır
    template = (page.get("dm_message") or "").strip() if page else ""
    dm_reply = render_dm(template or settings.get("dm_template", ""), link,
                         page["title"] if page else settings.get("general_title", ""))

    comment_ok = await reply_to_comment(comment_id, public_reply)
    fmt = settings.get("dm_format") or CARD_DEFAULTS["dm_format"]
    dm_kind = "text"
    dm_ok = False
    if fmt == "card":
        # Kart: başlık = video adı, açıklama = gönderiye özel DM metni ya da ortak kart açıklaması
        subtitle = card_text(page.get("dm_message", ""), 80) if page and page.get("dm_message") else             settings.get("card_subtitle") or CARD_DEFAULTS["card_subtitle"]
        card = build_card(page["title"] if page else (settings.get("general_title") or "Likya Studios"),
                          subtitle, link, settings.get("card_button") or CARD_DEFAULTS["card_button"],
                          card_image_url(settings))
        dm_ok = await send_private_reply_card(comment_id, card)
        if dm_ok:
            dm_kind = "card"
            dm_reply = f"[Kart] {card['attachment']['payload']['elements'][0]['title']} — {subtitle} → {link}"
    if not dm_ok:
        # Kart gönderilemezse (veya düz metin seçiliyse) eski usul metin mesajı gider
        dm_ok = await send_private_reply(comment_id, dm_reply)
    status_tag = "✅" if (comment_ok and dm_ok) else ("⚠️" if (comment_ok or dm_ok) else "❌")
    RECENT_LOGS.append({
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S"), "at": _now_iso(),
        "type": "YORUM -> DM", "kind": "comment",
        "user": f"@{username}" if username else comment_id, "media_id": media_id,
        "input": text, "public_reply": public_reply, "dm_reply": dm_reply,
        "reply": f"Yorum: {public_reply} | DM: {dm_reply[:60]}...",
        "rule": target, "link": link, "comment_ok": comment_ok, "dm_ok": dm_ok, "dm_kind": dm_kind,
        "matched_rule": f"{target} {status_tag}",
    })


async def handle_dm(sender_id: str, text: str) -> None:
    data = await STORE.load()
    settings = data["settings"]
    index_link = public_base()
    if GEMINI_API_KEY:
        try:
            system_prompt = (settings.get("ai_prompt") or load_file_prompt()) + (
                f"\n\nTakipçi bir videodaki araçların/kaynakların linklerini sorarsa şu sayfayı paylaş: {index_link}"
                "\nBilmediğin bilgileri uydurma; 'Ekibimiz en kısa sürede dönüş yapacak' de.")
            DM_HISTORY[sender_id].append({"role": "user", "content": text})
            reply = await call_gemini(text, system_prompt, list(DM_HISTORY[sender_id])[:-1])
            DM_HISTORY[sender_id].append({"role": "assistant", "content": reply})
            label = "Yapay zekâ"
        except Exception as e:
            logger.error(f"DM yapay zekâ hatası: {e}")
            reply, label = render_dm(settings.get("dm_template", ""), index_link, ""), "Genel sayfa linki (AI hatası)"
    else:
        reply, label = render_dm(settings.get("dm_template", ""), index_link, ""), "Genel sayfa linki"

    ok = await send_instagram_dm(sender_id, reply)
    RECENT_LOGS.append({
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S"), "at": _now_iso(),
        "type": "DM", "kind": "dm", "user": sender_id, "input": text,
        "reply": reply, "dm_reply": reply, "rule": label, "dm_ok": ok,
        "matched_rule": f"{label} {'✅' if ok else '❌'}",
    })


def valid_signature(body: bytes, header: str | None) -> bool:
    if not IG_APP_SECRET:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(IG_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


# ---------------------------------------------------------------- Giriş uç noktaları

@app.get("/api/session")
async def session_info(request: Request):
    """Panelin hangi giriş yöntemini göstereceği (herkese açık; gizli bilgi içermez)."""
    mode = auth_mode()
    email = None
    if mode == "google":
        email = auth.read_session(request.cookies.get(auth.SESSION_COOKIE, ""), SESSION_SECRET, ADMIN_EMAILS)
    return {"mode": mode, "client_id": GOOGLE_CLIENT_ID if mode == "google" else "", "email": email}


@app.post("/auth/google")
async def google_login(request: Request):
    if auth_mode() != "google":
        raise HTTPException(status_code=400, detail="Google girişi bu sunucuda ayarlı değil.")
    _check_same_origin(request)
    data = await request.json()
    try:
        email = await auth.verify_google_credential(str(data.get("credential", "")), GOOGLE_CLIENT_ID, ADMIN_EMAILS)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    resp = JSONResponse({"status": "ok", "email": email})
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(auth.SESSION_COOKIE, auth.sign_session(email, SESSION_SECRET), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, secure=secure, samesite="lax", path="/")
    return resp


@app.post("/auth/logout")
async def logout():
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


# ---------------------------------------------------------------- Yönetim API'leri

@app.get("/api/status")
async def get_status_api(request: Request):
    who = require_admin(request)
    data = await STORE.load()
    pages = data["pages"]
    return {
        "version": app.version,
        "user": who,
        "auth_mode": auth_mode(),
        "started_at": STARTED_AT.isoformat(timespec="seconds"),
        "public_base": public_base(),
        "public_base_configured": bool(PUBLIC_BASE_URL),
        "instagram": {
            "token_set": bool(IG_PAGE_ACCESS_TOKEN), "account_id": IG_ACCOUNT_ID,
            "media_ok": STATS["media_ok"], "media_error": STATS["media_error"],
            "media_checked_at": STATS["media_checked_at"],
        },
        "webhook": {
            "signature_check": bool(IG_APP_SECRET),
            "verify_token_is_default": IG_VERIFY_TOKEN == DEFAULT_VERIFY_TOKEN,
            "events": STATS["webhook_events"], "rejected": STATS["webhook_rejected"],
            "last_event_at": STATS["last_webhook_at"], "last_rejected_at": STATS["last_rejected_at"],
        },
        "ai": {"key_set": bool(GEMINI_API_KEY), "model": GEMINI_MODEL},
        "storage": {"backend": STORE.backend, "error": STORE.last_error},
        "security": {"admin_pin_is_default": auth_mode() == "pin" and ADMIN_PIN == DEFAULT_ADMIN_PIN},
        "pages": {"total": len(pages), "active": sum(1 for p in pages.values() if p.get("is_active", True)),
                  "drafts": len(data.get("drafts", {}))},
        "logs": {"count": len(RECENT_LOGS), "capacity": RECENT_LOGS.maxlen},
    }


@app.get("/api/posts")
async def get_posts_api(request: Request, fresh: int = 0):
    """Instagram gönderileri + her birinin sayfa durumu. Yeni gönderi otomatik listelenir."""
    require_admin(request)
    media = await fetch_instagram_media(fresh=bool(fresh))
    await auto_bind_drafts(media)
    data = await STORE.load()
    pages = data["pages"]
    posts = []
    seen = set()
    for m in media:
        page = pages.get(m["id"])
        seen.add(m["id"])
        posts.append({**m, "page": page, "page_url": page_url(page) if page else None})
    # Sayfası olup son gönderiler listesinde görünmeyen eski videolar
    for mid, page in pages.items():
        if mid not in seen:
            posts.append({"id": mid, "title": page.get("title", ""), "caption": "", "media_type": "", "permalink": "",
                          "timestamp": "", "thumb": None, "page": page, "page_url": page_url(page), "older": True})
    drafts = [{**d, "page_url": page_url(d)} for d in data.get("drafts", {}).values()]
    return {"posts": posts, "drafts": drafts, "general_url": public_base(), "media_error": STATS["media_error"]}


@app.post("/api/drafts/{slug}/bind/{media_id}")
async def bind_draft_api(slug: str, media_id: str, request: Request):
    """Hazır taslağı panelden elle bir gönderiye bağlar."""
    require_admin(request)
    data = await STORE.load(force=True)
    if slug not in data.get("drafts", {}):
        raise HTTPException(status_code=404, detail="Taslak bulunamadı.")
    if media_id in data["pages"]:
        raise HTTPException(status_code=409, detail="Bu gönderinin zaten bir sayfası var.")
    draft = data["drafts"].pop(slug)
    draft.update(bound_at=_now_iso())
    data["pages"][media_id] = draft
    try:
        await STORE.save(data)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Kaydedilemedi: {e}")
    return {"status": "ok", "page_url": page_url(draft)}


@app.delete("/api/drafts/{slug}")
async def delete_draft_api(slug: str, request: Request):
    require_admin(request)
    data = await STORE.load(force=True)
    if data.get("drafts", {}).pop(slug, None) is None:
        raise HTTPException(status_code=404, detail="Taslak bulunamadı.")
    await STORE.save(data)
    return {"status": "ok"}


def _clean_page(raw: dict) -> dict:
    title = str(raw.get("title") or "").strip()[:120]
    if not title:
        raise HTTPException(status_code=422, detail="Sayfa başlığı gerekli.")
    slug = slugify(str(raw.get("slug") or "") or title)
    if not slug or slug in RESERVED_SLUGS:
        raise HTTPException(status_code=422, detail="Sayfa adresi geçersiz; başka bir adres yazın.")
    links = []
    for link in (raw.get("links") or [])[:15]:
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or "").strip()[:500]
        label = str(link.get("label") or "").strip()[:80]
        if not url and not label:
            continue
        if not public_pages.safe_url(url):
            raise HTTPException(status_code=422, detail=f"Geçersiz link: “{url or label}”. Link https:// ile başlamalı.")
        links.append({"label": label, "url": url, "note": str(link.get("note") or "").strip()[:120]})
    prompts = []
    for pr in (raw.get("prompts") or [])[:12]:
        if not isinstance(pr, dict):
            continue
        text = str(pr.get("text") or "").strip()[:4000]
        if not text:
            continue
        prompts.append({"label": str(pr.get("label") or "Prompt").strip()[:100],
                        "tool": str(pr.get("tool") or "").strip()[:100], "text": text})
    steps = [str(s).strip()[:300] for s in (raw.get("steps") or [])[:15] if str(s).strip()]
    external = str(raw.get("external_url") or "").strip()[:500]
    if external and not public_pages.safe_url(external):
        raise HTTPException(status_code=422, detail="Harici sayfa adresi https:// ile başlamalı.")
    return {"title": title, "slug": slug, "body": str(raw.get("body") or "").strip()[:2000],
            "links": links, "prompts": prompts, "steps": steps, "tip": str(raw.get("tip") or "").strip()[:600],
            "external_url": external, "dm_message": str(raw.get("dm_message") or "").strip()[:900],
            "is_active": bool(raw.get("is_active", True)), "updated_at": _now_iso()}


@app.put("/api/pages/{media_id}")
async def save_page_api(media_id: str, request: Request):
    require_admin(request)
    if not re.fullmatch(r"[0-9A-Za-z_]{1,40}", media_id) or media_id == GENERAL_KEY:
        raise HTTPException(status_code=422, detail="Geçersiz gönderi kimliği.")
    page = _clean_page(await request.json())
    data = await STORE.load(force=True)
    if page["slug"] in data.get("drafts", {}):
        raise HTTPException(status_code=409, detail=f"“{page['slug']}” adresi bekleyen bir taslakta kullanılıyor.")
    for mid, other in data["pages"].items():
        if mid != media_id and other.get("slug") == page["slug"]:
            raise HTTPException(status_code=409, detail=f"“{page['slug']}” adresi başka bir videoda kullanılıyor.")
    prev = data["pages"].get(media_id) or {}
    olds = [x for x in (prev.get("old_slugs") or []) + ([prev["slug"]] if prev.get("slug") else []) if x != page["slug"]]
    if olds:
        page["old_slugs"] = olds[-10:]
    data["pages"][media_id] = page
    try:
        await STORE.save(data)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Kaydedilemedi: {e}")
    return {"status": "ok", "page": page, "page_url": page_url(page)}


@app.delete("/api/pages/{media_id}")
async def delete_page_api(media_id: str, request: Request):
    require_admin(request)
    data = await STORE.load(force=True)
    if data["pages"].pop(media_id, None) is None:
        raise HTTPException(status_code=404, detail="Sayfa bulunamadı.")
    try:
        await STORE.save(data)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Silinemedi: {e}")
    return {"status": "ok"}


@app.get("/api/settings")
async def get_settings_api(request: Request):
    require_admin(request)
    s = (await STORE.load())["settings"]
    return {**CARD_DEFAULTS, **s, "ai_prompt": s.get("ai_prompt") or load_file_prompt(), "card_image": card_image_url(s)}


@app.post("/api/settings")
async def save_settings_api(request: Request):
    require_admin(request)
    raw = await request.json()
    reply = str(raw.get("public_reply") or "").strip()
    dm = str(raw.get("dm_template") or "").strip()
    if not reply:
        raise HTTPException(status_code=422, detail="Yorum yanıtı boş olamaz.")
    if len(reply) > 300:
        raise HTTPException(status_code=422, detail="Yorum yanıtı en fazla 300 karakter olabilir.")
    if not dm:
        raise HTTPException(status_code=422, detail="DM mesajı boş olamaz.")
    data = await STORE.load(force=True)
    data["settings"].update({
        "public_reply": reply,
        "dm_template": dm[:900],
        "general_title": str(raw.get("general_title") or "").strip()[:120] or data["settings"].get("general_title", ""),
        "general_intro": str(raw.get("general_intro") or "").strip()[:500],
        "ai_prompt": str(raw.get("ai_prompt") or "").strip()[:8000],
        "dm_format": "text" if raw.get("dm_format") == "text" else "card",
        "card_subtitle": str(raw.get("card_subtitle") or "").strip()[:80] or CARD_DEFAULTS["card_subtitle"],
        "card_button": str(raw.get("card_button") or "").strip()[:20] or CARD_DEFAULTS["card_button"],
    })
    try:
        await STORE.save(data)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Kaydedilemedi: {e}")
    return {"status": "ok"}


@app.get("/api/logs")
async def get_logs_api(request: Request):
    require_admin(request)
    return list(RECENT_LOGS)


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------- Webhook (Meta)

@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == IG_VERIFY_TOKEN:
        logger.info("Meta Webhook doğrulaması başarılı.")
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="Doğrulama başarısız")


@app.post("/webhook")
async def receive_webhook(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not valid_signature(body, request.headers.get("X-Hub-Signature-256")):
        STATS["webhook_rejected"] += 1
        STATS["last_rejected_at"] = _now_iso()
        raise HTTPException(status_code=401, detail="Geçersiz imza")
    STATS["webhook_events"] += 1
    STATS["last_webhook_at"] = _now_iso()

    try:
        data = json.loads(body)
    except ValueError:   # bozuk JSON veya UTF-8 olmayan gövde
        return JSONResponse({"status": "invalid_json"}, status_code=400)
    logger.info(f"Webhook alındı: {data}")

    for entry in data.get("entry", []):
        for msg_event in entry.get("messaging", []):
            message = msg_event.get("message", {})
            mid, text = message.get("mid"), message.get("text")
            sender_id = msg_event.get("sender", {}).get("id")
            if mid and mid not in SEEN_EVENT_IDS and text and not message.get("is_echo") and str(sender_id) != IG_ACCOUNT_ID:
                SEEN_EVENT_IDS.append(mid)
                background.add_task(handle_dm, sender_id, text)

        comment_candidates = [c.get("value", {}) for c in entry.get("changes", []) if c.get("field") == "comments"]
        if entry.get("field") == "comments":
            comment_candidates.append(entry.get("value", {}))
        for val in comment_candidates:
            if not isinstance(val, dict):
                continue
            cid, ctext = val.get("id"), val.get("text")
            from_obj = val.get("from", {})
            cuser, from_id = from_obj.get("username", ""), str(from_obj.get("id", ""))
            media_id = val.get("media", {}).get("id", "")
            # Kendi yorumumuza / botun yanıtına tekrar yanıt verme
            if (from_id and from_id == IG_ACCOUNT_ID) or (cuser and cuser.lower() == IG_USERNAME):
                continue
            if cid and cid not in SEEN_EVENT_IDS and ctext:
                SEEN_EVENT_IDS.append(cid)
                background.add_task(handle_comment, cid, ctext, cuser, media_id)
    return {"status": "ok"}


# ---------------------------------------------------------------- Herkese açık video sayfaları

def _is_public_host(request: Request) -> bool:
    if not PUBLIC_BASE_URL:
        return False
    p = urlparse(PUBLIC_BASE_URL)
    return p.path in ("", "/") and request.headers.get("host", "").split(":")[0] == p.hostname


async def _thumbs() -> dict[str, str]:
    try:
        return {m["id"]: m.get("thumb") for m in await fetch_instagram_media()}
    except Exception:
        return {}


async def _public_index() -> HTMLResponse:
    data = await STORE.load()
    thumbs = await _thumbs()
    order = {mid: i for i, mid in enumerate(thumbs)}
    items = [{"title": p["title"], "href": page_url(p), "thumb": thumbs.get(mid),
              "link_count": len(p.get("links") or []), "prompt_count": len(p.get("prompts") or []),
              "_o": order.get(mid, 10_000)}
             for mid, p in data["pages"].items() if p.get("is_active", True) and mid != GENERAL_KEY]
    items.sort(key=lambda x: x["_o"])
    s = data["settings"]
    html = public_pages.render_index(s.get("general_title") or "Likya Studios", s.get("general_intro", ""), items, public_base())
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=60"})


async def _public_page(slug: str) -> HTMLResponse:
    data = await STORE.load()
    for mid, page in data["pages"].items():
        if page.get("slug") == slug and page.get("is_active", True):
            thumb = (await _thumbs()).get(mid)
            return HTMLResponse(public_pages.render_video_page(page, thumb, public_base(), public_base()),
                                headers={"Cache-Control": "public, max-age=60"})
    for page in data["pages"].values():        # adresi değişmiş sayfa: eski linkler yeni sayfaya gitsin
        if slug in (page.get("old_slugs") or []):
            return RedirectResponse(page_url(page), status_code=301)
    draft = data.get("drafts", {}).get(slug)   # video henüz paylaşılmadıysa da sayfa açılır
    if draft and draft.get("is_active", True):
        return HTMLResponse(public_pages.render_video_page(draft, None, public_base(), public_base()),
                            headers={"Cache-Control": "public, max-age=60"})
    return HTMLResponse(public_pages.render_not_found(public_base()), status_code=404)


@app.get("/p", include_in_schema=False)
async def public_index_path():
    return await _public_index()


@app.get("/p/{slug}", include_in_schema=False)
async def public_page_path(slug: str):
    return await _public_page(slug)


@app.get("/", include_in_schema=False)
async def root(request: Request):
    if _is_public_host(request):
        return await _public_index()
    # Yönetim paneli kabuğu: veri gömülmez, her şey yetkili API'lerden gelir
    return FileResponse(DASHBOARD_FILE, media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow",
                                 "X-Frame-Options": "DENY", "Referrer-Policy": "strict-origin-when-cross-origin"})


@app.get("/{slug}", include_in_schema=False)
async def public_page_root(slug: str, request: Request):
    if _is_public_host(request):
        return await _public_page(slug)
    raise HTTPException(status_code=404, detail="Bulunamadı")
