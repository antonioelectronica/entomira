"""Configuración del servidor (variables de entorno / .env)."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    for cand in (Path("/home/entomira/entomira/secrets/.env"),
                 Path(__file__).resolve().parents[3] / "secrets" / ".env",
                 Path(__file__).resolve().parents[2] / ".env"):
        if cand.exists():
            load_dotenv(cand)
            break
except Exception:
    pass


def _get(k: str, d: str) -> str:
    return os.environ.get(k, d)


# Directorio de datos (BD + imágenes). En la Pi: /home/entomira/entomira/data
DATA_DIR = Path(_get("ENTOMIRA_DATA_DIR",
                     str(Path(__file__).resolve().parents[2] / "data")))
IMAGES_DIR = Path(_get("IMAGES_DIR", str(DATA_DIR / "images")))
THUMBS_DIR = IMAGES_DIR / "thumbs"
DB_PATH = Path(_get("ENTOMIRA_DB", str(DATA_DIR / "entomira.db")))

DATABASE_URL = _get("DATABASE_URL", f"sqlite:///{DB_PATH}")

DEVICE_API_KEY = _get("DEVICE_API_KEY", "cambia-esta-clave-de-dispositivos")
WORKER_API_KEY = _get("WORKER_API_KEY", "cambia-esta-clave-del-worker")

# Acceso al panel web (Basic Auth). Si ADMIN_PASS está vacío, el login se desactiva
# (útil en local). En producción (Funnel público) DEBE estar puesto.
ADMIN_USER = _get("ADMIN_USER", "entomira")
ADMIN_PASS = _get("ADMIN_PASS", "")

# Modo local (desarrollo en un PC): SOLO con esta variable a 1 se permite arrancar sin claves,
# con admin anónimo y con /docs. Sin ella, el servidor está en internet (entomira.com) y un
# `.env` que no carga no puede dejar la API abierta: no arranca.
MODO_LOCAL = _get("ENTOMIRA_MODO_LOCAL", "") == "1"
# Subredes donde puede haber cámaras con relay directo (ESP32 en la LAN). El servidor solo
# contacta IPs de aquí, y nunca la primera dirección de cada una (la puerta de enlace).
REDES_CAMARAS = [r.strip() for r in _get("ENTOMIRA_REDES_CAMARAS", "192.168.1.0/24").split(",")
                 if r.strip()]
# Orígenes que pueden llamar a la API desde otro dominio (la web se sirve desde el mismo).
ORIGENES = [o.strip() for o in _get("ENTOMIRA_ORIGENES", "http://localhost:8000").split(",")
            if o.strip()]


def claves_inseguras() -> list[str]:
    """Nombres de las claves que faltan o conservan el valor de ejemplo."""
    malas = []
    if not ADMIN_PASS.strip():
        malas.append("ADMIN_PASS")
    if not DEVICE_API_KEY.strip() or DEVICE_API_KEY == "cambia-esta-clave-de-dispositivos":
        malas.append("DEVICE_API_KEY")
    if not WORKER_API_KEY.strip() or WORKER_API_KEY == "cambia-esta-clave-del-worker":
        malas.append("WORKER_API_KEY")
    return malas

SERVER_HOST = _get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(_get("SERVER_PORT", "8000"))

# Sectores fijos de cada invernadero (orden de dibujo del mapa de calor).
SECTORES = ["A", "B", "pasillo", "C", "D"]

# Umbral (segundos) para considerar una trampa "online" desde su último visto.
ONLINE_GRACE = int(_get("ONLINE_GRACE", "180"))

for _d in (DATA_DIR, IMAGES_DIR, THUMBS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
