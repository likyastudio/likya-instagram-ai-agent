# Likya Studios — Instagram Otomasyonu ve Paneli

`@likyastudios` için Meta Graph API üzerinde çalışan yorum ve DM otomasyonu. Google Cloud Run'da çalışır.

## Nasıl çalışır?

1. Bir gönderiye yorum gelir (Meta webhook → `/webhook`).
2. Yoruma her zaman **standart yanıt** yazılır (panel → Mesaj ayarları).
3. Yorum yapan kişiye **DM** gider: DM metni + o videonun sayfa linki (`link.likyastudios.live/<sayfa>`).
   Sayfası olmayan videolarda genel kaynak sayfasının linki gider.
4. Doğrudan gelen DM'leri Gemini yanıtlar (anahtar yoksa genel sayfa linki gönderilir).

## Adresler

| Adres | Ne |
|---|---|
| `panel.likyastudios.live` | Yönetim paneli — yalnızca `ADMIN_EMAILS` içindeki Google hesapları |
| `link.likyastudios.live` | Takipçilerin gördüğü video sayfaları (promptlar Kopyala düğmeli, linkler, adımlar, ipucu) |
| `<servis>/p/<sayfa>` | Aynı sayfalar, alan adı bağlanmadan önce |
| `<servis>/webhook` | Meta webhook adresi |

## Panel

- **Gönderiler:** Instagram'daki gönderiler kendiliğinden listelenir. Her videoya sayfa oluşturulur/düzenlenir.
  Hazır video JSON dosyası (`{slug, title, excerpt, blocks:[h2|p|prompt|link|steps|tip]}`) yapıştırılarak form doldurulabilir.
  "Harici sayfa adresi" doldurulursa DM bu adresi gönderir.
- **Hareketler:** Son 50 yorum/DM ve gönderim durumu (bellekte; sunucu yeniden başlayınca sıfırlanır).
- **Mesaj ayarları:** Standart yorum yanıtı, DM metni (`{link}`, `{user}`), genel sayfa, yapay zekâ talimatı.

## Veri

Ayarlar ve sayfalar `DATA_BUCKET` kovasında `instagram-agent/data.json` olarak kalıcı tutulur (`store.py`).
Kova boşsa ilk açılışta `seed-data.json` kullanılır. `DATA_BUCKET` tanımsızsa yerel `data.local.json` (yalnızca geliştirme).

## Dosyalar

```text
app.py            FastAPI: webhook, yönetim API'leri, herkese açık sayfalar
auth.py           Google girişi + imzalı oturum çerezi
store.py          Cloud Storage / yerel JSON deposu
public_pages.py   Takipçi sayfalarının HTML'i
static/dashboard.html  Panel (bağımlılıksız)
seed-data.json    İlk ayarlar ve sayfalar
tests/            pytest
```

## Yerelde çalıştırma ve test

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app:app --reload --port 8080
.venv/Scripts/python -m pytest -q
```

## Dağıtım

Ortam değişkenleri Cloud Run'da tutulur; `--env-vars-file` kullanmayın (mevcut değerleri siler).

```bash
gcloud run deploy instagram-ai-agent --source . --project app-map-509800 --region europe-west1
```
