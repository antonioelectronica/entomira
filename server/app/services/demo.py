"""
Modo Demo. Sin trampas físicas, el fallback siembra una imagen pendiente a partir de
una muestra del dataset; el Worker la procesa (YOLO real en GPU) por el pipeline normal
y el frontend hace la animación de escáner mientras espera las detecciones.
"""
from __future__ import annotations

import random
import shutil
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlmodel import Session, select

from ..core import config
from ..models.models import Imagen, Trampa
from . import images as images_svc
from . import traps as traps_svc

DEMO_SAMPLES = config.DATA_DIR / "demo_samples"
DEMO_DEVICE = "demo-fallback"
DEVICE_STREAM_PORT = 81


def _seed_pending(session: Session, trap: Trampa, data: bytes) -> Imagen:
    saved = images_svc.save_capture(trap.device_id, data)
    img = Imagen(trampa_id=trap.id, estado="pendiente", es_demo=True, **saved)
    session.add(img)
    try:
        session.commit()
    except Exception:
        session.rollback()
        images_svc.descartar(saved)      # sin fila no debe quedar fichero
        raise
    images_svc.confirmar(saved)
    session.refresh(img)
    return img


def capture_from_trap(session: Session, trap: Trampa) -> Imagen:
    """Foto REAL de la trampa: por WiFi si es alcanzable; si no, la última que subió
    por USB (sigue siendo una imagen real de la cámara). La siembra como pendiente."""
    # 1) foto fresca por WiFi (si la cámara responde)
    if trap.ip:
        try:
            r = httpx.get(f"http://{trap.ip}:{DEVICE_STREAM_PORT}/snapshot", timeout=6)
            if r.status_code == 200 and r.content:
                traps_svc.touch_seen(session, trap)
                return _seed_pending(session, trap, r.content)
        except httpx.HTTPError:
            pass

    # 2) fallback: la última captura real de la trampa (llega por USB tethered)
    last = session.exec(
        select(Imagen).where(Imagen.trampa_id == trap.id, Imagen.es_demo == False)  # noqa: E712
        .order_by(Imagen.capturada.desc()).limit(1)
    ).first()
    if last and Path(last.ruta_fichero).exists():
        return _seed_pending(session, trap, Path(last.ruta_fichero).read_bytes())

    raise ConnectionError("No hay cámara alcanzable ni capturas recientes de esta trampa.")


def list_samples() -> list[Path]:
    if not DEMO_SAMPLES.exists():
        return []
    return [p for p in DEMO_SAMPLES.iterdir()
            if p.suffix.lower() in (".jpg", ".jpeg", ".png")]


def seed_demo_image(session: Session, sample: Path | None = None) -> Imagen:
    """Crea una imagen de demo en estado 'pendiente' para que la procese el Worker."""
    samples = list_samples()
    if not samples:
        raise FileNotFoundError(
            "No hay muestras de demo en data/demo_samples/. "
            "Súbelas en el despliegue (FASE 2).")
    chosen = sample or random.choice(samples)

    # trampa virtual de demo (auto-registrada una vez)
    trap, _ = traps_svc.get_or_register(session, DEMO_DEVICE)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    day_dir = config.IMAGES_DIR / "demo"
    day_dir.mkdir(parents=True, exist_ok=True)
    dest = day_dir / f"demo_{ts}{chosen.suffix.lower()}"
    shutil.copy2(chosen, dest)

    from PIL import Image as PILImage
    with PILImage.open(dest) as im:
        w, h = im.size
        thumb_dir = config.THUMBS_DIR / "demo"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        tpath = thumb_dir / dest.name
        t = im.convert("RGB")
        t.thumbnail((400, 400))
        t.save(tpath, "JPEG", quality=80)

    img = Imagen(
        trampa_id=trap.id,
        ruta_fichero=str(dest),
        ruta_thumbnail=str(tpath),
        ancho=w, alto=h,
        estado="pendiente",
        es_demo=True,
    )
    session.add(img)
    session.commit()
    session.refresh(img)
    return img
