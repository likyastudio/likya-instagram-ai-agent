"""Ana sitedeki video sayfaları (likyastudios.live/prompt) ile Instagram gönderilerini eşleştirme.

Her video açıklamasında "yoruma GÖRSEL yaz" gibi bir cümle var ve bu kelime her videoda farklı.
Site, tüm sayfaları yorum kelimeleriyle birlikte /api/prompts adresinde listeler. Yeni bir gönderinin
açıklamasındaki kelime bir sayfanınkiyle aynıysa gönderi o sayfaya bağlanır; panelde elle taslak
eklemek gerekmez. Açıklamada kelime bulunamazsa, ilk yorumun kendisi (ör. "SAHNE") kullanılır.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import timedelta

import httpx

from video_import import TR_MAP, _parse_date

logger = logging.getLogger("instagram-agent")

SITE_PROMPTS_URL = os.getenv("SITE_PROMPTS_URL", "https://likyastudios.live/api/prompts")
CACHE_TTL = 300
_CACHE: dict = {"at": 0.0, "pages": []}

# "yoruma GÖRSEL yaz", "Yorumlara “SAHNE” yaz", "yorum olarak KİTAP yaz"
_PHRASE = re.compile(r"yorum(?:a|lara|larına|larina|\s+olarak)?\s+[\"“'‘«]?([^\s\"”'’»]{2,24})[\"”'’»]?\s+yaz", re.IGNORECASE)


def norm(word: str) -> str:
    """Türkçe harfleri sadeleştirip büyük/küçük farkını kaldırır: "GÖRSEL", "görsel", "Gorsel" → "gorsel"."""
    return re.sub(r"[^a-z0-9]", "", (word or "").translate(TR_MAP).lower())


def caption_word(caption: str) -> str:
    """Açıklamadaki yorum kelimesi (normalize edilmiş), yoksa boş."""
    m = _PHRASE.search(caption or "")
    return norm(m.group(1)) if m else ""


def _used(page: dict, bound_pages: dict[str, dict]) -> bool:
    for p in bound_pages.values():
        if not isinstance(p, dict):
            continue
        if p.get("slug") == page.get("slug") or (p.get("external_url") or "") == page.get("url"):
            return True
    return False


def _by_word(site: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    dup: set[str] = set()
    for p in site:
        w = norm(p.get("commentWord") or "")
        if not w:
            continue
        if w in out:
            dup.add(w)
        out[w] = p
    for w in dup:          # aynı kelime iki sayfada: belirsiz, kullanma
        out.pop(w, None)
    return out


def _recent_enough(post: dict, page: dict) -> bool:
    """Gönderi, sayfanın hazırlandığı tarihten en fazla 3 gün önce olabilir (eski videolar karışmasın)."""
    posted, made = _parse_date(post.get("timestamp", "")), _parse_date(page.get("date", ""))
    return not (posted and made and posted < made - timedelta(days=3))


def find_site_bindings(site: list[dict], posts: list[dict], bound_pages: dict[str, dict]) -> dict[str, dict]:
    """{media_id: site_page} — açıklamasındaki yorum kelimesi tek bir sayfayla eşleşen, henüz bağlanmamış gönderiler."""
    words = _by_word(site)
    result: dict[str, dict] = {}
    taken: set[str] = set()
    for post in posts:
        mid = str(post.get("id") or "")
        if not mid or mid in bound_pages:
            continue
        page = words.get(caption_word(post.get("caption") or ""))
        if not page or page["slug"] in taken or _used(page, bound_pages) or not _recent_enough(post, page):
            continue
        result[mid] = page
        taken.add(page["slug"])
    return result


def page_for_comment(site: list[dict], comment_text: str, post: dict | None, bound_pages: dict[str, dict]) -> dict | None:
    """Açıklamada kelime yoksa: yorum tek kelimeyse ve bir sayfanın kelimesiyse, o sayfa (henüz bağlı değilse)."""
    tokens = (comment_text or "").split()
    if len(tokens) != 1:
        return None
    page = _by_word(site).get(norm(tokens[0]))
    if not page or _used(page, bound_pages) or (post and not _recent_enough(post, page)):
        return None
    return page


def to_panel_page(site_page: dict, draft: dict | None = None) -> dict:
    """Site sayfasını panel sayfasına çevirir; DM'de sitedeki adres gider. Aynı adlı taslak varsa içeriği korunur."""
    page = dict(draft or {})
    page.update(
        title=page.get("title") or site_page.get("title") or site_page["slug"],
        slug=site_page["slug"],
        external_url=site_page["url"],
        is_active=True,
        number=site_page.get("number"),
        date=site_page.get("date") or page.get("date", ""),
        comment_word=site_page.get("commentWord") or "",
    )
    for key, empty in (("body", ""), ("links", []), ("prompts", []), ("steps", []), ("tip", ""), ("dm_message", "")):
        page.setdefault(key, empty)
    return page


async def fetch_site_pages(fresh: bool = False) -> list[dict]:
    """Sitedeki sayfa listesi (5 dk önbellek). Hata olursa son bilinen liste döner."""
    if not fresh and _CACHE["pages"] and time.monotonic() - _CACHE["at"] < CACHE_TTL:
        return _CACHE["pages"]
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(SITE_PROMPTS_URL)
        if r.status_code == 200:
            pages = [p for p in r.json().get("pages", [])
                     if isinstance(p, dict) and p.get("slug") and str(p.get("url", "")).startswith("https://")]
            _CACHE.update(at=time.monotonic(), pages=pages)
            return pages
        logger.error(f"Site sayfa listesi alınamadı ({r.status_code})")
    except Exception as e:
        logger.error(f"Site sayfa listesi alınamadı: {e}")
    return _CACHE["pages"]

