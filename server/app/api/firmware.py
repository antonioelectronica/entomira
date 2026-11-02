"""
Distribución de firmware para actualización OTA de las trampas.

Por qué existe: hasta ahora cada cambio de firmware exigía **acceso físico** a la placa
(USB al PC o a la Pi). Con la trampa colgada en un invernadero, eso convierte cualquier
corrección en un desplazamiento. Aquí el servidor publica el binario y la trampa se
actualiza sola desde cualquier red, igual que sube las fotos.

Flujo:
1. `POST /firmware` (admin) sube el `.bin` compilado y su versión. El servidor calcula el
   **MD5** y lo guarda junto al binario.
2. La trampa consulta `GET /firmware/latest` de vez en cuando y compara la versión con la
   suya, que lleva compilada.
3. Si difieren, descarga `GET /firmware/bin` y la escribe con `Update`, que **verifica el
   MD5** antes de dar la actualización por buena. Si algo falla, la placa sigue arrancando
   con la versión anterior: se escribe en la ranura inactiva y solo se conmuta al final.

El MD5 protege de una descarga corrupta, no de un binario malicioso: lo anuncia el mismo que
sirve el fichero. Por eso desde 2026-09-12 **el binario va FIRMADO** (ECDSA P-256 sobre su
SHA-256, firmado en el PC con la clave del proyecto) y la placa lo verifica con la clave
pública que lleva compilada, ANTES de conmutar de partición. Este servidor solo transporta la
firma: aunque alguien lo suplantara o escribiera en él, no puede hacer que una trampa instale
su código.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from ..core.db import get_session
from ..models.models import Trampa
from ..services import consumo as consumo_svc

from ..core import config
from ..core.deps import require_admin
from ..core.security import require_device

router = APIRouter(prefix="/api/v1", tags=["firmware"])

DIR = config.DATA_DIR / "firmware"
BIN = DIR / "firmware.bin"
META = DIR / "latest.json"


def _meta() -> dict | None:
    try:
        return json.loads(META.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@router.get("/firmware/latest", dependencies=[Depends(require_device)])
def latest():
    """Qué versión hay publicada. La trampa la compara con la suya.

    Devuelve `{}` si no hay ninguna: la placa lo interpreta como «nada que hacer» y sigue.
    """
    m = _meta()
    if not m or not BIN.exists():
        return {}
    return m


@router.get("/firmware/bin", dependencies=[Depends(require_device)])
def binario(request: Request, device_id: str = "", session: Session = Depends(get_session)):
    """El binario. Lo descarga la trampa por trozos (`Range`) y lo escribe con `Update`.

    `device_id` es opcional y sirve solo para apuntar el gasto de datos: una actualización son
    ~1 MB por 4G, con diferencia lo más caro que descarga una trampa, y sin esto no aparecería
    en el contador.
    """
    if not BIN.exists():
        raise HTTPException(404, "No hay firmware publicado.")
    if device_id:
        trap = session.exec(select(Trampa).where(Trampa.device_id == device_id)).first()
        if trap:
            # Lo que se lleva este trozo: el `Range` dice exactamente cuánto.
            rango = request.headers.get("range", "")
            trozo = 0
            if rango.startswith("bytes="):
                try:
                    ini, fin = rango[6:].split("-", 1)
                    trozo = (int(fin) - int(ini) + 1) if fin else (BIN.stat().st_size - int(ini))
                except ValueError:
                    trozo = 0
            consumo_svc.anotar(session, trap, "ota",
                               bytes_=trozo or BIN.stat().st_size)
    return FileResponse(BIN, media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store"})


@router.post("/firmware", dependencies=[Depends(require_admin)])
async def publicar(fichero: UploadFile = File(...), version: str = Form(...),
                   notas: str = Form(default=""), firma: str = Form(default="")):
    """Publica una versión nueva. La sube `scripts/publicar_firmware.py` tras compilar."""
    datos = await fichero.read()
    if len(datos) < 100_000:
        # Un firmware de esta placa ronda el MB. Algo mucho menor es casi seguro un error
        # (fichero equivocado, descarga truncada) y publicarlo dejaría a las trampas
        # intentando instalar basura.
        raise HTTPException(422, f"El binario parece incompleto ({len(datos)} bytes).")

    DIR.mkdir(parents=True, exist_ok=True)
    BIN.write_bytes(datos)
    # La FIRMA se guarda tal cual y se sirve a la trampa. El servidor no la comprueba —no
    # tendria sentido: si alguien puede escribir aqui, tambien podria falsear la comprobacion—.
    # Quien la verifica es la placa, con la clave publica que lleva compilada, y por eso este
    # servidor deja de ser una pieza de confianza para el firmware.
    if not firma.strip():
        raise HTTPException(
            422, "Falta la firma del binario. Publica con scripts/publicar_firmware.py, que "
                 "firma con la clave del proyecto: las trampas rechazan el firmware sin firmar.")
    meta = {
        "version": version.strip(),
        "md5": hashlib.md5(datos).hexdigest(),
        "bytes": len(datos),
        "firma": firma.strip(),
        "publicado": datetime.now(timezone.utc).isoformat(),
        "notas": notas.strip(),
    }
    META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta
