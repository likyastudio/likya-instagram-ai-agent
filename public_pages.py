"""Takipçilerin DM'deki linkle açtığı herkese açık video sayfaları (link.likyastudios.live).

Sayfalar sunucuda üretilir; tüm metinler html.escape ile kaçırılır ve yalnızca http(s)
linklerine izin verilir. Instagram uygulama içi tarayıcısında hızlı açılsın diye dış
kaynak (font, CSS kütüphanesi) yüklenmez.

Bloklar ana sitedeki /prompt sayfalarıyla aynı mantıkta: açıklama, kopyalanabilir
promptlar (LIKYA STUDIOS video kimliği: gece laciverti, Akdeniz mavisi, turkuaz,
antik altın, krem), numaralı adımlar, ipucu kutusu ve bağlantı kartları.
"""
from __future__ import annotations

from html import escape
from urllib.parse import urlparse

SITE_URL = "https://likyastudios.live"
SITE_NAME = "Likya Studios"

CSS = """
:root{--paper:#efe7d2;--bone:#f7f1de;--ink:#15140f;--muted:#5a5448;--line:rgba(21,20,15,.14);--ember:#ad4030;--focus:#1f6fb2;
 --night:#0b1630;--night2:#12224a;--sea:#1f6fb2;--turq:#3fd0c9;--gold:#c9a24a;--cream:#f4ecd8;--cream70:rgba(244,236,216,.72)}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%;color-scheme:light}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.65 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
a{color:inherit}:focus-visible{outline:3px solid var(--focus);outline-offset:2px;border-radius:12px}
.wrap{max-width:672px;margin:0 auto;padding:12px 20px 48px}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:56px;margin-bottom:8px}
.brand{display:flex;align-items:center;gap:10px;text-decoration:none;font-weight:800;letter-spacing:-.01em;min-height:44px}
.mark{width:36px;height:36px;border-radius:50%;display:block;flex:none;box-shadow:0 1px 4px rgba(0,0,0,.25)}
.back{display:inline-flex;white-space:nowrap;align-items:center;gap:6px;min-height:44px;font-size:14px;color:var(--muted);text-decoration:none}
.back svg{width:18px;height:18px}
.eyebrow{margin:0;font:600 11px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.14em;color:var(--ember)}
.head{display:flex;gap:16px;align-items:flex-start;padding:8px 0 22px}
.head img{width:76px;height:95px;border-radius:14px;object-fit:cover;flex:none;background:var(--bone);box-shadow:0 10px 24px -14px rgba(11,22,48,.6)}
h1{margin:8px 0 0;font-size:clamp(28px,7vw,44px);line-height:1.08;letter-spacing:-.025em;font-weight:800;overflow-wrap:anywhere}
h2{margin:30px 0 12px;font-size:24px;line-height:1.2;letter-spacing:-.015em;font-weight:700}
.lead{margin:0 0 6px;color:var(--muted);white-space:pre-line;overflow-wrap:anywhere}
.stack{display:grid;gap:18px}
/* Prompt kartı: gece laciverti, turkuaz etiket, antik altın Kopyala */
.prompt{background:linear-gradient(160deg,var(--night) 0%,var(--night2) 100%);color:var(--cream);border-radius:24px;overflow:hidden;
 box-shadow:0 18px 40px -24px rgba(11,22,48,.7)}
.prompt-head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;padding:20px 20px 0}
.prompt-label{font:600 11px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.18em;color:var(--turq)}
.prompt-tool{margin:4px 0 0;font-size:13px;color:var(--cream70)}
.copy{flex:none;display:inline-flex;align-items:center;gap:8px;min-height:44px;padding:0 20px;border-radius:999px;border:0;background:var(--gold);
 color:var(--night);font-weight:700;font-size:14px;cursor:pointer;transition:transform .1s}
.copy:active{transform:scale(.97)}.copy[data-state=ok]{background:var(--turq)}
.copy svg{width:16px;height:16px}
.prompt-text{margin:16px 12px 12px;padding:16px;border-radius:16px;background:rgba(244,236,216,.06);border:1px solid rgba(31,111,178,.33);
 white-space:pre-wrap;overflow-wrap:anywhere;font-size:15px;line-height:1.75;-webkit-user-select:all;user-select:all}
.copy-hint{margin:0 20px 14px;font-size:13px;color:var(--turq)}.copy-hint:empty{display:none}
/* Adımlar ve ipucu */
.steps{margin:0;padding:0;list-style:none;display:grid;gap:12px;counter-reset:s}
.steps li{display:flex;gap:12px;align-items:flex-start;line-height:1.6}
.steps li::before{counter-increment:s;content:counter(s);flex:none;width:28px;height:28px;border-radius:50%;display:grid;place-items:center;
 background:var(--night);color:var(--gold);font:700 12px ui-monospace,Menlo,monospace;margin-top:1px}
.tip{display:flex;gap:12px;align-items:flex-start;margin:20px 0 0;padding:16px 20px;border-radius:18px;background:rgba(63,208,201,.13);
 border:1px solid rgba(63,208,201,.4);font-size:15px;line-height:1.7;overflow-wrap:anywhere}
.tip svg{flex:none;width:20px;height:20px;margin-top:3px;color:var(--sea)}
/* Bağlantı kartları */
.links{display:grid;gap:12px;margin:0;padding:0;list-style:none}
.link{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:64px;padding:16px 20px;border-radius:20px;
 background:var(--night);color:var(--cream);text-decoration:none;transition:transform .15s}
.link:hover{transform:translateY(-2px)}
.link-label{display:block;font-size:18px;font-weight:700;line-height:1.3}
.link .host{display:block;margin-top:2px;font:500 12px ui-monospace,Menlo,monospace;color:var(--turq);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.link small{display:block;margin-top:2px;font-size:13px;color:var(--cream70)}
.link svg{flex:none;width:20px;height:20px;color:var(--gold)}
.link>span{min-width:0}
/* Kapanış kutusu */
.cta{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:16px;margin-top:40px;padding:24px;border-radius:28px;
 background:linear-gradient(135deg,var(--night),var(--sea));color:var(--cream)}
.cta p{margin:0}.cta b{display:block;font-size:20px;line-height:1.3}.cta span{font-size:14px;color:rgba(244,236,216,.8)}
.cta a{display:inline-flex;align-items:center;gap:8px;min-height:44px;padding:0 20px;border-radius:999px;background:var(--gold);color:var(--night);
 font-weight:700;font-size:14px;text-decoration:none}
/* Liste */
.list{display:grid;gap:14px;margin:0;padding:0;list-style:none}
.row{display:flex;gap:16px;align-items:center;padding:14px;min-height:96px;border-radius:24px;text-decoration:none;background:var(--night);
 color:var(--cream);transition:transform .15s}
.row:hover{transform:translateY(-3px)}
.row img,.row .ph{width:64px;height:80px;border-radius:12px;object-fit:cover;flex:none;background:var(--night2)}
.row b{display:block;font-size:17px;line-height:1.3}.row .meta{display:block;margin-top:4px;font:600 11px ui-monospace,Menlo,monospace;
 text-transform:uppercase;letter-spacing:.12em;color:var(--turq)}
.row .go{display:block;margin-top:8px;font-size:14px;font-weight:700;color:var(--gold)}
.empty{padding:28px 20px;border-radius:24px;background:var(--bone);text-align:center;color:var(--muted)}
.foot{margin-top:32px;text-align:center;color:var(--muted);font-size:14px}
.foot a{display:inline-flex;align-items:center;min-height:44px;padding:0 8px}
"""

