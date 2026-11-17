"""Video içerik dosyalarını (ana sitedeki NNN-slug.json şeması) sayfaya çevirir ve
Instagram gönderileriyle eşleştirir.

Şema: { number, slug, date, title, excerpt, blocks: [h2 | p | prompt | link | steps | tip] }

Akış:
1. Video dosyası hazırlanınca `scripts/video_page.py add <dosya>` ile *taslak* olarak eklenir.
   Taslağın sayfası link.likyastudios.live/<slug> adresinde hemen yayındadır.
2. Instagram'da yeni gönderi görülünce (panel açılışı / ilk yorum), açıklamasında taslağın
   anahtar kelimesi geçiyorsa taslak o gönderiye bağlanır; artık o videoya yorum yapanlara
   bu sayfanın linki gider. Kesin olmayan eşleşmelerde bağlama yapılmaz.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜâîûÂÎÛ", "cgiosuCGIOSUaiuAIU")

# Çok genel kelimeler tek başına eşleşme sebebi sayılmaz
STOPWORDS = {
    "yapay", "zeka", "araci", "araclar", "arac", "ucretsiz", "google", "video", "videosu", "link", "linkleri",
    "prompt", "promptlari", "promptlar", "nasil", "icin", "ile", "bir", "kurulum", "indir", "basvuru",
    "dosya", "dosyani", "hazirla", "yeni", "ozel", "sayfa", "gemini", "chatgpt", "github", "telefon",
}


def fold(text: str) -> str:
    """Türkçe karakterleri sadeleştirip küçük harf + harf/rakam dizisine çevirir."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").translate(TR_MAP).lower()).strip()


def keywords(draft: dict) -> list[str]:
    """Taslağı tanımlayan ayırt edici kelimeler: sayfa adresindeki ve başlığın ilk kısmındaki özel kelimeler."""
    words: list[str] = []
    slug = draft.get("slug", "")
    joined = slug.replace("-", "")
    if len(joined) >= 5 and "-" in slug:
        words.append(joined)                          # ör. "orgu-yemek" -> "orguyemek"
    head = (draft.get("title") or "").split(":")[0]   # "MarkItDown: dosyanı..." -> "MarkItDown"
    for w in fold(slug.replace("-", " ") + " " + head).split():
        if len(w) >= 4 and not w.isdigit() and w not in STOPWORDS and w not in words:
            words.append(w)
    return words


def caption_matches(draft: dict, caption: str) -> bool:
    cap = fold(caption)
    squashed = cap.replace(" ", "")
    for kw in keywords(draft):
        if re.search(rf"\b{re.escape(kw)}", cap) or (len(kw) >= 8 and kw in squashed):
            return True
    return False


def _parse_date(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            d = datetime.strptime(value[:24] if "T" in value else value[:10], fmt)
            return d.replace(tzinfo=None)
        except (ValueError, TypeError):
            continue
    return None


def find_bindings(drafts: dict[str, dict], posts: list[dict], bound_ids: set[str]) -> dict[str, str]:
    """{media_id: draft_slug} — yalnızca tek ve kesin eşleşmeler.

    - Gönderinin zaten sayfası varsa atlanır.
    - Gönderi, taslak tarihinden en fazla 3 gün önce paylaşılmış olamaz (eski videolar karışmasın).
    - Bir gönderiye birden fazla taslak uyuyorsa veya bir taslak birden fazla gönderiye uyuyorsa bağlanmaz.
    """
    candidates: dict[str, list[str]] = {}
    for post in posts:
        mid = str(post.get("id"))
        if not mid or mid in bound_ids:
            continue
        posted = _parse_date(post.get("timestamp", ""))
        for slug, draft in drafts.items():
            ddate = _parse_date(draft.get("date", ""))
            if posted and ddate and posted < ddate - timedelta(days=3):
                continue
            if caption_matches(draft, post.get("caption") or post.get("title") or ""):
                candidates.setdefault(mid, []).append(slug)
    result: dict[str, str] = {}
    slug_hits: dict[str, int] = {}
    for slugs in candidates.values():
        for s in slugs:
            slug_hits[s] = slug_hits.get(s, 0) + 1
    for mid, slugs in candidates.items():
        if len(slugs) == 1 and slug_hits[slugs[0]] == 1:
            result[mid] = slugs[0]
    return result


def page_from_video_json(d: dict) -> dict:
    """Ana sitedeki video dosyasını panel sayfa biçimine çevirir (panelin "JSON'dan doldur" ile aynı)."""
    if not isinstance(d, dict) or not isinstance(d.get("blocks"), list):
        raise ValueError("Dosyada 'blocks' listesi yok.")
    body: list[str] = [str(d["excerpt"])] if d.get("excerpt") else []
    prompts, links, steps, tips = [], [], [], []
    section = ""
    for b in d["blocks"]:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "h2":
            section = str(b.get("text") or "").strip()
        elif t == "p" and b.get("text"):
            body.append(str(b["text"]))
        elif t == "prompt" and b.get("text"):
            label = " — ".join(x for x in (section, str(b.get("label") or "")) if x)
            prompts.append({"label": label or "Prompt", "tool": str(b.get("tool") or ""), "text": str(b["text"])})
        elif t == "link" and b.get("url"):
            links.append({"label": str(b.get("label") or ""), "url": str(b["url"]), "note": str(b.get("note") or "")})
        elif t == "steps" and isinstance(b.get("items"), list):
            steps.extend(str(x) for x in b["items"])
        elif t == "tip" and b.get("text"):
            tips.append(str(b["text"]))
    slug = str(d.get("slug") or "").strip()
    title = str(d.get("title") or "").strip()
    if not slug or not title:
        raise ValueError("Dosyada 'slug' ve 'title' gerekli.")
    return {
        "title": title, "slug": slug, "body": "\n\n".join(body), "links": links, "prompts": prompts,
        "steps": steps, "tip": " ".join(tips), "external_url": "", "dm_message": "", "is_active": True,
        "number": d.get("number"), "date": str(d.get("date") or ""),
    }
