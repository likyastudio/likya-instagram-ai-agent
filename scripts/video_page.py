"""Video sayfası komutu — yeni video için hazırlanan içerik dosyasını sisteme ekler.

Kullanım (proje klasöründe):
  python scripts/video_page.py add yol/025-markitdown.json        # taslak olarak ekle
  python scripts/video_page.py add yol/025-markitdown.json --media-id 18123456789   # doğrudan gönderiye bağla
  python scripts/video_page.py list                                # sayfaları ve bekleyen taslakları göster

Taslak eklenince sayfa hemen https://link.likyastudios.live/<slug> adresinde açılır. Video Instagram'da
paylaşılınca, açıklamasında taslağın adı geçiyorsa sistem taslağı o gönderiye kendiliğinden bağlar ve o
videoya yorum yapanlara bu sayfanın linki gider.

Gereksinim: gcloud CLI ile oturum açılmış olmalı (app-map-509800 projesine yazma yetkisi).
Veri: gs://likya-instagram-data-530805644511/instagram-agent/data.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import video_import  # noqa: E402

PROJECT = "app-map-509800"
OBJECT = "gs://likya-instagram-data-530805644511/instagram-agent/data.json"
PUBLIC = "https://link.likyastudios.live"
RESERVED = {"p", "api", "auth", "brand", "webhook", "health", "static", "docs", "openapi.json", "favicon.ico", "robots.txt"}


def gcloud(*args: str, stdin: bytes | None = None) -> bytes:
    exe = "gcloud.cmd" if sys.platform == "win32" else "gcloud"
    r = subprocess.run([exe, *args, f"--project={PROJECT}"], input=stdin, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"gcloud hatası: {r.stderr.decode('utf-8', 'replace').strip()[:400]}")
    return r.stdout


def read_store() -> tuple[dict, str]:
    gen = gcloud("storage", "objects", "describe", OBJECT, "--format=value(generation)").decode().strip()
    data = json.loads(gcloud("storage", "cat", OBJECT).decode("utf-8"))
    data.setdefault("pages", {})
    data.setdefault("drafts", {})
    return data, gen


def write_store(data: dict, generation: str) -> None:
    body = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
    # Okuduğumuzdan beri başka bir kayıt olduysa yazma (panel değişikliği ezilmesin)
    gcloud("storage", "cp", "-", OBJECT, f"--if-generation-match={generation}",
           "--content-type=application/json; charset=utf-8", stdin=body)


def cmd_add(path: str, media_id: str | None) -> None:
    page = video_import.page_from_video_json(json.loads(Path(path).read_text(encoding="utf-8")))
    slug = page["slug"]
    if slug in RESERVED:
        raise SystemExit(f"'{slug}' sayfa adresi olarak kullanılamaz.")
    data, gen = read_store()
    for mid, other in data["pages"].items():
        if other.get("slug") == slug and mid != media_id:
            raise SystemExit(f"'{slug}' zaten {mid} gönderisinin sayfası. Güncellemek için: --media-id {mid}")
    if media_id:
        page["bound_at"] = "komut"
        data["drafts"].pop(slug, None)
        data["pages"][media_id] = page
        where = f"gönderi {media_id}"
    else:
        data["drafts"][slug] = page
        where = "taslak (video paylaşılınca kendiliğinden bağlanacak)"
    write_store(data, gen)
    kws = ", ".join(video_import.keywords(page)) or "-"
    print(f"Eklendi: {page['title']}\n  Yer: {where}\n  Sayfa: {PUBLIC}/{slug}\n"
          f"  Eşleştirme kelimeleri: {kws}\n  Prompt: {len(page['prompts'])}, link: {len(page['links'])}, adım: {len(page['steps'])}")


def cmd_list() -> None:
    data, _ = read_store()
    print("Bağlı sayfalar:")
    for mid, p in data["pages"].items():
        print(f"  {mid:>20}  {PUBLIC}/{p.get('slug')}  {'(otomatik)' if p.get('auto_bound') else ''}")
    print("Bekleyen taslaklar:" if data["drafts"] else "Bekleyen taslak yok.")
    for slug, p in data["drafts"].items():
        print(f"  {PUBLIC}/{slug}  kelimeler: {', '.join(video_import.keywords(p))}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Likya Studios video sayfası komutu")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="video içerik dosyasını ekle")
    a.add_argument("file")
    a.add_argument("--media-id", help="Instagram gönderi kimliği (biliniyorsa doğrudan bağlar)")
    sub.add_parser("list", help="sayfaları ve taslakları listele")
    args = ap.parse_args()
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    cmd_add(args.file, args.media_id) if args.cmd == "add" else cmd_list()


if __name__ == "__main__":
    main()
