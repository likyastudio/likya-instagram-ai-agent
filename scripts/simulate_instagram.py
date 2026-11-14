"""
Instagram Yerel Test Betiği:
DM ve Gönderi Yorumlarını Sanal Olarak Ajan Sunucusuna Gönderir.
Kullanım:
  python scripts/simulate_instagram.py dm "Merhaba, ürün fiyatı nedir?"
  python scripts/simulate_instagram.py comment "Fiyat öğrenebilir miyim?"
"""
import hashlib
import hmac
import json
import os
import sys
import httpx
from dotenv import load_dotenv

load_dotenv()

SERVER_URL = os.getenv("TEST_SERVER_URL", "http://127.0.0.1:8000")
SECRET = os.getenv("IG_APP_SECRET", "")


def send_payload(payload: dict):
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if SECRET:
        sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = f"sha256={sig}"

    try:
        r = httpx.post(f"{SERVER_URL}/webhook", content=raw, headers=headers, timeout=10)
        print(f"[*] Yanıt Kodu: {r.status_code}")
        print(f"[*] Yanıt Gövdesi: {r.text}")
    except Exception as e:
        print(f"[!] Hata: {e}")


def simulate_dm(text: str):
    print(f"[*] Sanal Instagram DM gönderiliyor: '{text}'")
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": "17841400000000000",
                "time": 1700000000,
                "messaging": [
                    {
                        "sender": {"id": "ig_user_12345"},
                        "recipient": {"id": "my_business_page"},
                        "timestamp": 1700000000,
                        "message": {
                            "mid": f"mid.IG_DM_{os.urandom(4).hex()}",
                            "text": text,
                        },
                    }
                ],
            }
        ],
    }
    send_payload(payload)


def simulate_comment(text: str, username: str = "ornek_kullanici"):
    print(f"[*] Sanal Instagram Gönderi Yorumu gönderiliyor (@{username}): '{text}'")
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
                            "id": f"comment_{os.urandom(4).hex()}",
                            "text": text,
                            "from": {"id": "user_999", "username": username},
                            "media": {"id": "media_post_111"},
                        },
                    }
                ],
            }
        ],
    }
    send_payload(payload)


if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "dm"
    content = sys.argv[2] if len(sys.argv) > 2 else "Merhaba, detaylı bilgi alabilir miyim?"

    if mode == "comment":
        simulate_comment(content)
    else:
        simulate_dm(content)
