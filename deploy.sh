#!/usr/bin/env bash
# ==============================================================================
# Google Cloud Run Tek Komutla Dağıtım Betiği (ÜCRETSİZ TIER / KREDİ İLE ÇALIŞIR)
# ==============================================================================
set -e

SERVICE_NAME="instagram-ai-agent"
REGION="europe-west1" # veya us-central1 (Always Free için)

echo "=================================================="
echo "🚀 Instagram AI Ajanı Google Cloud Run'a Yükleniyor"
echo "=================================================="

# 1. Proje kimliğini kontrol et
PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
echo "[*] Aktif Google Cloud Projeniz: $PROJECT_ID"

# 2. Gerekli servisleri etkinleştir
echo "[*] Gerekli Google Cloud API'leri etkinleştiriliyor..."
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com --quiet

# 3. Cloud Run'a dağıt
echo "[*] Cloud Run servisi inşa ediliyor ve yayınlanıyor..."
gcloud run deploy "$SERVICE_NAME" \
    --source . \
    --region "$REGION" \
    --platform managed \
    --allow-unauthenticated \
    --memory 512Mi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 2 \
    --update-env-vars "PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-https://link.likyastudios.live}"

echo "=================================================="
echo "✅ Dağıtım tamamlandı!"
echo "Yukarıda verilen URL'in sonuna '/webhook' ekleyerek Meta Developer paneline kaydedin."
echo "Örnek: https://instagram-ai-agent-xxxx-ew.a.run.app/webhook"
echo "=================================================="
