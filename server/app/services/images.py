"""Guardado de imágenes en disco + generación de thumbnail + corrección de color.

La corrección de color del OV5640 (tinte magenta a alta resolución) es AJUSTABLE en
caliente desde el panel: los gains R/G/B y el umbral se guardan en `image_params.json`.
"""
from __future__ import annotations

import io
import logging
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image as PILImage

from ..core import config
from . import recorte as recorte_svc

_log = logging.getLogger("entomira.imagenes")

# Valores por defecto (calibración fija que neutraliza el tinte magenta del OV5640).
DEFAULT_IMG_PARAMS = {"r_gain": 0.90, "g_gain": 1.25, "b_gain": 0.90, "hires_min_w": 1500}
_PARAMS_FILE = config.DATA_DIR / "image_params.json"
# Muestra RAW (sin corregir) de la última captura del ESP32, para la previsualización.
CALIB_SAMPLE = config.DATA_DIR / "calib_sample.jpg"


def load_img_params() -> dict:
    if _PARAMS_FILE.exists():
        try:
            return {**DEFAULT_IMG_PARAMS, **json.loads(_PARAMS_FILE.read_text())}
        except Exception:
            pass
    return dict(DEFAULT_IMG_PARAMS)


def save_img_params(p: dict) -> dict:
    cur = load_img_params()
    for k in ("r_gain", "g_gain", "b_gain", "hires_min_w"):
        if k in p and p[k] is not None:
            cur[k] = p[k]
    _PARAMS_FILE.write_text(json.dumps(cur), encoding="utf-8")
    return cur


def _apply(ch, gain):
    return ch.point(lambda v: min(255, int(v * gain)))


def apply_gains(img: "PILImage.Image", r: float, g: float, b: float,
                hires_min_w: int) -> "PILImage.Image":
    """Aplica los gains por canal si la imagen es de alta resolución."""
    if img.width < hires_min_w:
        return img
    if img.mode != "RGB":
        img = img.convert("RGB")
    rc, gc, bc = img.split()
    return PILImage.merge("RGB", (_apply(rc, r), _apply(gc, g), _apply(bc, b)))


def wb_fix(img: "PILImage.Image", params: dict | None = None) -> "PILImage.Image":
    p = params or load_img_params()
    return apply_gains(img, p["r_gain"], p["g_gain"], p["b_gain"], int(p["hires_min_w"]))


# Sufijo de los ficheros que todavía NO son definitivos. Ver `save_capture`.
PARCIAL = ".parcial"