_SVG = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2.2" '
        'stroke-linecap="round" stroke-linejoin="round" d="{}"/></svg>')
ARROW = _SVG.format("M7 17L17 7M9 7h8v8")
BULB = _SVG.format("M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V17h6v-.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z")
BACK = _SVG.format("M15 18l-6-6 6-6")

# Kopyala: Clipboard API → execCommand yedeği → "basılı tutup kopyala" uyarısı (Instagram tarayıcısı için)
COPY_JS = """<script>
document.addEventListener('click',async function(e){
  var b=e.target.closest('.copy'); if(!b) return;
  var box=document.getElementById(b.getAttribute('aria-controls')); var hint=b.closest('.prompt').querySelector('.copy-hint');
  var text=box.innerText, ok=false;
  try{ if(navigator.clipboard&&window.isSecureContext){ await navigator.clipboard.writeText(text); ok=true; } }catch(_){}
  if(!ok){ try{ var t=document.createElement('textarea'); t.value=text; t.setAttribute('readonly',''); t.style.position='fixed'; t.style.opacity='0';
    document.body.appendChild(t); t.select(); t.setSelectionRange(0,t.value.length); ok=document.execCommand('copy'); t.remove(); }catch(_){} }
  if(ok){ b.dataset.state='ok'; b.textContent='Kopyalandı ✓'; hint.textContent='';
    setTimeout(function(){ b.dataset.state=''; b.textContent='Kopyala'; },2200); }
  else { var r=document.createRange(); r.selectNodeContents(box); var s=getSelection(); s.removeAllRanges(); s.addRange(r);
    hint.textContent='Metni basılı tutup “Kopyala”ya dokun.'; }
});
</script>"""


