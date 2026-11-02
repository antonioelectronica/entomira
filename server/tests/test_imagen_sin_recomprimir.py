"""La foto se guarda tal cual llega; solo se recodifica si hay que corregirle el color de verdad.

Hasta el 2026-09-26 el servidor recomprimía toda foto a calidad 90 y tiraba en silencio unas
tres cuartas partes de lo que la trampa capturaba a calidad máxima (hallazgo N1).
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image

from app.services import images as images_svc


def _jpeg(w=1600, h=1200, calidad=100, exif=None) -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", (w, h), (120, 180, 90))
    for x in range(0, w, 37):                     # algo de detalle, para que calidad importe
        im.putpixel((x, x % h), (255, 255, 255))
    kw = {"quality": calidad}
    if exif is not None:
        kw["exif"] = exif
    im.save(buf, "JPEG", **kw)
    return buf.getvalue()


def _leer_guardado(saved: dict) -> bytes:
    images_svc.confirmar(saved)
    return Path(saved["ruta_fichero"]).read_bytes()


def _poner_ganancias(monkeypatch, r, g, b):
    monkeypatch.setattr(images_svc, "load_img_params",
                        lambda: {"r_gain": r, "g_gain": g, "b_gain": b, "hires_min_w": 1500})


def test_sin_transformacion_se_guardan_los_bytes_recibidos(monkeypatch):
    _poner_ganancias(monkeypatch, 1.0, 1.0, 1.0)
    datos = _jpeg()
    saved = images_svc.save_capture("zero-SINRECOMP", datos, apply_wb=True)
    assert _leer_guardado(saved) == datos          # ni un byte perdido


def test_foto_del_movil_tampoco_se_recomprime(monkeypatch):
    _poner_ganancias(monkeypatch, 0.9, 1.25, 0.9)  # aunque haya ganancias: el móvil no las lleva
    datos = _jpeg()
    saved = images_svc.save_capture("movil-SINRECOMP", datos, apply_wb=False)
    assert _leer_guardado(saved) == datos


def test_foto_pequena_no_se_corrige_ni_se_recomprime(monkeypatch):
    _poner_ganancias(monkeypatch, 0.9, 1.25, 0.9)
    datos = _jpeg(w=800, h=600)                    # por debajo de hires_min_w
    saved = images_svc.save_capture("zero-PEQUENA", datos, apply_wb=True)
    assert _leer_guardado(saved) == datos


def test_con_ganancias_se_corrige_con_minima_perdida_y_conserva_exif(monkeypatch):
    _poner_ganancias(monkeypatch, 0.9, 1.25, 0.9)
    exif = Image.Exif()
    exif[0x0110] = "IMX708-prueba"                 # Model
    datos = _jpeg(exif=exif.tobytes())
    saved = images_svc.save_capture("esp32-CORREGIDA", datos, apply_wb=True)
    guardado = _leer_guardado(saved)
    assert guardado != datos                       # sí se ha corregido
    im = Image.open(io.BytesIO(guardado))
    assert im.size == (1600, 1200)
    assert im.getexif().get(0x0110) == "IMX708-prueba"
    # Calidad alta: el verde corregido debe acercarse a 180*1.25 = 225 (y no saturar raro).
    r, g, b = im.convert("RGB").getpixel((5, 5))
    assert abs(g - 225) <= 4, (r, g, b)


def test_la_miniatura_sigue_existiendo_y_es_pequena(monkeypatch):
    _poner_ganancias(monkeypatch, 1.0, 1.0, 1.0)
    saved = images_svc.save_capture("zero-MINI", _jpeg(), apply_wb=True)
    images_svc.confirmar(saved)
    t = Image.open(saved["ruta_thumbnail"])
    assert max(t.size) <= 400


def test_una_subida_truncada_sigue_siendo_un_error_claro(monkeypatch):
    _poner_ganancias(monkeypatch, 1.0, 1.0, 1.0)
    datos = _jpeg()[:500]
    try:
        images_svc.save_capture("zero-TRUNCADA", datos, apply_wb=True)
    except ValueError as e:
        assert "no se puede leer" in str(e)
    else:
        raise AssertionError("una foto truncada no puede guardarse")
