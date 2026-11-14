"""
Likya Studios — Instagram Otomasyon & AI Yönetim Sistemi (Pro Sürüm)
====================================================================
Özellikler:
1. Web Yönetim Paneli (PIN Şifre Korumalı — Varsayılan: likya2026)
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
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

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

# Bellek içi kuyruklar
DM_HISTORY: dict[str, deque] = defaultdict(lambda: deque(maxlen=15))
SEEN_EVENT_IDS: deque = deque(maxlen=1000)
RECENT_LOGS: deque = deque(maxlen=50)

app = FastAPI(title="Likya Studios Instagram Automation Pro", version="3.0.0")


# ---------------------------------------------------------------- Yardımcılar & Kurallar

def verify_pin(request: Request) -> None:
    """API isteklerinde X-Admin-Pin başlığını doğrular (Test ortamında boşsa izin verir)."""
    pin = request.headers.get("X-Admin-Pin", "")
    if ADMIN_PIN and pin and pin != ADMIN_PIN:
        raise HTTPException(status_code=401, detail="Geçersiz Yönetim Şifresi (PIN)")


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
    if not IG_PAGE_ACCESS_TOKEN:
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
                    })
                return result
    except Exception as e:
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
        "type": "DM",
        "user": sender_id,
        "input": text,
        "reply": reply,
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
        "type": "YORUM -> DM",
        "user": f"@{username}" if username else comment_id,
        "input": text,
        "reply": f"Yorum: {public_reply} | DM: {dm_reply[:60]}...",
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

@app.get("/", response_class=HTMLResponse)
async def dashboard_page():
    rules = load_rules()
    prompt = load_system_prompt()
    rules_json_str = json.dumps(rules, ensure_ascii=False)
    logs_json_str = json.dumps(list(RECENT_LOGS), ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Likya Studios — Instagram Otomasyon & AI Yönetim Paneli</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
  <style>
    body {{ background: #0f172a; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    .glass {{ background: rgba(30, 41, 59, 0.75); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); }}
    .gradient-btn {{ background: linear-gradient(135deg, #e11d48, #db2777); }}
  </style>
</head>
<body class="min-h-screen p-4 md:p-8">

  <!-- Üst Başlık -->
  <header class="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between pb-6 border-b border-slate-800 gap-4">
    <div class="flex items-center gap-3">
      <div class="w-12 h-12 rounded-xl gradient-btn flex items-center justify-center text-white text-2xl shadow-lg">
        <i class="fa-brands fa-instagram"></i>
      </div>
      <div>
        <h1 class="text-2xl font-bold tracking-tight">Likya Studios — Otomasyon Paneli <span class="text-xs bg-pink-500/20 text-pink-400 px-2 py-0.5 rounded-full ml-1">PRO</span></h1>
        <p class="text-slate-400 text-sm">@likyastudios • Filigransız Yorum & DM Otomasyonu + Gemini AI</p>
      </div>
    </div>
    <div class="flex items-center gap-3">
      <span class="px-3 py-1.5 text-xs font-semibold rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 flex items-center gap-1.5">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span> 7/24 Aktif
      </span>
      <button onclick="openModal()" class="gradient-btn hover:opacity-90 px-4 py-2 rounded-lg font-medium text-sm flex items-center gap-2 shadow-lg">
        <i class="fa-solid fa-plus"></i> Yeni Kural Ekle
      </button>
      <button onclick="changePin()" title="Yönetim Şifresini Gir/Değiştir" class="bg-slate-800 hover:bg-slate-700 px-3 py-2 rounded-lg text-slate-300 text-sm border border-slate-700">
        <i class="fa-solid fa-lock"></i>
      </button>
    </div>
  </header>

  <main class="max-w-6xl mx-auto mt-8 grid grid-cols-1 lg:grid-cols-3 gap-8">

    <!-- Sol/Orta Sütun: Otomasyon Kuralları -->
    <div class="lg:col-span-2 space-y-6">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold flex items-center gap-2">
          <i class="fa-solid fa-bolt text-amber-400"></i> Yorum & DM Otomasyon Kuralları
        </h2>
        <span id="ruleCount" class="text-xs text-slate-400"></span>
      </div>

      <div id="rulesContainer" class="space-y-4"></div>

      <!-- Canlı Loglar -->
      <div class="glass rounded-xl p-6 mt-8">
        <div class="flex items-center justify-between mb-4">
          <h3 class="font-semibold flex items-center gap-2">
            <i class="fa-solid fa-wave-square text-sky-400"></i> Son Gelen Yorum & DM Hareketleri
          </h3>
          <button onclick="refreshLogs()" class="text-xs text-slate-400 hover:text-white flex items-center gap-1">
            <i class="fa-solid fa-rotate"></i> Yenile
          </button>
        </div>
        <div id="logsContainer" class="space-y-3 max-h-80 overflow-y-auto text-sm"></div>
      </div>
    </div>

    <!-- Sağ Sütun: AI Asistan ve İpuçları -->
    <div class="space-y-6">
      <div class="glass rounded-xl p-6">
        <h2 class="text-lg font-semibold flex items-center gap-2 mb-2">
          <i class="fa-solid fa-wand-magic-sparkles text-purple-400"></i> Gemini AI Kişiliği
        </h2>
        <p class="text-xs text-slate-400 mb-4">
          Hiçbir anahtar kelimeyle eşleşmeyen genel yorumlara veya doğrudan gelen DM sorularına yapay zekanın nasıl yanıt vereceğini buradan belirleyin.
        </p>
        <textarea id="systemPromptInput" rows="7" class="w-full rounded-lg bg-slate-900/90 border border-slate-700 p-3 text-sm text-slate-200 focus:outline-none focus:border-pink-500">{prompt}</textarea>
        <button onclick="savePrompt()" class="mt-3 w-full py-2.5 rounded-lg bg-slate-800 hover:bg-slate-700 font-medium text-sm border border-slate-700 transition">
          <i class="fa-solid fa-floppy-disk mr-1"></i> AI Talimatını Kaydet
        </button>
      </div>

      <div class="glass rounded-xl p-6 space-y-3 text-xs text-slate-300">
        <h3 class="font-semibold text-white text-sm flex items-center gap-2">
          <i class="fa-solid fa-shield-halved text-emerald-400"></i> Anti-Spam & Kullanım İpuçları
        </h3>
        <p>• <b>Çoklu Yorum Varyasyonu:</b> Yorum yanıtı kutusunda <code>|</code> işareti kullanarak birden fazla cümle yazabilirsiniz. Sistem her yorumda rastgele birini seçer ve spama düşmez.</p>
        <p>• <b>Videoya Özel Kural:</b> Yeni kural eklerken <b>"Instagram'daki Videolarımdan Seç"</b> menüsünden doğrudan ilgili Reels videonuzu seçebilirsiniz.</p>
        <p>• <b>Varsayılan Yönetim Şifresi:</b> <code>likya2026</code></p>
      </div>
    </div>
  </main>

  <!-- Kural Ekle / Düzenle Modalı -->
  <div id="ruleModal" class="fixed inset-0 bg-black/75 hidden items-center justify-center p-4 z-50">
    <div class="glass rounded-2xl max-w-lg w-full p-6 space-y-4 border border-slate-700 max-h-[92vh] overflow-y-auto">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <h3 id="modalTitle" class="text-lg font-bold">Yeni Otomasyon Kuralı</h3>
        <button onclick="closeModal()" class="text-slate-400 hover:text-white"><i class="fa-solid fa-xmark text-lg"></i></button>
      </div>

      <input type="hidden" id="ruleId">

      <div>
        <label class="block text-xs text-slate-400 mb-1">Kural Adı (Örn: Stirling PDF Linki)</label>
        <input id="ruleName" type="text" placeholder="Örn: Stirling PDF Kampanyası" class="w-full rounded-lg bg-slate-900 border border-slate-700 p-2.5 text-sm">
      </div>

      <div>
        <label class="block text-xs text-slate-400 mb-1">Hangi Gönderi / Reels Videosunda Geçerli Olsun?</label>
        <select id="mediaSelect" onchange="onMediaSelectChange()" class="w-full rounded-lg bg-slate-900 border border-slate-700 p-2.5 text-sm mb-2 text-slate-200">
          <option value="all">🌐 Tüm Gönderiler ve Reels Videoları (Genel)</option>
        </select>
        <input id="ruleMediaId" type="text" value="all" placeholder="all veya Gönderi ID" class="w-full rounded-lg bg-slate-950 border border-slate-800 p-2 text-xs text-slate-400">
      </div>

      <div>
        <label class="block text-xs text-slate-400 mb-1">Tetikleyici Yorum Kelimeleri (Virgülle ayırın, hepsi için *)</label>
        <input id="ruleKeywords" type="text" placeholder="Örn: pdf, link, indir, bilgi" class="w-full rounded-lg bg-slate-900 border border-slate-700 p-2.5 text-sm">
      </div>

      <div>
        <label class="block text-xs text-slate-400 mb-1">Yoruma Yazılacak Herkese Açık Yanıt (Farklı varyasyonları <b>|</b> ile ayırın)</label>
        <textarea id="rulePublicReply" rows="2" placeholder="Harika! Linki DM'den ilettik ✨ | Selamlar, bağlantıyı DM kutunuza gönderdik 🚀" class="w-full rounded-lg bg-slate-900 border border-slate-700 p-2.5 text-sm"></textarea>
      </div>

      <div>
        <label class="block text-xs text-slate-400 mb-1">DM'den Gönderilecek Özel Mesaj & Link</label>
        <textarea id="ruleDmMessage" rows="4" placeholder="Merhaba! İstediğiniz bağlantı burada: https://..." class="w-full rounded-lg bg-slate-900 border border-slate-700 p-2.5 text-sm"></textarea>
      </div>

      <div class="flex justify-end gap-3 pt-2">
        <button onclick="closeModal()" class="px-4 py-2 rounded-lg bg-slate-800 text-sm">İptal</button>
        <button onclick="saveRuleForm()" class="gradient-btn px-5 py-2 rounded-lg font-medium text-sm">Kaydet ve Etkinleştir</button>
      </div>
    </div>
  </div>

  <script>
    let rules = {rules_json_str};
    let logs = {logs_json_str};
    let mediaList = [];

    function getPin() {{
      let pin = localStorage.getItem('likya_admin_pin') || 'likya2026';
      return pin;
    }}

    function changePin() {{
      const p = prompt('Yönetim Paneli Şifresini (PIN) girin:', getPin());
      if (p !== null) {{
        localStorage.setItem('likya_admin_pin', p.trim());
        alert('Şifre kaydedildi!');
      }}
    }}

    async function loadMediaList() {{
      try {{
        const res = await fetch('/api/media');
        if (res.ok) {{
          mediaList = await res.json();
          const select = document.getElementById('mediaSelect');
          select.innerHTML = '<option value="all">🌐 Tüm Gönderiler ve Reels Videoları (Genel)</option>';
          mediaList.forEach(m => {{
            const opt = document.createElement('option');
            opt.value = m.id;
            opt.textContent = `🎬 ${{m.title}} (${{m.id}})`;
            select.appendChild(opt);
          }});
        }}
      }} catch (e) {{
        console.error('Medya yüklenemedi:', e);
      }}
    }}

    function onMediaSelectChange() {{
      const val = document.getElementById('mediaSelect').value;
      document.getElementById('ruleMediaId').value = val;
    }}

    function getMediaTitle(mediaId) {{
      if (!mediaId || mediaId === 'all') return '🌐 Tüm Videolar';
      const found = mediaList.find(m => m.id === mediaId);
      return found ? `🎬 ${{found.title.substring(0, 38)}}...` : `🎬 Video ID: ${{mediaId}}`;
    }}

    function renderRules() {{
      const container = document.getElementById('rulesContainer');
      document.getElementById('ruleCount').innerText = `${{rules.length}} Kural Tanımlı`;
      if (!rules.length) {{
        container.innerHTML = '<div class="glass rounded-xl p-8 text-center text-slate-400">Henüz kural eklenmemiş. Sağ üstten Yeni Kural Ekle butonuna tıklayın.</div>';
        return;
      }}
      container.innerHTML = rules.map((r, idx) => {{
        const varCount = (r.public_reply || '').split('|').filter(x => x.trim()).length;
        return `
        <div class="glass rounded-xl p-5 border-l-4 ${{r.is_active ? 'border-l-pink-500' : 'border-l-slate-600 opacity-60'}}">
          <div class="flex items-start justify-between gap-4">
            <div class="space-y-1.5 flex-1">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-bold text-base">${{r.name}}</span>
                <span class="text-xs px-2 py-0.5 rounded bg-slate-800 text-sky-300 border border-slate-700">
                  ${{getMediaTitle(r.media_id)}}
                </span>
                <span class="text-xs px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-300 border border-emerald-500/20">
                  <i class="fa-solid fa-shuffle mr-1"></i>${{varCount}} Varyasyon
                </span>
              </div>
              <p class="text-xs text-pink-400 font-medium">
                <i class="fa-solid fa-key mr-1"></i> Tetikleyiciler: <span class="text-slate-200">${{r.keywords}}</span>
              </p>
              <div class="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3 pt-2">
                <div class="bg-slate-900/70 p-3 rounded-lg border border-slate-800">
                  <span class="text-[11px] uppercase tracking-wider text-slate-400 block mb-1">💬 Yorum Yanıtı</span>
                  <p class="text-xs text-slate-200 line-clamp-2">${{r.public_reply}}</p>
                </div>
                <div class="bg-slate-900/70 p-3 rounded-lg border border-slate-800">
                  <span class="text-[11px] uppercase tracking-wider text-slate-400 block mb-1">📩 Özel DM Mesajı</span>
                  <p class="text-xs text-slate-200 whitespace-pre-line line-clamp-3">${{r.dm_message}}</p>
                </div>
              </div>
            </div>
            <div class="flex items-center gap-2">
              <button onclick="toggleRule(${{idx}})" title="Aktif/Pasif" class="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs">
                <i class="fa-solid ${{r.is_active ? 'fa-toggle-on text-emerald-400 text-lg' : 'fa-toggle-off text-slate-400 text-lg'}}"></i>
              </button>
              <button onclick="editRule(${{idx}})" title="Düzenle" class="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs text-sky-400">
                <i class="fa-solid fa-pen"></i>
              </button>
              <button onclick="deleteRule(${{idx}})" title="Sil" class="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs text-rose-400">
                <i class="fa-solid fa-trash"></i>
              </button>
            </div>
          </div>
        </div>
      `}}).join('');
    }}

    function renderLogs() {{
      const container = document.getElementById('logsContainer');
      if (!logs.length) {{
        container.innerHTML = '<p class="text-slate-500 text-xs">Henüz kaydedilmiş bir yorum veya DM hareketi yok.</p>';
        return;
      }}
      container.innerHTML = logs.slice().reverse().map(l => `
        <div class="p-3 rounded-lg bg-slate-900/75 border border-slate-800 flex flex-col gap-1">
          <div class="flex items-center justify-between text-xs">
            <span class="font-semibold text-pink-400">${{l.type}} • ${{l.user}}</span>
            <span class="text-slate-400">${{l.matched_rule}} • ${{l.time}}</span>
          </div>
          <p class="text-xs text-slate-300"><b>Gelen:</b> ${{l.input}}</p>
          <p class="text-xs text-emerald-300"><b>Yanıt:</b> ${{l.reply}}</p>
        </div>
      `).join('');
    }}

    function openModal(editIdx = null) {{
      document.getElementById('ruleModal').classList.remove('hidden');
      document.getElementById('ruleModal').classList.add('flex');
      if (editIdx !== null) {{
        const r = rules[editIdx];
        document.getElementById('modalTitle').innerText = 'Kuralı Düzenle';
        document.getElementById('ruleId').value = editIdx;
        document.getElementById('ruleName').value = r.name;
        document.getElementById('ruleMediaId').value = r.media_id || 'all';
        document.getElementById('mediaSelect').value = mediaList.some(m => m.id === r.media_id) ? r.media_id : 'all';
        document.getElementById('ruleKeywords').value = r.keywords;
        document.getElementById('rulePublicReply').value = r.public_reply;
        document.getElementById('ruleDmMessage').value = r.dm_message;
      }} else {{
        document.getElementById('modalTitle').innerText = 'Yeni Otomasyon Kuralı';
        document.getElementById('ruleId').value = '';
        document.getElementById('ruleName').value = '';
        document.getElementById('ruleMediaId').value = 'all';
        document.getElementById('mediaSelect').value = 'all';
        document.getElementById('ruleKeywords').value = '';
        document.getElementById('rulePublicReply').value = 'Harika! İstediğiniz bağlantıyı hemen DM kutunuza ilettik ✨ | Selamlar! Linki DM üzerinden gönderdik 🚀';
        document.getElementById('ruleDmMessage').value = '';
      }}
    }}

    function closeModal() {{
      document.getElementById('ruleModal').classList.add('hidden');
      document.getElementById('ruleModal').classList.remove('flex');
    }}

    function editRule(idx) {{ openModal(idx); }}

    async function saveRuleForm() {{
      const idx = document.getElementById('ruleId').value;
      const newRule = {{
        id: idx !== '' ? rules[idx].id : 'rule_' + Date.now(),
        name: document.getElementById('ruleName').value.trim() || 'İsimsiz Kural',
        media_id: document.getElementById('ruleMediaId').value.trim() || 'all',
        keywords: document.getElementById('ruleKeywords').value.trim() || '*',
        public_reply: document.getElementById('rulePublicReply').value.trim(),
        dm_message: document.getElementById('ruleDmMessage').value.trim(),
        is_active: idx !== '' ? rules[idx].is_active : true
      }};
      if (idx !== '') rules[idx] = newRule;
      else rules.unshift(newRule);
      await syncRules();
      closeModal();
    }}

    async function toggleRule(idx) {{
      rules[idx].is_active = !rules[idx].is_active;
      await syncRules();
    }}

    async function deleteRule(idx) {{
      if (!confirm('Bu kuralı silmek istediğinize emin misiniz?')) return;
      rules.splice(idx, 1);
      await syncRules();
    }}

    async function syncRules() {{
      const res = await fetch('/api/rules', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json', 'X-Admin-Pin': getPin() }},
        body: JSON.stringify({{ rules }})
      }});
      if (res.status === 401) {{
        alert('Yönetim şifresi hatalı! Sağ üstteki kilit ikonundan şifreyi kontrol edin.');
        return;
      }}
      renderRules();
    }}

    async function savePrompt() {{
      const prompt = document.getElementById('systemPromptInput').value;
      const res = await fetch('/api/prompt', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json', 'X-Admin-Pin': getPin() }},
        body: JSON.stringify({{ prompt }})
      }});
      if (res.status === 401) {{
        alert('Yönetim şifresi hatalı!');
        return;
      }}
      alert('AI Sistem Talimatı başarıyla güncellendi!');
    }}

    async function refreshLogs() {{
      const res = await fetch('/api/logs');
      logs = await res.json();
      renderLogs();
    }}

    renderRules();
    renderLogs();
    loadMediaList().then(() => renderRules());
    setInterval(refreshLogs, 8000);
  </script>
</body>
</html>"""


@app.get("/api/media")
async def get_media_api():
    return await fetch_instagram_media()


@app.get("/api/rules")
async def get_rules_api():
    return load_rules()


@app.post("/api/rules")
async def update_rules_api(request: Request):
    verify_pin(request)
    data = await request.json()
    rules = data if isinstance(data, list) else data.get("rules", [])
    save_rules(rules)
    return {"status": "ok", "count": len(rules)}


@app.get("/api/logs")
async def get_logs_api():
    return list(RECENT_LOGS)


@app.post("/api/prompt")
async def update_prompt(request: Request):
    verify_pin(request)
    data = await request.json()
    new_prompt = data.get("prompt", "")
    with open(PROMPT_FILE, "w", encoding="utf-8") as f:
        f.write(new_prompt)
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
        raise HTTPException(status_code=401, detail="Geçersiz imza")

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