def safe_url(url: str) -> str | None:
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme in ("http", "https") and p.netloc:
        return url
    return None


def asset_root(base_url: str) -> str:
    """Logo gibi dosyaların kök adresi (ör. https://link.likyastudios.live veya servis adresi)."""
    return base_url[:-2] if base_url.endswith("/p") else base_url


def _shell(title: str, description: str, inner: str, image: str | None = None, canonical: str | None = None,
           script: str = "", root: str = "", back_url: str | None = None) -> str:
    image = image or (f"{root}/brand/og.png" if root else None)
    og_img = (f'<meta property="og:image" content="{escape(image)}"><meta name="twitter:card" content="summary_large_image">'
              if image else "")
    canon = f'<link rel="canonical" href="{escape(canonical)}">' if canonical else ""
    back = f'<a class="back" href="{escape(back_url)}">{BACK}Tüm kaynaklar</a>' if back_url else ""
    return f"""<!DOCTYPE html>
<html lang="tr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light"><meta name="theme-color" content="#efe7d2">
<title>{escape(title)}</title>
<meta name="description" content="{escape(description[:160])}">
<meta property="og:title" content="{escape(title)}"><meta property="og:description" content="{escape(description[:160])}">
<meta property="og:site_name" content="{SITE_NAME}">{og_img}{canon}
<meta name="referrer" content="no-referrer">
<link rel="icon" href="{root}/favicon.ico" sizes="any"><link rel="icon" type="image/png" href="{root}/brand/logo-192.png">
<link rel="apple-touch-icon" href="{root}/brand/logo-192.png">
<style>{CSS}</style>
</head><body><main class="wrap">
<div class="top"><a class="brand" href="{SITE_URL}"><img class="mark" src="{root}/brand/logo-96.png" alt="" width="36" height="36">{SITE_NAME}</a>{back}</div>
{inner}
<div class="cta"><p><b>Denedin mi?</b><span>Sonucu Instagram'da @likyastudios etiketiyle paylaş, görelim.</span></p>
<a href="https://www.instagram.com/likyastudios/">@likyastudios</a></div>
<p class="foot"><a href="{SITE_URL}">likyastudios.live</a></p>
</main>{script}</body></html>"""


def _links_html(links: list[dict]) -> str:
    out = []
    for link in links or []:
        url = safe_url(link.get("url", ""))
        if not url:
            continue
        host = urlparse(url).netloc.removeprefix("www.")
        label = (link.get("label") or "").strip() or host
        note = (link.get("note") or "").strip()
        out.append(f'<li><a class="link" href="{escape(url)}" target="_blank" rel="noopener nofollow"><span>'
                   f'<span class="link-label">{escape(label)}</span><span class="host">{escape(host)}</span>'
                   f'{f"<small>{escape(note)}</small>" if note else ""}</span>{ARROW}</a></li>')
    return f'<ul class="links">{"".join(out)}</ul>' if out else ""


