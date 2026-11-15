"""
Likya Studios — Instagram Otomasyon & AI Yönetim Sistemi (Pro Sürüm)
====================================================================
Özellikler:
1. Web Yönetim Paneli (PIN korumalı — ADMIN_PIN ortam değişkeni; panel: static/dashboard.html)
2. Instagram Reels/Gönderi Seçici (Canlı Meta API'den videoları otomatik çeker)
3. Anti-Spam Yorum Varyasyonları (| ile ayrılmış çoklu yanıtlar arasından rastgele seçim)
4. Kural Bazlı Otomatik Yorum Yanıtlama + Özel DM (Filigransız / ManyChat'siz)
5. Google Gemini AI Asistanı (Kural eşleşmeyen durumlarda ve DM sohbetlerinde)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import random
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

# Loglama
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("instagram-agent")

load_dotenv()

# NO_PROXY içinde [::1] temizliği (httpx uyumluluğu)
for var in ("NO_PROXY", "no_proxy"):
    if var in os.environ and "[::1]" in os.environ[var]:
        os.environ[var] = ",".join(
            p.strip() for p in os.environ[var].split(",") if p.strip() not in ("[::1]", "::1")
        )

# Meta & Google Ayarları
IG_PAGE_ACCESS_TOKEN = os.getenv("IG_PAGE_ACCESS_TOKEN", "")
IG_ACCOUNT_ID = os.getenv("IG_ACCOUNT_ID", "17841425616032125")
IG_APP_SECRET = os.getenv("IG_APP_SECRET", "")
IG_VERIFY_TOKEN = os.getenv("IG_VERIFY_TOKEN", "likya_verify_token_2026_secure")
IG_GRAPH_VERSION = os.getenv("IG_GRAPH_VERSION", "v21.0")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Yönetim Paneli Şifresi
ADMIN_PIN = os.getenv("ADMIN_PIN", "likya2026")

RULES_FILE = Path(__file__).parent / "rules.json"
PROMPT_FILE = Path(__file__).parent / "system-prompt.txt"
DASHBOARD_FILE = Path(__file__).parent / "static" / "dashboard.html"

DEFAULT_ADMIN_PIN = "likya2026"
DEFAULT_VERIFY_TOKEN = "likya_verify_token_2026_secure"

# Bellek içi kuyruklar
DM_HISTORY: dict[str, deque] = defaultdict(lambda: deque(maxlen=15))
SEEN_EVENT_IDS: deque = deque(maxlen=1000)
RECENT_LOGS: deque = deque(maxlen=50)

# Panelde gösterilen çalışma durumu (bellekte; sunucu yeniden başlayınca sıfırlanır)
STARTED_AT = datetime.now(timezone.utc)
STATS: dict = {
    "webhook_events": 0,          # imzası geçerli webhook istekleri
    "webhook_rejected": 0,        # imzası geçersiz olduğu için reddedilenler
    "last_webhook_at": None,      # son geçerli webhook zamanı (ISO)
    "last_rejected_at": None,
    "media_ok": None,             # son medya listesi çekimi başarılı mı
    "media_error": "",
    "media_checked_at": None,
}

# PIN deneme sınırı: aynı IP'den 10 dakikada 10 hatalı denemeden sonra geçici blok
PIN_FAIL_WINDOW = 600
PIN_FAIL_LIMIT = 10
PIN_FAILURES: dict[str, deque] = defaultdict(lambda: deque(maxlen=PIN_FAIL_LIMIT))

app = FastAPI(title="Likya Studios Instagram Automation Pro", version="3.1.0")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- Yardımcılar & Kurallar

def verify_pin(request: Request) -> None:
    """Yönetim API'lerinde X-Admin-Pin başlığını zorunlu olarak doğrular.

    Eski sürüm başlık boş gönderildiğinde isteği kabul ediyordu; bu, şifresiz herkesin
    kuralları ve AI talimatını değiştirebilmesi demekti. Artık PIN her zaman gerekir.
    """
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


def load_rules() -> list[dict]:
    """rules.json dosyasından kuralları okur."""
    if RULES_FILE.exists():
        try:
            with open(RULES_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"rules.json okuma hatası: {e}")

    default_rules = [
        {
            "id": "rule_stirling_pdf",
            "name": "Stirling PDF İndirme Linki",
            "media_id": "17917802667556092",
            "keywords": "pdf, stirling, indir, link",
            "public_reply": "Harika! İstediğiniz indirme bağlantısını hemen DM kutunuza ilettik ✨ | Selamlar! Stirling PDF linkini DM'den gönderdik 🚀 | DM kutunuzu kontrol edebilirsiniz, indirme bağlantısını ilettik 📩 | İstediğiniz bağlantıyı özel mesaj olarak gönderdik, keyifli çalışmalar! 💛",
            "dm_message": "Selam! Gönderimizde bahsettiğimiz Stirling PDF bağlantısı burada 👇\n\n📄 İndirme Bağlantısı: https://www.stirling.com\n📘 GitHub: https://github.com/Stirling-Tools/Stirling-PDF\n\nBireysel kullanım tamamen ücretsizdir. Keyifli çalışmalar dileriz! 💛",
            "is_active": True,
        },
        {
            "id": "rule_google_tools",
            "name": "Google Ücretsiz 7 Yapay Zeka Aracı",
            "media_id": "18154159846505090",
            "keywords": "google, araç, arac, liste, link",
            "public_reply": "Süper! 7 ücretsiz Google yapay zeka aracının listesini ve linklerini DM'den gönderdik 🧠✨ | Harika! Tüm araçların bağlantılarını DM kutunuza ilettik 🚀 | DM kutunuzu kontrol edin, Google AI listesini hemen paylaştık 📩",
            "dm_message": "Merhaba! Videomuzda bahsettiğimiz 7 ücretsiz Google yapay zeka aracının listesi ve bağlantıları:\n\n1️⃣ Gemini Notebook: https://notebooklm.google.com\n2️⃣ Google AI Studio: https://aistudio.google.com\n3️⃣ Stitch: https://labs.google\n4️⃣ Antigravity: https://idx.dev\n5️⃣ Lyria & Gemini: https://gemini.google.com\n6️⃣ Flow (Veo): https://labs.google/fx\n7️⃣ Öğrenci İndirimi: https://gemini.google.com/students\n\nFaydalı olması dileğiyle! 🚀",
            "is_active": True,
        },
        {
            "id": "rule_5_ai_tools",
            "name": "Ücretsiz 5 Yapay Zeka Aracı",
            "media_id": "18113791415261080",
            "keywords": "araç, arac, 1, 2, 3, 4, 5, link",
            "public_reply": "Harika seçim! 5 ücretsiz yapay zeka aracının tüm linklerini DM kutunuza ilettik 🧠✨ | Selamlar! Araçların indirme bağlantılarını DM'den gönderdik 🚀 | Bağlantıları özel mesaj olarak ilettik, DM kutunuzu kontrol edebilirsiniz 📩",
            "dm_message": "Selam! Videomuzdaki 5 ücretsiz yapay zeka aracının doğrudan bağlantıları 👇\n\n1️⃣ Gemini Notebook: https://notebooklm.google.com\n2️⃣ Google AI Studio: https://aistudio.google.com\n3️⃣ Ollama: https://ollama.com\n4️⃣ Whisper: https://github.com/openai/whisper\n5️⃣ Upscayl: https://upscayl.org\n\nKeyifli denemeler! ✨",
            "is_active": True,
        },
        {
            "id": "rule_localsend",
            "name": "LocalSend Dosya Paylaşım Aracı",
            "media_id": "18106100525456225",
            "keywords": "github, localsend, airdrop, link, indir",
            "public_reply": "Harika bir araç! LocalSend indirme ve GitHub bağlantısını DM kutunuza ilettik 📱💻 | Selamlar! LocalSend linkini hemen DM'den paylaştık 🚀 | DM kutunuzu kontrol edebilirsiniz, indirme bağlantısını gönderdik ✨",
            "dm_message": "Selam! Videomuzda bahsettiğimiz açık kaynak dosya aktarım aracı LocalSend linkleri:\n\n🔗 Resmi Web Sitesi & İndir: https://localsend.org\n⭐ GitHub Sayfası: https://github.com/localsend/localsend\n\nAynı Wi-Fi ağında internetsiz ve şifreli çalışır. Keyifle kullanın! ✨",
            "is_active": True,
        },
        {
            "id": "rule_genel_destek",
            "name": "Genel Anahtar Kelime Yakalayıcı (Tüm Videolar)",
            "media_id": "all",
            "keywords": "pdf, google, github, fiyat, ucret, bilgi",
            "public_reply": "Harika! İstediğiniz bağlantıyı ve detayları hemen DM kutunuza ilettik ✨ | Selamlar! İlgili linkleri DM üzerinden sizinle paylaştık 🚀 | DM kutunuzu kontrol edebilirsiniz, detayları gönderdik 📩",
            "dm_message": "Merhaba! Likya Studios içeriklerine gösterdiğiniz ilgi için teşekkür ederiz 🤝\n\nEn popüler açık kaynak & yapay zeka araç linklerimiz:\n📄 Stirling PDF: https://www.stirling.com\n📱 LocalSend: https://localsend.org\n🧠 Google AI Studio: https://aistudio.google.com\n🌐 Web Sitemiz: https://likyastudios.com",
            "is_active": True,
        },
    ]
    save_rules(default_rules)
    return default_rules


def save_rules(rules: list[dict]) -> None:
    """Kuralları rules.json dosyasına kaydeder."""
    try:
        with open(RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"rules.json kaydetme hatası: {e}")


def load_system_prompt() -> str:
    if PROMPT_FILE.exists():
        return PROMPT_FILE.read_text(encoding="utf-8").strip()
    return (
        "Sen Likya Studios (@likyastudios) Instagram hesabının yapay zeka asistanısın. "
        "Samimi, enerjik, profesyonel ve Türkçe yanıtlar ver. "
        "Gönderi yorumlarına maksimum 1-2 cümlelik sıcak yanıt ver ve detayların DM'den iletildiğini söyle."
    )


def pick_variation(reply_template: str, username: str = "") -> str:
    """'|' ile ayrılmış yanıt şablonları arasından rastgele birini seçer ve @kullanici_adi ekler."""
    if not reply_template:
        return "Harika! Detayları DM kutunuza ilettik ✨"
    options = [opt.strip() for opt in reply_template.split("|") if opt.strip()]
    chosen = random.choice(options) if options else reply_template.strip()
    if username and "{user}" in chosen:
        chosen = chosen.replace("{user}", f"@{username}")
    return chosen


def find_matching_rule(comment_text: str, media_id: str = "") -> dict | None:
    """
    Önce videoya özel (media_id eşleşen) kuralları arar,
    bulamazsa 'all' (tüm gönderiler) kurallarına bakar.
    """
    rules = load_rules()
    lower_text = comment_text.lower().strip()

    # 1. Aşama: Sadece bu videoya (media_id) özel aktif kurallar
    if media_id:
        for rule in rules:
            if not rule.get("is_active", True):
                continue
            if str(rule.get("media_id", "")).strip() == str(media_id).strip():
                if _matches_keywords(lower_text, rule.get("keywords", "")):
                    return rule

    # 2. Aşama: Tüm gönderiler ("all") için geçerli aktif kurallar
    for rule in rules:
        if not rule.get("is_active", True):
            continue
        rule_media = str(rule.get("media_id", "all")).strip()
        if rule_media in ("all", "", "*"):
            if _matches_keywords(lower_text, rule.get("keywords", "")):
                return rule

    return None


def _matches_keywords(lower_text: str, keywords_str: str) -> bool:
    keywords_str = keywords_str.strip()
    if keywords_str in ("*", "hepsi", "all", ""):
        return True
    keywords = [k.strip().lower() for k in keywords_str.split(",") if k.strip()]
    return any(kw in lower_text for kw in keywords)


# ---------------------------------------------------------------- Gemini LLM

async def call_gemini(user_prompt: str, context_type: str = "DM", history: list | None = None) -> str:
    """Google Gemini modelini çağırır."""
    if not GEMINI_API_KEY:
        if context_type == "COMMENT":
            return "Yorumunuz için çok teşekkürler! Detayları DM'den paylaştık ✨"
        return "Merhaba! Mesajınız için teşekkürler, ekibimiz en kısa sürede dönüş sağlayacaktır."

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    instruction = f"{load_system_prompt()}\n\n[Şu anki mod: {context_type}]"
    if context_type == "COMMENT":
        instruction += "\nKural: Bu bir Instagram gönderi altındaki yorumdur. Maksimum 1-2 cümlelik, samimi ve emojili yanıt ver."

    contents = []
    if history and context_type == "DM":
        for item in history:
            role = "user" if item["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": item["content"]}]})
    contents.append({"role": "user", "parts": [{"text": user_prompt}]})

    payload = {
        "system_instruction": {"parts": [{"text": instruction}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 300},
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


# ---------------------------------------------------------------- Instagram API

async def fetch_instagram_media(limit: int = 15) -> list[dict]:
    """Hesaptaki son gönderi ve Reels videolarını çeker."""
    STATS["media_checked_at"] = _now_iso()
    if not IG_PAGE_ACCESS_TOKEN:
        STATS["media_ok"] = False
        STATS["media_error"] = "IG_PAGE_ACCESS_TOKEN tanımlı değil"
        return []
    target_id = IG_ACCOUNT_ID if IG_ACCOUNT_ID else "me"
    url = f"https://graph.facebook.com/{IG_GRAPH_VERSION}/{target_id}/media"
    params = {
        "fields": "id,caption,media_type,permalink,timestamp",
        "limit": str(limit),
        "access_token": IG_PAGE_ACCESS_TOKEN,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params=params)
            if r.status_code == 200:
                items = r.json().get("data", [])
                result = []
                for item in items:
                    caption = (item.get("caption") or "Başlıksız Gönderi").strip()
                    first_line = caption.split("\n")[0][:65]
                    result.append({
                        "id": item.get("id"),
                        "title": first_line,
                        "media_type": item.get("media_type", "VIDEO"),
                        "permalink": item.get("permalink", ""),
                        "timestamp": item.get("timestamp", ""),
                    })
                STATS["media_ok"] = True
                STATS["media_error"] = ""
                return result
            # Meta hata mesajını (token süresi doldu vb.) panelde göstermek için sakla
            try:
                meta_msg = r.json().get("error", {}).get("message", "")
            except Exception:
                meta_msg = ""
            STATS["media_ok"] = False
            STATS["media_error"] = f"Meta API {r.status_code}: {meta_msg}"[:300]
            logger.error(f"Medya listesi çekme hatası ({r.status_code}): {r.text[:300]}")
    except Exception as e:
        STATS["media_ok"] = False
        STATS["media_error"] = f"Bağlantı hatası: {e}"[:300]
        logger.error(f"Medya listesi çekme hatası: {e}")
    return []


async def send_instagram_dm(recipient_id: str, text: str) -> bool:
    if not IG_PAGE_ACCESS_TOKEN:
        return False
    target_id = IG_ACCOUNT_ID if IG_ACCOUNT_ID else "me"
    url = f"https://graph.facebook.com/{IG_GRAPH_VERSION}/{target_id}/messages"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                url,
                json={"recipient": {"id": recipient_id}, "message": {"text": text[:1000]}},
                headers={"Authorization": f"Bearer {IG_PAGE_ACCESS_TOKEN}"},
            )
            if r.status_code >= 400:
                logger.error(f"DM Gönderme Hatası ({r.status_code}): {r.text}")
                return False
            return True
    except Exception as e:
        logger.error(f"DM İstek Hatası: {e}")
        return False


async def reply_to_comment(comment_id: str, text: str) -> bool:
    if not IG_PAGE_ACCESS_TOKEN:
        return False
    url = f"https://graph.facebook.com/{IG_GRAPH_VERSION}/{comment_id}/replies"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                url,
                json={"message": text[:300]},
                headers={"Authorization": f"Bearer {IG_PAGE_ACCESS_TOKEN}"},
            )
            if r.status_code >= 400:
                logger.error(f"Yorum Yanıtlama Hatası ({r.status_code}): {r.text}")
                return False
            return True
    except Exception as e:
        logger.error(f"Yorum Yanıtlama İstek Hatası: {e}")
        return False


async def send_private_reply(comment_id: str, text: str) -> bool:
    if not IG_PAGE_ACCESS_TOKEN:
        return False
    target_id = IG_ACCOUNT_ID if IG_ACCOUNT_ID else "me"
    url = f"https://graph.facebook.com/{IG_GRAPH_VERSION}/{target_id}/messages"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                url,
                json={"recipient": {"comment_id": comment_id}, "message": {"text": text[:1000]}},
                headers={"Authorization": f"Bearer {IG_PAGE_ACCESS_TOKEN}"},
            )
            if r.status_code >= 400:
                logger.error(f"Özel DM Yanıtlama Hatası ({r.status_code}): {r.text}")
                return False
            return True
    except Exception as e:
        logger.error(f"Özel DM Yanıtlama İstek Hatası: {e}")
        return False


# ---------------------------------------------------------------- İşleyiciler

async def handle_dm(sender_id: str, text: str) -> None:
    # Önce DM içinde bir anahtar kelime kuralı tetikleniyor mu kontrol et
    matched = find_matching_rule(text, "all")
    if matched and matched.get("dm_message"):
        reply = matched["dm_message"]
        rule_label = f"Kural: {matched.get('name')}"
    else:
        try:
            DM_HISTORY[sender_id].append({"role": "user", "content": text})
            reply = await call_gemini(text, "DM", list(DM_HISTORY[sender_id])[:-1])
            DM_HISTORY[sender_id].append({"role": "assistant", "content": reply})
            rule_label = "Gemini AI"
        except Exception as e:
            logger.error(f"DM Hatası: {e}")
            reply = "Mesajınız için teşekkür ederiz, ekibimiz en kısa sürede dönüş sağlayacaktır."
            rule_label = "Yedek Yanıt"

    ok = await send_instagram_dm(sender_id, reply)
    RECENT_LOGS.append({
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "at": _now_iso(),
        "type": "DM",
        "kind": "dm",
        "user": sender_id,
        "input": text,
        "reply": reply,
        "dm_reply": reply,
        "rule": rule_label,
        "dm_ok": ok,
        "matched_rule": f"{rule_label} {'✅' if ok else '❌'}",
    })


async def handle_comment(comment_id: str, text: str, username: str = "", media_id: str = "") -> None:
    matched = find_matching_rule(text, media_id)
    rule_name = matched.get("name") if matched else "Akıllı AI Yanıtı"

    if matched:
        raw_public = matched.get("public_reply") or "Harika! Detayları DM kutunuza ilettik ✨"
        public_reply = pick_variation(raw_public, username)
        dm_reply = matched.get("dm_message") or "Merhaba! İlgilendiğiniz detayları sizinle paylaşıyoruz."
    else:
        try:
            prompt = f"Kullanıcı (@{username}): '{text}'" if username else f"Yorum: '{text}'"
            public_reply = await call_gemini(prompt, "COMMENT")
            dm_prompt = f"Kullanıcı gönderi altına '{text}' yazdı. Ona DM kutusunda doğrudan bilgi ver."
            dm_reply = await call_gemini(dm_prompt, "DM")
        except Exception as e:
            logger.error(f"Yorum Hatası: {e}")
            public_reply = "Harika! İstediğiniz detayları DM kutunuza ilettik ✨"
            dm_reply = "Merhaba! Gönderimize yaptığınız yorum için teşekkürler. İlgilendiğiniz detayları paylaşıyoruz."

    comment_ok = await reply_to_comment(comment_id, public_reply)
    dm_ok = await send_private_reply(comment_id, dm_reply)

    status_tag = "✅" if (comment_ok and dm_ok) else ("⚠️ Yorum OK" if comment_ok else "⚠️ DM OK" if dm_ok else "❌")

    RECENT_LOGS.append({
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "at": _now_iso(),
        "type": "YORUM -> DM",
        "kind": "comment",
        "user": f"@{username}" if username else comment_id,
        "media_id": media_id,
        "input": text,
        "reply": f"Yorum: {public_reply} | DM: {dm_reply[:60]}...",
        "public_reply": public_reply,
        "dm_reply": dm_reply,
        "rule": rule_name,
        "comment_ok": comment_ok,
        "dm_ok": dm_ok,
        "matched_rule": f"{rule_name} {status_tag}",
    })


def valid_signature(body: bytes, header: str | None) -> bool:
    if not IG_APP_SECRET:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(IG_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


# ---------------------------------------------------------------- Dashboard & API Endpoint'leri

@app.get("/", include_in_schema=False)
async def dashboard_page():
    """Yönetim panelinin kabuğunu döndürür.

    Sayfa artık sunucuda veri gömmez (kurallar, kayıtlar, AI talimatı); hepsi PIN ile
    korunan API'lerden çekilir. Böylece Instagram'dan gelen yorum metinleri sayfaya HTML
    olarak enjekte edilemez ve kişisel veriler şifresiz görüntülenemez.
    """
    return FileResponse(DASHBOARD_FILE, media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store"})


def _validate_rules_payload(data) -> list[dict]:
    rules = data if isinstance(data, list) else (data or {}).get("rules")
    if not isinstance(rules, list) or not all(isinstance(r, dict) for r in rules):
        raise HTTPException(status_code=422, detail="Kurallar bir liste olmalı.")
    clean = []
    for r in rules:
        clean.append({
            "id": str(r.get("id") or f"rule_{int(time.time() * 1000)}"),
            "name": str(r.get("name") or "İsimsiz Kural").strip()[:120],
            "media_id": str(r.get("media_id") or "all").strip()[:64],
            "keywords": str(r.get("keywords") or "*").strip()[:500],
            "public_reply": str(r.get("public_reply") or "").strip()[:2000],
            "dm_message": str(r.get("dm_message") or "").strip()[:1000],
            "is_active": bool(r.get("is_active", True)),
        })
    return clean


@app.get("/api/status")
async def get_status_api(request: Request):
    """Paneldeki durum kartları için yapılandırma ve çalışma bilgisi (gizli değer döndürmez)."""
    verify_pin(request)
    rules = load_rules()
    return {
        "version": app.version,
        "started_at": STARTED_AT.isoformat(timespec="seconds"),
        "instagram": {
            "token_set": bool(IG_PAGE_ACCESS_TOKEN),
            "account_id": IG_ACCOUNT_ID,
            "graph_version": IG_GRAPH_VERSION,
            "media_ok": STATS["media_ok"],
            "media_error": STATS["media_error"],
            "media_checked_at": STATS["media_checked_at"],
        },
        "webhook": {
            "signature_check": bool(IG_APP_SECRET),
            "verify_token_is_default": IG_VERIFY_TOKEN == DEFAULT_VERIFY_TOKEN,
            "events": STATS["webhook_events"],
            "rejected": STATS["webhook_rejected"],
            "last_event_at": STATS["last_webhook_at"],
            "last_rejected_at": STATS["last_rejected_at"],
        },
        "ai": {"key_set": bool(GEMINI_API_KEY), "model": GEMINI_MODEL},
        "security": {"admin_pin_is_default": ADMIN_PIN == DEFAULT_ADMIN_PIN},
        "rules": {
            "total": len(rules),
            "active": sum(1 for r in rules if r.get("is_active", True)),
        },
        "logs": {"count": len(RECENT_LOGS), "capacity": RECENT_LOGS.maxlen},
    }


@app.get("/api/media")
async def get_media_api(request: Request):
    verify_pin(request)
    return await fetch_instagram_media()


@app.get("/api/rules")
async def get_rules_api(request: Request):
    verify_pin(request)
    return load_rules()


@app.post("/api/rules")
async def update_rules_api(request: Request):
    verify_pin(request)
    rules = _validate_rules_payload(await request.json())
    save_rules(rules)
    return {"status": "ok", "count": len(rules)}


@app.post("/api/test-match")
async def test_match_api(request: Request):
    """Örnek bir yorumun hangi kurala düşeceğini gösterir. Instagram'a hiçbir şey göndermez."""
    verify_pin(request)
    data = await request.json()
    text = str(data.get("text", ""))[:500]
    media_id = str(data.get("media_id", "") or "")
    rule = find_matching_rule(text, media_id) if text.strip() else None
    return {"matched": bool(rule), "rule": rule}