def save_capture(device_id: str, data: bytes, apply_wb: bool = True,
                 recorte: dict | None = None) -> dict:
    """Guarda el JPEG y su thumbnail **en provisional**. Devuelve rutas y dimensiones.

    Los ficheros se escriben con sufijo `.parcial` y no son definitivos hasta que quien
    llama confirme con `confirmar()` — después de que la fila esté commiteada — o los tire
    con `descartar()`.

    **Por qué.** Antes se escribían directamente y la fila se creaba después: si el commit
    fallaba, el fichero se quedaba en disco sin nadie que lo mirara ni lo borrara. Un
    huérfano invisible que come disco es exactamente como se llenó la Pi en julio y tumbó
    la ingesta tres días **en silencio**.

    `apply_wb`: aplicar la corrección de color del sensor OV5640 (solo capturas del
    ESP32). Las fotos del móvil NO deben pasar por ella (su cámara no tiene el tinte).
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    safe = device_id.replace(":", "").replace("/", "")
    name = f"{safe}_{ts}.jpg"

    day_dir = config.IMAGES_DIR / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    fpath = day_dir / name

    # UNA FOTO TRUNCADA NO PUEDE TUMBAR LA INGESTA.
    #
    # Por 4G los envios se cortan —el log de la placa lo dice tal cual: «se corto el envio de la
    # imagen»— y lo que llega entonces no es un JPEG. Pillow levantaba `UnidentifiedImageError`,
    # nadie la capturaba, y el endpoint devolvia **500**. Para la trampa un 500 es un fallo del
    # servidor, asi que **reintenta**: y cada reintento por movil son otros 300 KB del plan
    # gastados en mandar los mismos bytes rotos.
    #
    # Un 422 dice «esto que me mandas no vale», que es la verdad, y la placa no insiste.
    try:
        img = PILImage.open(io.BytesIO(data)).convert("RGB")
    except Exception as e:                       # noqa: BLE001 - Pillow lanza de varios tipos
        raise ValueError(
            f"la imagen no se puede leer ({type(e).__name__}): "
            f"{len(data)} bytes recibidos, probablemente una subida cortada") from e
    w, h = img.size
    p = load_img_params()
    if apply_wb:
        # guarda una muestra RAW (sin corregir) para la previsualización del panel de color
        try:
            if img.width >= int(p["hires_min_w"]):
                img.save(CALIB_SAMPLE, "JPEG", quality=85)
        except Exception:
            pass
    # LA FOTO SE GUARDA TAL CUAL LLEGA, salvo que haya que corregirle el color de verdad.
    #
    # Aquí se decodificaba y se volvía a comprimir SIEMPRE a calidad 90: la trampa captura a
    # calidad máxima (decisión de Antonio: la mosca blanca sale a ~28 px y el detalle fino es
    # todo) y el servidor tiraba en silencio unas tres cuartas partes de cada foto —medido el
    # 2026-09-26: 3,5-4,8 MB capturados, 1,1-1,3 MB guardados—, con las ganancias de color a
    # 1,0, o sea sin corregir nada. Lo encontró Codex el 2026-09-19 y el hallazgo se perdió sin
    # aplicarse ni descartarse; se recuperó al construir el estudio de revisiones.
    #
    # Sin transformación se escriben LOS BYTES RECIBIDOS, no una recodificación: conserva EXIF,
    # ICC y orientación tal como los puso la cámara. Con ganancias distintas de 1,0 (el tinte
    # del OV5640 del ESP32), se corrige y se guarda con la mínima pérdida posible, conservando
    # los metadatos que Pillow sabe llevar. Acordado con Codex, 2026-09-26.
    ganancias = (float(p["r_gain"]), float(p["g_gain"]), float(p["b_gain"]))
    transformar = (apply_wb and img.width >= int(p["hires_min_w"])
                   and ganancias != (1.0, 1.0, 1.0))
    if transformar:
        extra = {k: img.info[k] for k in ("exif", "icc_profile", "dpi") if k in img.info}
        img = wb_fix(img, p)       # corrige el tinte magenta del OV5640 a alta resolución
        img.save(str(fpath) + PARCIAL, "JPEG", quality=95, subsampling=0, **extra)
    else:
        with open(str(fpath) + PARCIAL, "wb") as fh:
            fh.write(data)

    # RECORTE DE LA TARJETA (calibración fija por trampa, services/recorte.py). Un derivado
    # aparte: el original de arriba no se toca. Si falla, la foto entra igual sin recorte.
    rpath = None
    version_recorte = None
    try:
        recortada = recorte_svc.aplicar(img, recorte)
    except ValueError as e:
        recortada = None
        _log.warning("recorte de %s no aplicado: calibración inválida (%s)", device_id, e)
    if recorte and recortada is None:
        # Que no pase en silencio: con calibración y sin recorte, la IA verá la foto entera.
        _log.warning("recorte de %s no aplicado: la foto mide %sx%s y la calibración es para %s",
                     device_id, w, h, recorte.get("res"))
    if recortada is not None:
        rdir = config.IMAGES_DIR / "recortes" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rdir.mkdir(parents=True, exist_ok=True)
        rpath = rdir / name
        recortada.save(str(rpath) + PARCIAL, "JPEG", quality=recorte_svc.CALIDAD, subsampling=0)
        version_recorte = recorte_svc.version(recorte)
        img = recortada                          # la miniatura, del recorte

    thumb_dir = config.THUMBS_DIR / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    thumb_dir.mkdir(parents=True, exist_ok=True)
    tpath = thumb_dir / name
    thumb = img.copy()
    thumb.thumbnail((400, 400))
    thumb.save(str(tpath) + PARCIAL, "JPEG", quality=80)

    # Se devuelven las rutas DEFINITIVAS: es lo que va en la fila, y lo que `confirmar()`
    # pondrá en su sitio en cuanto esa fila exista de verdad.
    return {
        "ruta_fichero": str(fpath),
        "ruta_thumbnail": str(tpath),
        "ruta_recorte": str(rpath) if rpath else None,
        "recorte_version": version_recorte,
        "ancho": w,
        "alto": h,
    }


def confirmar(saved: dict) -> None:
    """Pasa los ficheros a su nombre definitivo. Se llama DESPUÉS del commit de la fila."""
    for clave in ("ruta_fichero", "ruta_thumbnail", "ruta_recorte"):
        if not saved.get(clave):
            continue
        p = Path(str(saved.get(clave, "")) + PARCIAL)
        if p.exists():
            p.replace(str(saved[clave]))     # renombrar es atómico dentro del mismo disco


def descartar(saved: dict) -> None:
    """Tira los ficheros de una captura que no llegó a tener fila."""
    for clave in ("ruta_fichero", "ruta_thumbnail", "ruta_recorte"):
        base = str(saved.get(clave) or "")
        if not base:
            continue
        for p in (Path(base + PARCIAL), Path(base)):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def read_bytes(path: str) -> bytes:
    return Path(path).read_bytes()
