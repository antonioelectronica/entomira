"""Configuración del Worker de IA (lee variables de entorno / .env)."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    # .env local del worker o el compartido en secrets/
    for cand in (Path(__file__).resolve().parents[2] / "secrets" / ".env",
                 Path(__file__).resolve().parents[1] / ".env"):
        if cand.exists():
            load_dotenv(cand)
            break
except Exception:
    pass


def _get(key: str, default: str) -> str:
    return os.environ.get(key, default)


# URL del servidor (Tailscale con fallback a IP local); ver Docs_Obsidian/02_Red_y_Accesos.md
SERVER_BASE_URL = _get("SERVER_BASE_URL", "http://127.0.0.1:8000")
SERVER_FALLBACK_URL = _get("SERVER_FALLBACK_URL", "http://127.0.0.1:8000")
WORKER_API_KEY = _get("WORKER_API_KEY", "cambia-esta-clave-del-worker")

MODEL_PATH = _get("WORKER_MODEL_PATH", "models/best.pt")
DEVICE = _get("WORKER_DEVICE", "0")          # GPU 0
POLL_SECONDS = int(_get("WORKER_POLL_SECONDS", "10"))

API_V1 = "/api/v1"
