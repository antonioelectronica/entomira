"""Cliente HTTP del Worker hacia el servidor ENTOMIRA (con fallback y reconexión)."""
from __future__ import annotations

import logging
from typing import Any

import requests

from . import config

log = logging.getLogger("entomira.worker.client")


class ServerClient:
    """Habla con la API del servidor; prueba Tailscale y cae a la IP local."""

    def __init__(self) -> None:
        self._bases = []
        for url in (config.SERVER_BASE_URL, config.SERVER_FALLBACK_URL):
            if url and url not in self._bases:
                self._bases.append(url.rstrip("/"))
        self.active_base: str | None = None
        self._headers = {"X-API-Key": config.WORKER_API_KEY}

    # -- conexión ---------------------------------------------------------
    def _try(self, method: str, path: str, **kw) -> requests.Response | None:
        bases = ([self.active_base] if self.active_base else []) + [
            b for b in self._bases if b != self.active_base
        ]
        for base in bases:
            try:
                r = requests.request(method, f"{base}{config.API_V1}{path}",
                                     headers=self._headers, timeout=kw.pop("timeout", 15),
                                     **kw)
                self.active_base = base
                return r
            except requests.RequestException:
                continue
        self.active_base = None
        return None

    def ping(self) -> bool:
        r = self._try("GET", "/health", timeout=5)
        return bool(r and r.ok)

    # -- inferencia -------------------------------------------------------
    def pending_images(self) -> list[dict[str, Any]]:
        r = self._try("GET", "/inference/pending")
        if r and r.ok:
            return r.json()
        return []

    def download_image(self, image_id: int, dest: str) -> bool:
        r = self._try("GET", f"/images/{image_id}/file")
        if r and r.ok:
            with open(dest, "wb") as f:
                f.write(r.content)
            return True
        return False

    def submit_result(self, image_id: int, result: dict[str, Any]) -> bool:
        r = self._try("PUT", f"/inference/{image_id}", json=result)
        return bool(r and r.ok)

    def marcar_ilegible(self, image_id: int) -> bool:
        """Le dice al servidor que esta imagen no se puede leer, para que salga de la cola.

        Si esto falla (servidor caido, por ejemplo) se devuelve False y el worker la sigue
        saltando en local: peor, pero no bloquea el lote de esta sesion.
        """
        r = self._try("PUT", f"/inference/{image_id}/ilegible")
        return bool(r and r.ok)

    def submit_demo(self, result: dict[str, Any]) -> bool:
        r = self._try("POST", "/inference/demo", json=result)
        return bool(r and r.ok)

    def push_stats(self, stats: dict[str, Any]) -> bool:
        r = self._try("POST", "/worker/stats", json=stats, timeout=8)
        return bool(r and r.ok)

    def get_params(self) -> dict[str, Any]:
        r = self._try("GET", "/worker/params", timeout=8)
        if r and r.ok:
            return r.json()
        return {}
