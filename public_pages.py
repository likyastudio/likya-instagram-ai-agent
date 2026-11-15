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
:root{--bg:#f6f1e8;--card:#fffdf9;--line:#e2d8c9;--ink:#1c1917;--muted:#5f564d;--accent:#b4532a;--fill:#c75a2c;--focus:#1d4ed8;
 --night:#0b1630;--night2:#12224a;--sea:#1f6fb2;--turq:#3fd0c9;--gold:#c9a24a;--cream:#f4ecd8}
@media (prefers-color-scheme:dark){:root{--bg:#151210;--card:#1e1a17;--line:#3a332d;--ink:#f3ede4;--muted:#b5aa9d;--accent:#f0a274;--fill:#d0662f;--focus:#93b4ff}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
a{color:var(--accent)}:focus-visible{outline:3px solid var(--focus);outline-offset:2px;border-radius:10px}
.wrap{max-width:600px;margin:0 auto;padding:16px 16px 40px}
.brand{display:flex;align-items:center;gap:10px;text-decoration:none;color:var(--ink);font-weight:700;margin-bottom:16px;min-height:44px}
.mark{width:36px;height:36px;border-radius:10px;background:var(--fill);color:#fff;display:grid;place-items:center;font-weight:800}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;overflow:hidden;box-shadow:0 4px 18px rgba(28,25,23,.06)}
.hero{display:flex;gap:14px;align-items:center;padding:14px}
.hero img{width:84px;height:105px;border-radius:12px;object-fit:cover;flex:none;background:var(--line)}
h1{font-size:23px;line-height:1.25;margin:0}
h2{font-size:18px;margin:26px 0 10px}
.lead{margin:14px 2px 0;color:var(--muted);white-space:pre-line;overflow-wrap:anywhere}
.stack{display:grid;gap:12px}
/* Prompt kartı */
.prompt{background:var(--night);color:var(--cream);border-radius:20px;overflow:hidden;border:1px solid var(--night2)}
.prompt-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:14px 14px 0 16px}
.prompt-label{font:600 11px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.16em;color:var(--gold)}
.prompt-tool{margin:4px 0 0;font-size:13px;color:rgba(244,236,216,.72)}
.copy{flex:none;min-height:44px;min-width:104px;padding:0 14px;border-radius:999px;border:0;background:var(--turq);color:var(--night);font-weight:800;cursor:pointer;font-size:14px}
.copy[data-state=ok]{background:var(--gold)}
.prompt-text{margin:12px;padding:14px 16px;border-radius:14px;background:var(--night2);white-space:pre-wrap;overflow-wrap:anywhere;font-size:15px;line-height:1.75;
 -webkit-user-select:all;user-select:all;border-left:3px solid var(--sea)}
.copy-hint{margin:0 16px 12px;font-size:13px;color:var(--turq);min-height:0}
/* Adımlar ve ipucu */
.steps{margin:0;padding:0;list-style:none;display:grid;gap:10px;counter-reset:s}
.steps li{display:flex;gap:12px;align-items:flex-start}
.steps li::before{counter-increment:s;content:counter(s);flex:none;width:30px;height:30px;border-radius:50%;display:grid;place-items:center;
 background:var(--sea);color:#fff;font-weight:800;font-size:14px;margin-top:-1px}
.tip{display:flex;gap:10px;padding:14px 16px;border-radius:16px;background:rgba(201,162,74,.16);border:1px solid rgba(201,162,74,.45);overflow-wrap:anywhere}
.tip b{color:var(--gold);flex:none}
/* Bağlantılar */
.links{display:grid;gap:10px;margin:0;padding:0;list-style:none}
.link{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:60px;padding:12px 16px;border-radius:16px;
 background:var(--night);color:var(--cream);text-decoration:none;font-weight:700;overflow-wrap:anywhere;border:1px solid var(--night2)}
.link small{display:block;font-weight:500;font-size:13px;color:rgba(244,236,216,.72)}
.link .host{display:block;font:500 12px ui-monospace,Menlo,monospace;color:var(--turq);margin-top:2px}
.link svg{flex:none;width:20px;height:20px;color:var(--turq)}
/* Liste */
.list{display:grid;gap:12px;margin:0;padding:0;list-style:none}
.row{display:flex;gap:14px;align-items:center;padding:12px;min-height:88px;text-decoration:none;color:var(--ink)}
.row img,.row .ph{width:64px;height:80px;border-radius:12px;object-fit:cover;flex:none;background:var(--line)}
.row b{display:block;line-height:1.3}.row span{color:var(--muted);font-size:14px}
.foot{margin-top:28px;text-align:center;color:var(--muted);font-size:14px}
.foot a{display:inline-flex;align-items:center;min-height:44px;padding:0 8px}
.empty{padding:28px 20px;text-align:center;color:var(--muted)}
"""

ARROW = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2.2" '
         'stroke-linecap="round" stroke-linejoin="round" d="M7 17L17 7M9 7h8v8"/></svg>')

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


def _shell(title: str, description: str, inner: str, image: str | None = None, canonical: str | None = None,
           script: str = "") -> str:
    og_img = f'<meta property="og:image" content="{escape(image)}">' if image else ""
    canon = f'<link rel="canonical" href="{escape(canonical)}">' if canonical else ""
    return f"""<!DOCTYPE html>
<html lang="tr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{escape(title)}</title>
<meta name="description" content="{escape(description[:160])}">
<meta property="og:title" content="{escape(title)}"><meta property="og:description" content="{escape(description[:160])}">
<meta property="og:site_name" content="{SITE_NAME}">{og_img}{canon}
<meta name="referrer" content="no-referrer">
<style>{CSS}</style>
</head><body><main class="wrap">
<a class="brand" href="{SITE_URL}"><span class="mark" aria-hidden="true">L</span>{SITE_NAME}</a>
{inner}
<p class="foot"><a href="{SITE_URL}">likyastudios.live</a> · <a href="https://www.instagram.com/likyastudios/">Instagram'da takip et</a></p>
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
        out.append(f'<li><a class="link" href="{escape(url)}" target="_blank" rel="noopener nofollow"><span>{escape(label)}'
                   f'{f"<small>{escape(note)}</small>" if note else ""}<span class="host">{escape(host)}</span></span>{ARROW}</a></li>')
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
    sections = [f'<div class="card hero">{img}<h1>{escape(title)}</h1></div>']
    if body:
        sections.append(f'<p class="lead">{escape(body)}</p>')
    if links and not prompts:   # sadece link sayfası: linkler en üstte
        sections.append(f'<h2>Bağlantılar</h2>{links}')
    if prompts:
        sections.append(f'<h2>Promptlar</h2>{prompts}')
    if steps:
        sections.append('<h2>Nasıl kullanılır?</h2><ol class="steps">' + "".join(f"<li><span>{escape(s)}</span></li>" for s in steps) + "</ol>")
    if tip:
        sections.append(f'<p class="tip" style="margin-top:20px"><b>İpucu</b><span>{escape(tip)}</span></p>')
    if links and prompts:
        sections.append(f'<h2>Bağlantılar</h2>{links}')
    sections.append(f'<p class="foot" style="margin-top:22px"><a href="{escape(index_url)}">Diğer videoların kaynakları →</a></p>')
    return _shell(f"{title} — {SITE_NAME}", body or title, "\n".join(sections), image=thumb,
                  canonical=f"{base_url}/{page.get('slug', '')}", script=COPY_JS if prompts else "")


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
            rows.append(f'<li><a class="card row" href="{escape(it["href"])}">{img}'
                        f'<span><b>{escape(it["title"])}</b><span>{" · ".join(parts) or "Kaynaklar"}</span></span></a></li>')
        listing = f'<ul class="list">{"".join(rows)}</ul>'
    else:
        listing = '<div class="card empty">Henüz paylaşılan bir kaynak yok.</div>'
    inner = f"""<h1>{escape(title)}</h1>
{f'<p class="lead" style="margin-bottom:18px">{escape(intro)}</p>' if intro else '<div style="height:16px"></div>'}
{listing}"""
    return _shell(title, intro or title, inner, canonical=base_url)


def render_not_found(index_url: str) -> str:
    inner = (f'<div class="card empty"><h1>Sayfa bulunamadı</h1><p>Bu bağlantı kaldırılmış olabilir.</p>'
             f'<p><a href="{escape(index_url)}">Tüm video kaynaklarına git</a></p></div>')
    return _shell(f"Sayfa bulunamadı — {SITE_NAME}", "Sayfa bulunamadı", inner)