def _prompts_html(prompts: list[dict]) -> str:
    cards = []
    for i, pr in enumerate(prompts or []):
        text = (pr.get("text") or "").strip()
        if not text:
            continue
        label, tool = (pr.get("label") or "Prompt").strip(), (pr.get("tool") or "").strip()
        cards.append(f"""<section class="prompt" aria-label="{escape(label)}">
<div class="prompt-head"><div><div class="prompt-label">{escape(label)}</div>{f'<p class="prompt-tool">{escape(tool)}</p>' if tool else ''}</div>
<button class="copy" type="button" aria-controls="pt{i}" aria-label="{escape(label)}: Kopyala">Kopyala</button></div>
<div class="prompt-text" id="pt{i}">{escape(text)}</div><p class="copy-hint" aria-live="polite"></p></section>""")
    return f'<div class="stack">{"".join(cards)}</div>' if cards else ""


def render_video_page(page: dict, thumb: str | None, base_url: str, index_url: str) -> str:
    title, body = page.get("title", ""), (page.get("body") or "").strip()
    prompts = _prompts_html(page.get("prompts") or [])
    steps = [s for s in (page.get("steps") or []) if str(s).strip()]
    tip = (page.get("tip") or "").strip()
    links = _links_html(page.get("links") or [])
    img = f'<img src="{escape(thumb)}" alt="" referrerpolicy="no-referrer">' if thumb else ""
    eyebrow = "Videodaki promptlar" if prompts else "Videodaki bağlantılar" if links else "Video kaynakları"
    sections = [f'<header class="head">{img}<div><p class="eyebrow">{eyebrow}</p><h1>{escape(title)}</h1></div></header>']
    if body:
        sections.append(f'<p class="lead">{escape(body)}</p>')
    if links and not prompts:   # sadece link sayfası: linkler en üstte
        sections.append(f'<h2>Bağlantılar</h2>{links}')
    if prompts:
        sections.append(f'<h2>Promptlar</h2>{prompts}')
    if steps:
        sections.append('<h2>Nasıl kullanılır?</h2><ol class="steps">' + "".join(f"<li><span>{escape(s)}</span></li>" for s in steps) + "</ol>")
    if tip:
        sections.append(f'<p class="tip">{BULB}<span>{escape(tip)}</span></p>')
    if links and prompts:
        sections.append(f'<h2>Bağlantılar</h2>{links}')
    return _shell(f"{title} — {SITE_NAME}", body or title, "\n".join(sections), image=thumb,
                  canonical=f"{base_url}/{page.get('slug', '')}", script=COPY_JS if prompts else "",
                  root=asset_root(base_url), back_url=index_url)


def render_index(title: str, intro: str, items: list[dict], base_url: str) -> str:
    """items: [{title, href, thumb, link_count, prompt_count}] — yalnızca aktif sayfalar."""
    if items:
        rows = []
        for it in items:
            img = (f'<img src="{escape(it["thumb"])}" alt="" referrerpolicy="no-referrer" loading="lazy">'
                   if it.get("thumb") else '<span class="ph" aria-hidden="true"></span>')
            parts = []
            if it.get("prompt_count"):
                parts.append(f'{it["prompt_count"]} prompt')
            if it.get("link_count"):
                parts.append(f'{it["link_count"]} bağlantı')
            rows.append(f'<li><a class="row" href="{escape(it["href"])}">{img}'
                        f'<span><span class="meta">{" · ".join(parts) or "Kaynaklar"}</span><b>{escape(it["title"])}</b>'
                        f'<span class="go">Aç →</span></span></a></li>')
        listing = f'<ul class="list">{"".join(rows)}</ul>'
    else:
        listing = '<div class="empty">Henüz paylaşılan bir kaynak yok.</div>'
    inner = f"""<header class="head"><div><p class="eyebrow">Video kaynakları</p><h1>{escape(title)}</h1></div></header>
{f'<p class="lead" style="margin-bottom:22px">{escape(intro)}</p>' if intro else ''}
{listing}"""
    return _shell(title, intro or title, inner, canonical=base_url, root=asset_root(base_url))


def render_not_found(index_url: str) -> str:
    root = asset_root(index_url)
    inner = (f'<div class="empty"><h1 style="font-size:28px">Sayfa bulunamadı</h1><p>Bu bağlantı kaldırılmış olabilir.</p>'
             f'<p><a href="{escape(index_url)}">Tüm video kaynaklarına git</a></p></div>')
    return _shell(f"Sayfa bulunamadı — {SITE_NAME}", "Sayfa bulunamadı", inner, root=root)
