# Likya Studios — Instagram Otomasyon & AI Yönetim Paneli (PRO)

`@likyastudios` için geliştirilmiş, üçüncü parti filigranı (ManyChat vb. reklamları) olmadan doğrudan **Meta Graph API** ve **Google Gemini AI** üzerinde çalışan 7/24 bulut tabanlı Instagram Yorum & DM Otomasyon Sistemi.

## ✨ Özellikler

- **Yönetim Paneli (`/`):** Tailwind CSS tabanlı modern web arayüzü (PIN şifre korumalı).
- **Canlı Reels / Gönderi Seçici (`/api/media`):** Hesaba yüklenen son videoları otomatik olarak Graph API'den çeker ve açılır listede gösterir.
- **Anti-Spam Çoklu Varyasyon Desteği:** Yorum yanıtlarında `|` ile ayrılmış birden fazla cümle arasından rastgele seçim yapar.
- **Videoya Özel veya Genel Kurallar (`rules.json`):** Belirli bir `media_id` veya tüm gönderiler (`all`) için anahtar kelime tetikleyicileri.
- **Filigransız Özel Yanıt (Private Reply):** Yorum yapan kullanıcının DM kutusuna doğrudan `recipient: {"comment_id": ...}` ile özel mesaj ve link gönderir.
- **Google Gemini AI Asistanı (`gemini-3.5-flash-lite`):** Kurallarla eşleşmeyen yorumlara ve DM sohbetlerine `system-prompt.txt` talimatlarına göre doğal Türkçe yanıt üretir.

---

## 📁 Proje Yapısı

```text
├── app.py                  # FastAPI sunucusu, Web Yönetim Paneli (HTML/JS), Meta & Gemini entegrasyonu
├── rules.json              # Otomasyon kuralları (Video ID, anahtar kelimeler, yorum ve DM şablonları)
├── system-prompt.txt       # Gemini AI sistem kişiliği ve kuralları
├── requirements.txt        # Python bağımlılıkları
├── Dockerfile              # Google Cloud Run konteyner yapılandırması
├── deploy.sh               # Cloud Run tek tıkla dağıtım betiği
├── .env.example            # Ortam değişkenleri şablonu
└── tests/                  # Pytest birim ve entegrasyon testleri
```

---

## 🚀 Geliştirme & Tasarım Özelleştirme (Claude Code İpuçları)

Web yönetim paneli arayüzü `app.py` içerisindeki `dashboard_page()` fonksiyonunda (`@app.get("/")`) yer almaktadır. Ayrı bir `templates/index.html` veya React/Next.js yapısına taşımak isterseniz şu API uç noktalarını kullanabilirsiniz:

- `GET /api/media` → Son Instagram gönderilerini ve Reels videolarını döndürür.
- `GET /api/rules` → Mevcut otomasyon kurallarını listeler.
- `POST /api/rules` → Kuralları günceller (`X-Admin-Pin` başlığı ile).
- `GET /api/logs` → Son gelen yorum ve DM hareket kayıtlarını döndürür.
- `POST /api/prompt` → Gemini AI sistem talimatını günceller (`X-Admin-Pin` başlığı ile).
- `GET / POST /webhook` → Meta Developer Webhook doğrulama ve olay dinleme uç noktası.

### Yerelde Çalıştırma

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8080
```

### Testleri Çalıştırma

```bash
pytest tests/ -v
```

### Google Cloud Run'a Yükleme (Deploy)

```bash
gcloud run deploy instagram-ai-agent \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 2 \
  --env-vars-file .env.yaml
```
