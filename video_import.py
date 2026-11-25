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


def match_score(draft: dict, caption: str) -> int:
    """Açıklamada geçen ayırt edici kelime sayısı (0 = eşleşme yok)."""
    cap = fold(caption)
    squashed = cap.replace(" ", "")
    return sum(1 for kw in keywords(draft)
               if re.search(rf"\b{re.escape(kw)}", cap) or (len(kw) >= 8 and kw in squashed))


def caption_matches(draft: dict, caption: str) -> bool:
    return match_score(draft, caption) > 0


def _parse_date(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            d = datetime.strptime(value[:24] if "T" in value else value[:10], fmt)
            return d.replace(tzinfo=None)
        except (ValueError, TypeError):
            continue
    return None


def _publish_moment(value: str) -> datetime | None:
    """publishAt → UTC (saatsiz tarih: İstanbul 06:00, sitedeki varsayılanla aynı)."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        if "T" not in value:
            return datetime.strptime(value[:10], "%Y-%m-%d") + timedelta(hours=3)   # 06:00 TR = 03:00 UTC
        d = datetime.fromisoformat(value)
        return (d - d.utcoffset()).replace(tzinfo=None) if d.utcoffset() is not None else d
    except ValueError:
        return None


def _near(publish_at: str, posted: datetime, hours: int = 6) -> bool:
    moment = _publish_moment(publish_at)
    return bool(moment) and abs((posted - moment).total_seconds()) <= hours * 3600


def find_bindings(drafts: dict[str, dict], posts: list[dict], bound_ids: set[str]) -> dict[str, str]:
    """{media_id: draft_slug} — yalnızca tek ve kesin eşleşmeler.

    - Gönderinin zaten sayfası varsa atlanır.
    - Gönderi, taslak tarihinden en fazla 3 gün önce paylaşılmış olamaz (eski videolar karışmasın).
    - Bir gönderiye birden fazla taslak uyuyorsa önce planlanan açılış saati (publishAt) paylaşım saatine
      ±6 saat uyan, sonra en çok kelimesi tutan alınır; eşitse bağlanmaz.
    - Bir taslak birden fazla gönderiye uyuyorsa bağlanmaz.
    """
    # Bir gönderiye birden fazla taslak uyarsa en çok kelimesi tutan seçilir (ör. iki "Claude" videosu
    # sıradayken "Claude + Codex + görsel" geçen açıklama Codex taslağına gider); eşitlik varsa bağlanmaz.
    candidates: dict[str, list[str]] = {}
    for post in posts:
        mid = str(post.get("id"))
        if not mid or mid in bound_ids:
            continue
        posted = _parse_date(post.get("timestamp", ""))
        caption = post.get("caption") or post.get("title") or ""
        scored: list[tuple[int, str]] = []
        for slug, draft in drafts.items():
            ddate = _parse_date(draft.get("date", ""))
            if posted and ddate and posted < ddate - timedelta(days=3):
                continue
            score = match_score(draft, caption)
            if score and posted and _near(draft.get("publish_at", ""), posted):
                score += 100   # sayfanın planlanan açılış saati paylaşım saatine uyuyor: en güçlü işaret
            if score:
                scored.append((score, slug))
        scored.sort(reverse=True)
        if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            candidates[mid] = [scored[0][1]]
        elif scored:
            candidates[mid] = [s for _, s in scored]   # belirsiz: aşağıda elenir
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
        "publish_at": str(d.get("publishAt") or ""),
    }
