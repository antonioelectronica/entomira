"""Recorte de la tarjeta: original intacto, /file sirve el recorte (lo que ve la IA), /original el JPEG."""
from __future__ import annotations

import io

from PIL import Image

from conftest import ADMIN, DEVICE_KEY

CAL = {"res": [400, 300], "quad": [[100, 10], [280, 10], [290, 270], [90, 268]]}


def _jpeg(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (90, 140, 200)).save(buf, "JPEG", quality=95)
    return buf.getvalue()


def _subir(cliente, dev, datos):
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("c.jpg", datos, "image/jpeg")}, data={"device_id": dev})
    assert r.status_code == 201, r.text
    return r.json()


def test_con_calibracion_file_sirve_el_recorte_y_original_el_jpeg(cliente):
    dev = "zero-RECORTE1"
    original = _jpeg(400, 300)
    tid = _subir(cliente, dev, original)["trap_id"]
    r = cliente.put(f"/api/v1/traps/{tid}/recorte", auth=ADMIN, json=CAL)
    assert r.status_code == 200 and r.json()["version"]

    iid = _subir(cliente, dev, original)["image_id"]
    rec = Image.open(io.BytesIO(cliente.get(f"/api/v1/images/{iid}/file", auth=ADMIN).content))
    assert rec.size == (190, 259)                       # el recorte enderezado
    assert cliente.get(f"/api/v1/images/{iid}/original", auth=ADMIN).content == original   # byte a byte
    assert cliente.get(f"/api/v1/images/{iid}", auth=ADMIN).json()["recortada"] is True
    miniatura = Image.open(io.BytesIO(cliente.get(f"/api/v1/images/{iid}/thumb", auth=ADMIN).content))
    assert miniatura.size[0] < miniatura.size[1]        # la miniatura sale del recorte (vertical)


def test_resolucion_distinta_entra_sin_recorte(cliente):
    dev = "zero-RECORTE2"
    tid = _subir(cliente, dev, _jpeg(400, 300))["trap_id"]
    assert cliente.put(f"/api/v1/traps/{tid}/recorte", auth=ADMIN, json=CAL).status_code == 200
    iid = _subir(cliente, dev, _jpeg(320, 240))["image_id"]
    img = Image.open(io.BytesIO(cliente.get(f"/api/v1/images/{iid}/file", auth=ADMIN).content))
    assert img.size == (320, 240)
    assert cliente.get(f"/api/v1/images/{iid}", auth=ADMIN).json()["recortada"] is False


def test_calibracion_invalida_se_rechaza_y_se_puede_quitar(cliente):
    dev = "zero-RECORTE3"
    tid = _subir(cliente, dev, _jpeg(400, 300))["trap_id"]
    malo = {"res": [400, 300], "quad": [[0, 0], [500, 0], [400, 300], [0, 300]]}
    assert cliente.put(f"/api/v1/traps/{tid}/recorte", auth=ADMIN, json=malo).status_code == 422
    assert cliente.put(f"/api/v1/traps/{tid}/recorte", json=CAL).status_code == 401
    assert cliente.put(f"/api/v1/traps/{tid}/recorte", auth=ADMIN, json={"recorte": None}).json()["recorte"] is None


def test_borrar_la_imagen_borra_tambien_el_recorte(cliente):
    from pathlib import Path

    from sqlmodel import Session

    from app.core.db import engine
    from app.models.models import Imagen
    dev = "zero-RECORTE4"
    tid = _subir(cliente, dev, _jpeg(400, 300))["trap_id"]
    cliente.put(f"/api/v1/traps/{tid}/recorte", auth=ADMIN, json=CAL)
    iid = _subir(cliente, dev, _jpeg(400, 300))["image_id"]
    with Session(engine) as s:
        ruta = s.get(Imagen, iid).ruta_recorte
    assert ruta and Path(ruta).is_file()
    assert cliente.delete(f"/api/v1/traps/{tid}", auth=ADMIN).status_code in (200, 204)
    assert not Path(ruta).exists()
