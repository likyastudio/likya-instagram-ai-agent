import hashlib
import hmac
import json
import pytest
from fastapi.testclient import TestClient

import app as ig_module
from app import app, valid_signature, find_matching_rule


client = TestClient(app)


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
    res = client.get("/api/rules")
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
    res_post = client.post("/api/rules", json=new_rules)
    assert res_post.status_code == 200
    assert res_post.json()["count"] == 1


def test_find_matching_rule():
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
