"""Kalıcı veri deposu (ayarlar + video sayfaları).

Cloud Run'ın diski geçicidir; panelden yapılan değişiklikler sunucu yeniden başlayınca
kaybolur. Bu yüzden `DATA_BUCKET` tanımlıysa veri Google Cloud Storage'daki tek bir
JSON dosyasında tutulur. Kimlik, Cloud Run'ın servis hesabından (metadata sunucusu)
alınır; ek kütüphane gerekmez. `DATA_BUCKET` boşsa (yerel geliştirme, testler) yerel
dosya kullanılır.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from pathlib import Path
from urllib.parse import quote

import httpx

logger = logging.getLogger("instagram-agent.store")

METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)


class DataStore:
    def __init__(self, bucket: str, object_name: str, local_path: Path, seed: dict, cache_ttl: float = 10.0):
        self.bucket = bucket.strip()
        self.object_name = object_name
        self.local_path = local_path
        self.seed = seed
        self.cache_ttl = cache_ttl
        self._cache: dict | None = None
        self._cache_at = 0.0
        self._token: str = ""
        self._token_exp = 0.0
        self._lock = asyncio.Lock()
        self.last_error = ""
        self.last_ok_at: float | None = None

    @property
    def backend(self) -> str:
        return "gcs" if self.bucket else "local"

    # ------------------------------------------------------------ GCS yardımcıları
    async def _gcs_token(self, client: httpx.AsyncClient) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        r = await client.get(METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
        r.raise_for_status()
        data = r.json()
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 300))
        return self._token

    async def _gcs_read(self) -> dict | None:
        url = (f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/"
               f"{quote(self.object_name, safe='')}?alt=media")
        async with httpx.AsyncClient(timeout=10) as client:
            token = await self._gcs_token(client)
            r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()

    async def _gcs_write(self, data: dict) -> None:
        url = (f"https://storage.googleapis.com/upload/storage/v1/b/{self.bucket}/o"
               f"?uploadType=media&name={quote(self.object_name, safe='')}")
        body = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        async with httpx.AsyncClient(timeout=15) as client:
            token = await self._gcs_token(client)
            r = await client.post(url, content=body, headers={
                "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})
            r.raise_for_status()

    # ------------------------------------------------------------ Genel arayüz
    def _merge_seed(self, data: dict | None) -> dict:
        """Eksik alanları başlangıç verisiyle tamamlar (yeni ayar eklenince eski dosya bozulmaz)."""
        out = copy.deepcopy(self.seed)
        if isinstance(data, dict):
            out["settings"].update({k: v for k, v in (data.get("settings") or {}).items()})
            if isinstance(data.get("pages"), dict):
                out["pages"] = data["pages"]
        return out

    async def load(self, force: bool = False) -> dict:
        if not force and self._cache is not None and time.monotonic() - self._cache_at < self.cache_ttl:
            return copy.deepcopy(self._cache)
        raw: dict | None = None
        try:
            if self.bucket:
                raw = await self._gcs_read()
            elif self.local_path.exists():
                raw = json.loads(self.local_path.read_text(encoding="utf-8"))
            self.last_error = ""
            self.last_ok_at = time.time()
        except Exception as e:  # depo okunamazsa son bilinen veriyle devam et
            self.last_error = f"Okuma hatası: {e}"[:300]
            logger.error(self.last_error)
            if self._cache is not None:
                return copy.deepcopy(self._cache)
            # Hiç okunamadıysa başlangıç verisi gösterilir ama üzerine yazılmasına izin verilmez
            self._unreadable = True
            return self._merge_seed(None)
        self._unreadable = False
        data = self._merge_seed(raw)
        self._cache, self._cache_at = data, time.monotonic()
        return copy.deepcopy(data)

    async def save(self, data: dict) -> None:
        if getattr(self, "_unreadable", False):
            await self.load(force=True)
            if self._unreadable:
                raise RuntimeError("Kalıcı depo okunamadı; mevcut verinin üzerine yazmamak için kayıt durduruldu.")
        async with self._lock:
            if self.bucket:
                try:
                    await self._gcs_write(data)
                except Exception as e:
                    self.last_error = f"Yazma hatası: {e}"[:300]
                    logger.error(self.last_error)
                    raise
            else:
                self.local_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            self.last_error = ""
            self.last_ok_at = time.time()
            self._cache, self._cache_at = copy.deepcopy(data), time.monotonic()
