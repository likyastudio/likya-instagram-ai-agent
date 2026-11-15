import hashlib
import hmac
import json
import pytest
from fastapi.testclient import TestClient

import app as ig_module
from app import app, valid_signature, find_matching_rule


client = TestClient(app)
PIN = {"X-Admin-Pin": ig_module.ADMIN_PIN}


@pytest.fixture(autouse=True)
def isolated_files(tmp_path, monkeypatch):
    """Testler depodaki gerçek rules.json / system-prompt.txt dosyalarının üzerine yazmasın."""
    rules_copy = tmp_path / "rules.json"
    rules_copy.write_text(ig_module.RULES_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    prompt_copy = tmp_path / "system-prompt.txt"
    prompt_copy.write_text(ig_module.PROMPT_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(ig_module, "RULES_FILE", rules_copy)
    monkeypatch.setattr(ig_module, "PROMPT_FILE", prompt_copy)
    ig_module.PIN_FAILURES.clear()
    yield


def test_dashboard_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    assert "Likya Studios" in response.text
    assert "text/html" in response.headers["content-type"]


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_webhook_verify_success():
    token = ig_module.IG_VERIFY_TOKEN
    response = client.get(f"/webhook?hub.mode=subscribe&hub.verify_token={token}&hub.challenge=ig_challenge_777")
    assert response.status_code == 200
    assert response.text == "ig_challenge_777"


def test_webhook_verify_invalid_token():
    response = client.get("/webhook?hub.mode=subscribe&hub.verify_token=wrong_token&hub.challenge=test")
    assert response.status_code == 403


def test_signature_validation():
    secret = "test_app_secret_12345"
    ig_module.IG_APP_SECRET = secret
    body = b'{"object": "instagram"}'

    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    valid_header = f"sha256={expected}"

    assert valid_signature(body, valid_header) is True
    assert valid_signature(body, "sha256=invalidhash") is False


def test_rules_api():
    # GET rules
    res = client.get("/api/rules", headers=PIN)
    assert res.status_code == 200
    rules = res.json()
    assert isinstance(rules, list)

    # POST new rule
    new_rules = [
        {
            "id": "test_rule_1",
            "name": "Özel Test Kuralı",
            "media_id": "all",
            "keywords": "katalog, test",
            "public_reply": "Katalog DM'de!",
            "dm_message": "İşte kataloğunuz: https://likyastudios.com/katalog",
            "is_active": True,
        }
    ]
    res_post = client.post("/api/rules", json=new_rules, headers=PIN)
    assert res_post.status_code == 200
    assert res_post.json()["count"] == 1


def test_find_matching_rule():
    client.post("/api/rules", headers=PIN, json=[{
        "id": "test_rule_1", "name": "Özel Test Kuralı", "media_id": "all", "keywords": "katalog, test",
        "public_reply": "Katalog DM'de!", "dm_message": "İşte kataloğunuz: https://likyastudios.com/katalog",
        "is_active": True,
    }])
    matched = find_matching_rule("bana katalog atabilir misiniz?")
    assert matched is not None
    assert matched["name"] == "Özel Test Kuralı"
    assert "https://likyastudios.com/katalog" in matched["dm_message"]

    unmatched = find_matching_rule("alakasız bir yorum")
    assert unmatched is None


def test_webhook_receive_instagram_dm(monkeypatch):
    handled_dms = []

    async def mock_handle_dm(sender_id, text):
        handled_dms.append((sender_id, text))

    monkeypatch.setattr(ig_module, "handle_dm", mock_handle_dm)

    secret = "test_app_secret_12345"
    ig_module.IG_APP_SECRET = secret

    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": "17841400000000000",
                "time": 1700000000,
                "messaging": [
                    {
                        "sender": {"id": "ig_sender_555"},
                        "recipient": {"id": "page_id"},
                        "message": {"mid": "mid.DM_TEST_1", "text": "Fiyat nedir?"},
                    }
                ],
            }
        ],
    }
    raw = json.dumps(payload).encode()
    sig = f"sha256={hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}"

    res = client.post("/webhook", content=raw, headers={"X-Hub-Signature-256": sig})
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    assert len(handled_dms) == 1
    assert handled_dms[0] == ("ig_sender_555", "Fiyat nedir?")