@app.get("/api/logs")
async def get_logs_api(request: Request):
    verify_pin(request)
    return list(RECENT_LOGS)


@app.get("/api/prompt")
async def get_prompt(request: Request):
    verify_pin(request)
    return {"prompt": load_system_prompt()}


@app.post("/api/prompt")
async def update_prompt(request: Request):
    verify_pin(request)
    data = await request.json()
    new_prompt = str(data.get("prompt", "")).strip()
    if not new_prompt:
        raise HTTPException(status_code=422, detail="AI talimatı boş olamaz.")
    with open(PROMPT_FILE, "w", encoding="utf-8") as f:
        f.write(new_prompt[:8000])
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge", "")

    if mode == "subscribe" and token == IG_VERIFY_TOKEN:
        logger.info("Meta Webhook doğrulaması başarılı.")
        return PlainTextResponse(challenge)

    raise HTTPException(status_code=403, detail="Doğrulama başarısız")


@app.post("/webhook")
async def receive_webhook(request: Request, background: BackgroundTasks):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")

    if not valid_signature(body, signature):
        STATS["webhook_rejected"] += 1
        STATS["last_rejected_at"] = _now_iso()
        raise HTTPException(status_code=401, detail="Geçersiz imza")

    STATS["webhook_events"] += 1
    STATS["last_webhook_at"] = _now_iso()

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse({"status": "invalid_json"}, status_code=400)

    logger.info(f"Webhook alındı: {data}")

    for entry in data.get("entry", []):
        # 1. Doğrudan DM Mesajları
        for msg_event in entry.get("messaging", []):
            mid = msg_event.get("message", {}).get("mid")
            sender_id = msg_event.get("sender", {}).get("id")
            text = msg_event.get("message", {}).get("text")

            if (
                mid
                and mid not in SEEN_EVENT_IDS
                and text
                and not msg_event.get("message", {}).get("is_echo")
                and str(sender_id) != IG_ACCOUNT_ID
            ):
                SEEN_EVENT_IDS.append(mid)
                background.add_task(handle_dm, sender_id, text)

        # 2. Gönderi Yorumları
        comment_candidates = []
        for change in entry.get("changes", []):
            if change.get("field") == "comments":
                comment_candidates.append(change.get("value", {}))
        if entry.get("field") == "comments":
            comment_candidates.append(entry.get("value", {}))

        for val in comment_candidates:
            if not isinstance(val, dict):
                continue
            cid = val.get("id")
            ctext = val.get("text")
            from_obj = val.get("from", {})
            cuser = from_obj.get("username", "")
            from_id = str(from_obj.get("id", ""))
            media_id = val.get("media", {}).get("id", "")

            # Kendi yorumumuza veya botun kendi cevabına tekrar yanıt vermeyelim
            if (from_id and from_id == IG_ACCOUNT_ID) or (cuser and cuser.lower() == "likyastudios"):
                continue

            if cid and cid not in SEEN_EVENT_IDS and ctext:
                SEEN_EVENT_IDS.append(cid)
                background.add_task(handle_comment, cid, ctext, cuser, media_id)

    return {"status": "ok"}
