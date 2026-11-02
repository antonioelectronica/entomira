"""
Entorno de pruebas del servidor ENTOMIRA.

Todo apunta a un directorio temporal: **ninguna prueba toca la base de datos ni las imágenes
reales**. Es importante porque durante el desarrollo ya ocurrió lo contrario — una prueba
manual borró capturas de verdad — y esa es justamente una de las razones de que exista este
fichero.

La configuración del servidor se lee de variables de entorno (`core/config.py`), así que
basta con fijarlas ANTES de importar la aplicación.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# --- entorno aislado, antes de importar nada de la app -------------------------------
_TMP = Path(tempfile.mkdtemp(prefix="entomira_tests_"))
os.environ["ENTOMIRA_DATA_DIR"] = str(_TMP)
os.environ["IMAGES_DIR"] = str(_TMP / "images")
os.environ["ENTOMIRA_DB"] = str(_TMP / "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["DEVICE_API_KEY"] = "clave-dispositivo-de-prueba"
os.environ["WORKER_API_KEY"] = "clave-worker-de-prueba"
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASS"] = "secreto"          # con contraseña: se ejercita la autenticación

from fastapi.testclient import TestClient      # noqa: E402
from app.core.db import init_db                # noqa: E402
from app.main import app                       # noqa: E402

DEVICE_KEY = os.environ["DEVICE_API_KEY"]
ADMIN = ("admin", "secreto")


@pytest.fixture(scope="session", autouse=True)
def _bd():
    init_db()
    yield


@pytest.fixture()
def cliente():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def jpeg() -> bytes:
    """Un JPEG mínimo pero válido: el servidor lo abre con Pillow, no vale un byte suelto."""
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 200, 120)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture()
def trampa(cliente, jpeg):
    """Una trampa registrada por la vía real: subiendo una imagen (auto-registro)."""
    r = cliente.post("/api/v1/ingest",
                     headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("cap.jpg", jpeg, "image/jpeg")},
                     data={"device_id": "esp32-PRUEBA01"})
    assert r.status_code == 201, r.text
    return r.json()["trap_id"]