def test_webhook_receive_instagram_comment(monkeypatch):
    handled_comments = []

    async def mock_handle_comment(comment_id, text, username, media_id=""):
        handled_comments.append((comment_id, text, username, media_id))

    monkeypatch.setattr(ig_module, "handle_comment", mock_handle_comment)

    secret = "test_app_secret_12345"
    ig_module.IG_APP_SECRET = secret

    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": "17841400000000000",
                "time": 1700000000,
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": "comment_999",
                            "text": "Çok güzel bir ürün!",
                            "from": {"id": "user_11", "username": "ayse_yilmaz"},
                            "media": {"id": "media_post_111"},
                        },
                    }
                ],
            }
        ],
    }
    raw = json.dumps(payload).encode()
    sig = f"sha256={hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}"

    res = client.post("/webhook", content=raw, headers={"X-Hub-Signature-256": sig})
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    assert len(handled_comments) == 1
    assert handled_comments[0] == ("comment_999", "Çok güzel bir ürün!", "ayse_yilmaz", "media_post_111")


# ---------------------------------------------------------------- Yönetim API güvenliği

@pytest.mark.parametrize("method,path", [
    ("get", "/api/rules"), ("get", "/api/logs"), ("get", "/api/media"), ("get", "/api/status"),
    ("get", "/api/prompt"), ("post", "/api/rules"), ("post", "/api/prompt"), ("post", "/api/test-match"),
])
def test_admin_api_requires_pin(method, path):
    kwargs = {"json": {}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401
    bad = getattr(client, method)(path, headers={"X-Admin-Pin": "yanlis"}, **kwargs)
    assert bad.status_code == 401


def test_rules_cannot_be_overwritten_without_pin():
    before = client.get("/api/rules", headers=PIN).json()
    res = client.post("/api/rules", json=[])
    assert res.status_code == 401
    assert client.get("/api/rules", headers=PIN).json() == before


def test_pin_bruteforce_is_rate_limited():
    for _ in range(ig_module.PIN_FAIL_LIMIT):
        assert client.get("/api/status", headers={"X-Admin-Pin": "x"}).status_code == 401
    assert client.get("/api/status", headers={"X-Admin-Pin": "x"}).status_code == 429
    assert client.get("/api/status", headers=PIN).status_code == 429


def test_dashboard_does_not_embed_private_data():
    ig_module.RECENT_LOGS.append({"type": "DM", "user": "gizli_kullanici", "input": "<img src=x onerror=alert(1)>",
                                  "reply": "x", "matched_rule": "x"})
    try:
        html = client.get("/").text
        assert "gizli_kullanici" not in html
        assert "onerror=alert(1)" not in html
        assert "likya2026" not in html
        assert "cdn.tailwindcss.com" not in html
    finally:
        ig_module.RECENT_LOGS.pop()


def test_status_reports_config_without_secrets():
    data = client.get("/api/status", headers=PIN).json()
    assert set(data) >= {"instagram", "webhook", "ai", "security", "rules", "logs"}
    flat = json.dumps(data)
    for secret in (ig_module.IG_PAGE_ACCESS_TOKEN, ig_module.GEMINI_API_KEY, ig_module.IG_APP_SECRET, ig_module.ADMIN_PIN):
        if secret:
            assert secret not in flat


def test_test_match_has_no_side_effects(monkeypatch):
    calls = []
    monkeypatch.setattr(ig_module, "reply_to_comment", lambda *a: calls.append(a))
    res = client.post("/api/test-match", headers=PIN, json={"text": "pdf linki lütfen", "media_id": ""})
    assert res.status_code == 200
    assert res.json()["matched"] is True
    assert calls == []
    miss = client.post("/api/test-match", headers=PIN, json={"text": "merhaba", "media_id": ""}).json()
    assert miss["matched"] is False


def test_rules_payload_is_validated():
    assert client.post("/api/rules", headers=PIN, json={"rules": "bozuk"}).status_code == 422
    res = client.post("/api/rules", headers=PIN, json=[{"name": "  Yalın  "}])
    assert res.status_code == 200
    saved = client.get("/api/rules", headers=PIN).json()[0]
    assert saved["name"] == "Yalın" and saved["media_id"] == "all" and saved["keywords"] == "*" and saved["is_active"] is True


def test_prompt_roundtrip_and_empty_rejected():
    assert client.post("/api/prompt", headers=PIN, json={"prompt": "   "}).status_code == 422
    assert client.post("/api/prompt", headers=PIN, json={"prompt": "Yeni talimat"}).status_code == 200
    assert client.get("/api/prompt", headers=PIN).json()["prompt"] == "Yeni talimat"


def test_webhook_rejected_signature_is_counted():
    ig_module.IG_APP_SECRET = "test_app_secret_12345"
    before = ig_module.STATS["webhook_rejected"]
    res = client.post("/webhook", content=b"{}", headers={"X-Hub-Signature-256": "sha256=bad"})
    assert res.status_code == 401
    assert ig_module.STATS["webhook_rejected"] == before + 1
