import asyncio
import hashlib
import hmac
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as ig
import auth
import public_pages
from app import app, valid_signature
from store import DataStore

client = TestClient(app)
PIN = {"X-Admin-Pin": ig.ADMIN_PIN}
SECRET = "test_app_secret_12345"
BASE = "https://link.likyastudios.live"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Her test temiz, yerel bir veri deposu ve PIN modu ile başlar."""
    monkeypatch.setattr(ig, "STORE", DataStore("", "x.json", tmp_path / "data.json", ig._load_seed()))
    monkeypatch.setattr(ig, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(ig, "PUBLIC_BASE_URL", BASE)
    monkeypatch.setattr(ig, "IG_APP_SECRET", SECRET)
    ig.PIN_FAILURES.clear()
    ig.RECENT_LOGS.clear()
    ig.SEEN_EVENT_IDS.clear()
    ig.MEDIA_CACHE.update(at=0.0, items=[])
    yield


def signed(payload: dict) -> tuple[bytes, dict]:
    raw = json.dumps(payload).encode()
    return raw, {"X-Hub-Signature-256": "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()}


def comment_payload(cid="c1", text="link lütfen", media="18121864655327295", user="ayse"):
    return {"object": "instagram", "entry": [{"id": "1", "time": 1, "changes": [{"field": "comments", "value": {
        "id": cid, "text": text, "from": {"id": "u1", "username": user}, "media": {"id": media}}}]}]}


# ---------------------------------------------------------------- Temel uç noktalar

def test_dashboard_shell_has_no_private_data():
    ig.RECENT_LOGS.append({"user": "gizli_kullanici", "input": "<img src=x onerror=alert(1)>"})
    html = client.get("/").text
    assert "Likya Studios" in html
    assert "gizli_kullanici" not in html and "onerror=alert(1)" not in html and "likya2026" not in html


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_webhook_verify():
    ok = client.get(f"/webhook?hub.mode=subscribe&hub.verify_token={ig.IG_VERIFY_TOKEN}&hub.challenge=777")
    assert ok.status_code == 200 and ok.text == "777"
    assert client.get("/webhook?hub.mode=subscribe&hub.verify_token=x&hub.challenge=1").status_code == 403


def test_signature_validation():
    body = b'{"object": "instagram"}'
    good = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert valid_signature(body, good) is True
    assert valid_signature(body, "sha256=bad") is False


def test_webhook_rejected_signature_is_counted():
    before = ig.STATS["webhook_rejected"]
    assert client.post("/webhook", content=b"{}", headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 401
    assert ig.STATS["webhook_rejected"] == before + 1


# ---------------------------------------------------------------- Yorum → standart yanıt + sayfa linki DM

@pytest.fixture
def outbox(monkeypatch):
    sent = {"replies": [], "private": [], "dms": []}

    async def reply(cid, text):
        sent["replies"].append((cid, text)); return True

    async def private(cid, text):
        sent["private"].append((cid, text)); return True

    async def dm(rid, text):
        sent["dms"].append((rid, text)); return True

    monkeypatch.setattr(ig, "reply_to_comment", reply)
    monkeypatch.setattr(ig, "send_private_reply", private)
    monkeypatch.setattr(ig, "send_instagram_dm", dm)
    return sent


def post_comment(**kw):
    raw, headers = signed(comment_payload(**kw))
    assert client.post("/webhook", content=raw, headers=headers).status_code == 200


def test_comment_on_video_with_page_gets_standard_reply_and_page_link(outbox):
    post_comment()
    settings = ig._load_seed()["settings"]
    assert outbox["replies"] == [("c1", settings["public_reply"])]
    cid, dm = outbox["private"][0]
    assert cid == "c1" and f"{BASE}/orgu-yemek" in dm
    log = ig.RECENT_LOGS[-1]
    assert log["comment_ok"] and log["dm_ok"] and log["link"] == f"{BASE}/orgu-yemek"


def test_reply_is_the_same_for_every_comment(outbox):
    for i, text in enumerate(["harika", "link?", "fiyat ne", "👏"]):
        post_comment(cid=f"c{i}", text=text)
    assert len(outbox["replies"]) == 4 and len({t for _, t in outbox["replies"]}) == 1


def test_comment_on_video_without_page_gets_general_link(outbox):
    post_comment(media="999")
    assert ig.RECENT_LOGS[-1]["link"] == BASE
    assert BASE in outbox["private"][0][1] and "/orgu-yemek" not in outbox["private"][0][1]


def test_inactive_page_falls_back_to_general_link(outbox):
    client.put("/api/pages/18121864655327295", headers=PIN, json={"title": "Örgü", "slug": "orgu-yemek", "is_active": False})
    post_comment()
    assert ig.RECENT_LOGS[-1]["link"] == BASE


def test_external_url_is_used_in_dm(outbox):
    client.put("/api/pages/18121864655327295", headers=PIN,
               json={"title": "Örgü", "external_url": "https://likyastudios.live/prompt/orgu-yemek"})
    post_comment()
    assert "https://likyastudios.live/prompt/orgu-yemek" in outbox["private"][0][1]


def test_own_comments_are_ignored(outbox):
    post_comment(user="likyastudios")
    assert outbox["replies"] == [] and outbox["private"] == []


def test_settings_change_applies_to_next_comment(outbox):
    r = client.post("/api/settings", headers=PIN, json={"public_reply": "DM'ne baktık {user} 💌", "dm_template": "Al bakalım: {link}"})
    assert r.status_code == 200
    post_comment()
    assert outbox["replies"][0][1] == "DM'ne baktık @ayse 💌"
    assert outbox["private"][0][1] == f"Al bakalım: {BASE}/orgu-yemek"


def test_dm_template_without_link_placeholder_still_contains_link(outbox):
    client.post("/api/settings", headers=PIN, json={"public_reply": "ok", "dm_template": "Merhaba!"})
    post_comment()
    assert outbox["private"][0][1] == f"Merhaba!\n{BASE}/orgu-yemek"


def test_direct_dm_without_ai_key_gets_general_link(outbox, monkeypatch):
    monkeypatch.setattr(ig, "GEMINI_API_KEY", "")
    payload = {"object": "instagram", "entry": [{"id": "1", "messaging": [
        {"sender": {"id": "s1"}, "recipient": {"id": "p"}, "message": {"mid": "m-dm-1", "text": "link var mı"}}]}]}
    raw, headers = signed(payload)
    client.post("/webhook", content=raw, headers=headers)
    assert outbox["dms"][0][0] == "s1" and BASE in outbox["dms"][0][1]


# ---------------------------------------------------------------- Sayfa ve ayar API'leri

def test_page_validation_and_slug_conflict():
    assert client.put("/api/pages/123", headers=PIN, json={"title": ""}).status_code == 422
    assert client.put("/api/pages/123", headers=PIN, json={"title": "X", "links": [{"label": "kötü", "url": "javascript:alert(1)"}]}).status_code == 422
    assert client.put("/api/pages/123", headers=PIN, json={"title": "X", "slug": "api"}).status_code == 422
    assert client.put("/api/pages/123", headers=PIN, json={"title": "X", "external_url": "ftp://x"}).status_code == 422
    assert client.put("/api/pages/123", headers=PIN, json={"title": "Başka", "slug": "orgu-yemek"}).status_code == 409
    ok = client.put("/api/pages/123", headers=PIN, json={"title": "Çılgın Şeyler Öğren", "prompts": [{"label": "P", "text": "metin"}],
                                                         "steps": ["bir", " ", "iki"], "tip": "ipucu"})
    assert ok.status_code == 200
    page = ok.json()["page"]
    assert page["slug"] == "cilgin-seyler-ogren" and page["steps"] == ["bir", "iki"] and page["prompts"][0]["text"] == "metin"
    assert client.delete("/api/pages/123", headers=PIN).status_code == 200
    assert client.delete("/api/pages/123", headers=PIN).status_code == 404


def test_settings_validation():
    assert client.post("/api/settings", headers=PIN, json={"public_reply": "", "dm_template": "x"}).status_code == 422
    assert client.post("/api/settings", headers=PIN, json={"public_reply": "x" * 301, "dm_template": "x"}).status_code == 422
    assert client.post("/api/settings", headers=PIN, json={"public_reply": "ok", "dm_template": ""}).status_code == 422


def test_posts_include_pages_even_without_instagram_token():
    data = client.get("/api/posts", headers=PIN).json()
    assert "18121864655327295" in {p["id"] for p in data["posts"]}
    assert data["general_url"] == BASE


def test_new_instagram_post_appears_automatically(monkeypatch):
    ig.MEDIA_CACHE.update(at=10**12, items=[{"id": "777", "title": "Yeni video", "caption": "", "media_type": "VIDEO",
                                             "permalink": "https://instagram.com/p/x", "timestamp": "2026-11-15T10:00:00+0000", "thumb": None}])
    monkeypatch.setattr(ig.time, "monotonic", lambda: 10**12 + 1)
    post = next(p for p in client.get("/api/posts", headers=PIN).json()["posts"] if p["id"] == "777")
    assert post["page"] is None and post["title"] == "Yeni video"


@pytest.mark.parametrize("method,path", [
    ("get", "/api/status"), ("get", "/api/posts"), ("get", "/api/logs"), ("get", "/api/settings"),
    ("post", "/api/settings"), ("put", "/api/pages/1"), ("delete", "/api/pages/1"),
])
def test_admin_api_requires_login(method, path):
    kwargs = {"json": {}} if method in ("post", "put") else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401
    assert getattr(client, method)(path, headers={"X-Admin-Pin": "yanlis"}, **kwargs).status_code == 401


def test_pin_bruteforce_is_rate_limited():
    for _ in range(ig.PIN_FAIL_LIMIT):
        client.get("/api/status", headers={"X-Admin-Pin": "x"})
    assert client.get("/api/status", headers=PIN).status_code == 429


def test_status_has_no_secrets():
    flat = json.dumps(client.get("/api/status", headers=PIN).json())
    for secret in (ig.IG_PAGE_ACCESS_TOKEN, ig.GEMINI_API_KEY, SECRET, ig.ADMIN_PIN, ig.SESSION_SECRET):
        if secret:
            assert secret not in flat


# ---------------------------------------------------------------- Google girişi

@pytest.fixture
def google_mode(monkeypatch):
    monkeypatch.setattr(ig, "GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(ig, "ADMIN_EMAILS", {"sahip@example.com"})
    monkeypatch.setattr(ig, "SESSION_SECRET", "s" * 40)
    yield


def test_google_mode_disables_pin(google_mode):
    assert client.get("/api/session").json()["mode"] == "google"
    assert client.get("/api/status", headers=PIN).status_code == 401


def test_google_session_cookie_grants_access(google_mode):
    c = TestClient(app, cookies={auth.SESSION_COOKIE: auth.sign_session("sahip@example.com", "s" * 40)})
    assert c.get("/api/status").status_code == 200
    assert c.get("/api/session").json()["email"] == "sahip@example.com"
    # Başka siteden gelen değiştirme isteği reddedilir
    assert c.post("/api/settings", headers={"Origin": "https://kotu.example"}, json={}).status_code == 403


def test_session_rejects_tampering_expiry_and_other_emails():
    secret, allowed = "k" * 40, {"sahip@example.com"}
    tok = auth.sign_session("sahip@example.com", secret)
    assert auth.read_session(tok, secret, allowed) == "sahip@example.com"
    assert auth.read_session(tok + "x", secret, allowed) is None
    assert auth.read_session(tok, "baska" * 8, allowed) is None
    assert auth.read_session(tok, secret, {"diger@example.com"}) is None
    assert auth.read_session(auth.sign_session("sahip@example.com", secret, now=0), secret, allowed) is None


def test_google_login_rejects_unknown_accounts(google_mode, monkeypatch):
    async def fake_verify(credential, client_id, allowed):
        raise ValueError("Bu Google hesabının panele erişim izni yok.")
    monkeypatch.setattr(auth, "verify_google_credential", fake_verify)
    r = client.post("/auth/google", json={"credential": "x"})
    assert r.status_code == 403 and auth.SESSION_COOKIE not in r.cookies


def test_google_login_sets_httponly_cookie(google_mode, monkeypatch):
    async def fake_verify(credential, client_id, allowed):
        return "sahip@example.com"
    monkeypatch.setattr(auth, "verify_google_credential", fake_verify)
    r = client.post("/auth/google", json={"credential": "x"})
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()


# ---------------------------------------------------------------- Herkese açık sayfalar

def test_public_pages_on_path_and_host():
    page = client.get("/p/orgu-yemek")
    assert page.status_code == 200 and "Kopyala" in page.text and "Örgüden (tığ işi yün)" in page.text
    assert client.get("/orgu-yemek", headers={"host": "link.likyastudios.live"}).status_code == 200
    index = client.get("/", headers={"host": "link.likyastudios.live"})
    assert "Stirling PDF" in index.text and "Instagram Paneli" not in index.text
    assert client.get("/p/yok-boyle-sayfa").status_code == 404
    assert client.get("/orgu-yemek").status_code == 404   # panel adresinde sayfa yolu açık değil


def test_public_page_escapes_content():
    client.put("/api/pages/555", headers=PIN, json={
        "title": "<script>alert(1)</script>", "slug": "xss", "body": "<img src=x onerror=alert(2)>",
        "prompts": [{"label": "</div><b>", "text": "<svg onload=alert(3)>"}],
        "links": [{"label": "<i>a</i>", "url": "https://ok.example/?q=<x>"}]})
    html = client.get("/p/xss").text
    for bad in ("<script>alert(1)", "<img src=x", "<svg onload", "</div><b>", "<i>a</i>"):
        assert bad not in html


def test_safe_url():
    assert public_pages.safe_url("https://a.com") and not public_pages.safe_url("javascript:alert(1)")
    assert not public_pages.safe_url("data:text/html,x") and not public_pages.safe_url("/relative")


# ---------------------------------------------------------------- Depo

def test_store_refuses_to_overwrite_when_unreadable(tmp_path):
    bad = tmp_path / "data.json"
    bad.write_text("{bozuk json", encoding="utf-8")
    store = DataStore("", "x", bad, {"settings": {}, "pages": {}})

    async def run():
        await store.load()
        with pytest.raises(RuntimeError):
            await store.save({"settings": {}, "pages": {}})
    asyncio.run(run())
    assert bad.read_text(encoding="utf-8") == "{bozuk json"


def test_store_persists_between_instances(tmp_path):
    path = tmp_path / "d.json"

    async def run():
        a = DataStore("", "x", path, {"settings": {"public_reply": "v1"}, "pages": {}})
        data = await a.load(); data["settings"]["public_reply"] = "v2"; await a.save(data)
        b = DataStore("", "x", path, {"settings": {"public_reply": "v1", "yeni": "ek"}, "pages": {}})
        loaded = await b.load()
        assert loaded["settings"]["public_reply"] == "v2" and loaded["settings"]["yeni"] == "ek"
    asyncio.run(run())


def test_seed_file_is_valid():
    seed = json.loads(Path(ig.SEED_FILE).read_text(encoding="utf-8"))
    slugs = [p["slug"] for p in seed["pages"].values()]
    assert len(slugs) == len(set(slugs))
    for p in seed["pages"].values():
        for link in p["links"]:
            assert public_pages.safe_url(link["url"])


def test_webhook_with_invalid_encoding_returns_400():
    raw = '{"object":"instagram","entry":[{"text":"ğ"}]}'.encode("cp1254")
    sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    assert client.post("/webhook", content=raw, headers={"X-Hub-Signature-256": sig}).status_code == 400


def test_per_post_dm_message_overrides_shared_template(outbox):
    client.put("/api/pages/18121864655327295", headers=PIN, json={
        "title": "Örgü", "slug": "orgu-yemek", "dm_message": "Örgü promptların burada 🧶 {link}"})
    post_comment()
    post_comment(cid="c2", media="17917802667556092")   # özel metni olmayan gönderi
    assert outbox["private"][0][1] == f"Örgü promptların burada 🧶 {BASE}/orgu-yemek"
    assert outbox["private"][1][1] != outbox["private"][0][1] and f"{BASE}/stirling-pdf" in outbox["private"][1][1]
    # yorum yanıtı iki gönderide de aynı standart metin
    assert outbox["replies"][0][1] == outbox["replies"][1][1]


def test_logo_assets_are_served_and_used():
    for path in ("/brand/logo-96.png", "/brand/logo-192.png", "/brand/og.png", "/favicon.ico"):
        assert client.get(path).status_code == 200, path
    assert client.get("/brand/logo-96.png", headers={"host": "link.likyastudios.live"}).status_code == 200
    assert "/brand/logo-96.png" in client.get("/").text
    page = client.get("/p/stirling-pdf").text
    assert "/brand/logo-96.png" in page and "/favicon.ico" in page
    assert client.put("/api/pages/9", headers=PIN, json={"title": "X", "slug": "brand"}).status_code == 422


# ---------------------------------------------------------------- Video dosyası → taslak → otomatik bağlama

import video_import

FIX = Path(__file__).parent / "fixtures"


def markitdown_page():
    return video_import.page_from_video_json(json.loads((FIX / "025-markitdown.json").read_text(encoding="utf-8")))


def media(mid, caption, ts="2026-11-16T18:00:00+0000"):
    return {"id": mid, "title": caption.split("\n")[0][:90], "caption": caption, "media_type": "VIDEO",
            "permalink": "", "timestamp": ts, "thumb": None}


def set_media(monkeypatch, items):
    ig.MEDIA_CACHE.update(at=10**12, items=items)
    monkeypatch.setattr(ig.time, "monotonic", lambda: 10**12 + 1)


def add_draft(page):
    async def run():
        data = await ig.STORE.load(force=True)
        data["drafts"][page["slug"]] = page
        await ig.STORE.save(data)
    asyncio.run(run())


def test_video_json_conversion_keeps_everything():
    p = markitdown_page()
    assert p["slug"] == "markitdown" and len(p["prompts"]) == 2 and len(p["links"]) == 2 and len(p["steps"]) == 5
    assert p["prompts"][0]["label"].startswith("Kurulum ve kullanım — ") and p["tip"]
    with pytest.raises(ValueError):
        video_import.page_from_video_json({"title": "x"})


def test_caption_matching_rules():
    p = markitdown_page()
    assert video_import.caption_matches(p, "PDF'ini yapay zekâya vermeden önce MarkItDown ile çevir 📄")
    assert video_import.caption_matches(p, "#markitdown ile dosyalar")
    assert not video_import.caption_matches(p, "Yapay zekâ için ücretsiz Google aracı")   # genel kelimeler eşleşmez
    og = video_import.page_from_video_json(json.loads((FIX / "024-ogrenci.json").read_text(encoding="utf-8")))
    assert video_import.caption_matches(og, "Öğrenciysen bu 8 aracı ücretsiz kullanabilirsin 🎓")


def test_ambiguous_or_old_posts_are_not_bound():
    p = markitdown_page()
    two_posts = [media("1", "MarkItDown bölüm 1"), media("2", "MarkItDown bölüm 2")]
    assert video_import.find_bindings({"markitdown": p}, two_posts, set()) == {}
    old = [media("3", "MarkItDown eski video", ts="2026-10-01T10:00:00+0000")]
    assert video_import.find_bindings({"markitdown": p}, old, set()) == {}
    assert video_import.find_bindings({"markitdown": p}, [media("4", "MarkItDown")], {"4"}) == {}


def test_best_matching_draft_wins_when_two_drafts_share_a_word():
    # 25.11.2026: iki "Claude" taslağı sıradayken 034'ün gönderisi hiçbirine bağlanmamıştı
    codex = {"slug": "claude-codex-gorsel", "title": "Claude görsel üretemez, Codex'e ürettirir", "date": "2026-11-24"}
    reels = {"slug": "claude-reels-atolyesi", "title": "Claude Code ile Reels atölyesi", "date": "2026-11-24"}
    post = media("9", "Claude fotoğraf gibi tek bir görsel bile üretemez. OpenAI'ın Claude Code için resmi Codex eklentisiyle",
                 ts="2026-11-25T03:03:00+0000")
    drafts = {"claude-codex-gorsel": codex, "claude-reels-atolyesi": reels}
    assert video_import.find_bindings(drafts, [post], set()) == {"9": "claude-codex-gorsel"}
    tie = media("10", "Claude ile yeni video", ts="2026-11-25T03:03:00+0000")
    assert video_import.find_bindings(drafts, [tie], set()) == {}   # eşitlik: elle bağlanır


def test_publish_time_decides_between_equal_drafts():
    # Kelimeler eşit tutsa bile, sitede açılış saati paylaşım saatine uyan taslak seçilir
    a = {"slug": "claude-codex-gorsel", "title": "Claude görsel", "date": "2026-11-24", "publish_at": "2026-11-25"}
    b = {"slug": "claude-reels-atolyesi", "title": "Claude atölye", "date": "2026-11-24",
         "publish_at": "2026-11-26T06:04:00+03:00"}
    drafts = {"claude-codex-gorsel": a, "claude-reels-atolyesi": b}
    post_25 = media("11", "Claude ile yeni video", ts="2026-11-25T03:03:00+0000")   # 25.11 06:03 TR
    post_26 = media("12", "Claude ile yeni video", ts="2026-11-26T03:05:00+0000")   # 26.11 06:05 TR
    assert video_import.find_bindings(drafts, [post_25], set()) == {"11": "claude-codex-gorsel"}
    assert video_import.find_bindings(drafts, [post_26], set()) == {"12": "claude-reels-atolyesi"}
    page = video_import.page_from_video_json({"slug": "x-y", "title": "X", "blocks": [], "publishAt": "2026-11-25"})
    assert page["publish_at"] == "2026-11-25"


def test_draft_page_is_public_before_video_is_posted():
    add_draft(markitdown_page())
    r = client.get("/p/markitdown")
    assert r.status_code == 200 and "pip install" in r.text and "Kopyala" in r.text


def test_new_post_is_bound_automatically_when_panel_loads(monkeypatch):
    add_draft(markitdown_page())
    set_media(monkeypatch, [media("901", "Dosyanı yapay zekâya hazırla: MarkItDown 🧩")])
    data = client.get("/api/posts", headers=PIN).json()
    post = next(p for p in data["posts"] if p["id"] == "901")
    assert post["page"]["slug"] == "markitdown" and post["page_url"] == f"{BASE}/markitdown"
    assert data["drafts"] == []
    assert ig.RECENT_LOGS[-1]["kind"] == "system"


def test_first_comment_on_new_video_gets_its_page_link(outbox, monkeypatch):
    add_draft(markitdown_page())
    set_media(monkeypatch, [media("902", "MarkItDown ile PDF'i Markdown'a çevir")])
    post_comment(media="902")
    assert f"{BASE}/markitdown" in outbox["private"][0][1]


def test_comment_on_unmatched_post_still_gets_general_link(outbox, monkeypatch):
    add_draft(markitdown_page())
    set_media(monkeypatch, [media("903", "Bugün Antalya'da çekim yaptık")])
    post_comment(media="903")
    assert ig.RECENT_LOGS[-1]["link"] == BASE
    assert client.get("/api/posts", headers=PIN).json()["drafts"][0]["slug"] == "markitdown"


def test_manual_bind_from_panel():
    add_draft(markitdown_page())
    assert client.post("/api/drafts/yok/bind/1", headers=PIN).status_code == 404
    r = client.post("/api/drafts/markitdown/bind/904", headers=PIN)
    assert r.status_code == 200 and r.json()["page_url"] == f"{BASE}/markitdown"
    add_draft({**markitdown_page(), "slug": "ikinci"})
    assert client.post("/api/drafts/ikinci/bind/904", headers=PIN).status_code == 409
    assert client.put("/api/pages/905", headers=PIN, json={"title": "X", "slug": "ikinci"}).status_code == 409


def test_video_page_command_adds_draft(monkeypatch, tmp_path, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("video_page", Path(__file__).parent.parent / "scripts" / "video_page.py")
    cmd = importlib.util.module_from_spec(spec); spec.loader.exec_module(cmd)
    store = {"settings": {}, "pages": {"1": {"slug": "eski"}}, "drafts": {}}
    writes = []
    monkeypatch.setattr(cmd, "read_store", lambda: (json.loads(json.dumps(store)), "42"))
    monkeypatch.setattr(cmd, "write_store", lambda data, gen: writes.append((data, gen)))
    cmd.cmd_add(str(FIX / "025-markitdown.json"), None)
    data, gen = writes[-1]
    assert gen == "42" and "markitdown" in data["drafts"] and "markitdown" in capsys.readouterr().out
    cmd.cmd_add(str(FIX / "025-markitdown.json"), "777")
    data, _ = writes[-1]
    assert data["pages"]["777"]["slug"] == "markitdown" and "markitdown" not in data["drafts"]
    store["pages"]["2"] = {"slug": "markitdown"}
    with pytest.raises(SystemExit):
        cmd.cmd_add(str(FIX / "025-markitdown.json"), None)


def test_video_page_command_site_option(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("video_page2", Path(__file__).parent.parent / "scripts" / "video_page.py")
    cmd = importlib.util.module_from_spec(spec); spec.loader.exec_module(cmd)
    writes = []
    monkeypatch.setattr(cmd, "read_store", lambda: ({"settings": {}, "pages": {}, "drafts": {}}, "1"))
    monkeypatch.setattr(cmd, "write_store", lambda data, gen: writes.append(data))
    cmd.cmd_add(str(FIX / "024-ogrenci.json"), None, site=True)
    assert writes[-1]["drafts"]["ogrenci"]["external_url"] == "https://likyastudios.live/prompt/ogrenci"


def test_bound_draft_with_site_link_sends_site_url(outbox, monkeypatch):
    og = video_import.page_from_video_json(json.loads((FIX / "024-ogrenci.json").read_text(encoding="utf-8")))
    og["external_url"] = "https://likyastudios.live/prompt/ogrenci"
    add_draft(og)
    set_media(monkeypatch, [media("906", "Öğrenciysen bu 8 aracı ücretsiz kullan 🎓", ts="2026-11-15T17:00:00+0000")])
    post_comment(media="906")
    assert "https://likyastudios.live/prompt/ogrenci" in outbox["private"][0][1]


def test_old_slug_redirects_after_rename():
    client.put("/api/pages/18121864655327295", headers=PIN, json={"title": "Örgü", "slug": "orgu-yemek-yeni"})
    r = client.get("/p/orgu-yemek", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"].endswith("/orgu-yemek-yeni")
    client.put("/api/pages/18121864655327295", headers=PIN, json={"title": "Örgü", "slug": "orgu-yemek-yeni",
                                                               "external_url": "https://likyastudios.live/prompt/orgu-yemek"})
    assert client.get("/p/orgu-yemek", follow_redirects=False).headers["location"] == "https://likyastudios.live/prompt/orgu-yemek"


# ---------------------------------------------------------------- Kart (generic template) DM

@pytest.fixture
def cards(monkeypatch):
    sent = []

    async def card(cid, payload):
        sent.append((cid, payload)); return True
    monkeypatch.setattr(ig, "send_private_reply_card", card)
    return sent


def test_comment_dm_is_a_card_with_the_video_link(outbox, cards):
    post_comment()
    assert outbox["private"] == []                       # düz metin gönderilmedi
    cid, payload = cards[0]
    el = payload["attachment"]["payload"]["elements"][0]
    assert payload["attachment"]["payload"]["template_type"] == "generic"
    assert el["title"] == "Örgü yemek videosu promptları"
    assert el["default_action"]["url"] == el["buttons"][0]["url"] == f"{BASE}/orgu-yemek"
    assert el["buttons"][0]["title"] == "Bağlantıyı aç" and el["image_url"].endswith("/brand/og.png")
    assert ig.RECENT_LOGS[-1]["dm_kind"] == "card" and ig.RECENT_LOGS[-1]["dm_ok"]


def test_card_failure_falls_back_to_text(outbox, monkeypatch):
    async def failing(cid, payload):
        return False
    monkeypatch.setattr(ig, "send_private_reply_card", failing)
    post_comment()
    assert f"{BASE}/orgu-yemek" in outbox["private"][0][1] and ig.RECENT_LOGS[-1]["dm_kind"] == "text"


def test_text_format_setting_skips_card(outbox, cards):
    client.post("/api/settings", headers=PIN, json={"public_reply": "ok", "dm_template": "Link: {link}", "dm_format": "text"})
    post_comment()
    assert cards == [] and outbox["private"][0][1] == f"Link: {BASE}/orgu-yemek"


def test_card_uses_custom_texts_and_limits(outbox, cards):
    client.post("/api/settings", headers=PIN, json={"public_reply": "ok", "dm_template": "x {link}",
                                                    "card_subtitle": "Promptlar hazır ✨", "card_button": "Aç"})
    client.put("/api/pages/18121864655327295", headers=PIN, json={"title": "Ö" * 120, "slug": "orgu-yemek"})
    post_comment()
    el = cards[0][1]["attachment"]["payload"]["elements"][0]
    assert el["subtitle"] == "Promptlar hazır ✨" and el["buttons"][0]["title"] == "Aç" and len(el["title"]) <= 80


def test_card_text_strips_links():
    assert ig.card_text("Merhaba! İşte: {link}  https://x.com/a", 80) == "Merhaba! İşte:"
    assert ig.card_text("a" * 100, 20).endswith("…") and len(ig.card_text("a" * 100, 20)) == 20
