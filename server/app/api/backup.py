"""
Copia de seguridad de la base de datos.

Hoy `entomira.db` vive **solo en la tarjeta SD de la Raspberry Pi**. Una SD muerta —que es la
avería más común de una Pi— se lleva el histórico entero de capturas, conteos e invernaderos.
Las imágenes se pueden volver a hacer; las series temporales de conteo, no.

La copia se hace con `VACUUM INTO`, no copiando el fichero: con SQLite en modo WAL, copiar el
`.db` a pelo puede dar una copia **inconsistente** (parte de los datos vive en el `-wal`).
`VACUUM INTO` produce un fichero cerrado y consistente aunque haya escrituras en curso.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlmodel import Session

from ..core.db import get_session
from ..core.deps import require_admin

router = APIRouter(prefix="/api/v1", tags=["copia de seguridad"])


@router.get("/backup/db", dependencies=[Depends(require_admin)])
def descargar_bd(session: Session = Depends(get_session)):
    """Devuelve una copia CONSISTENTE de la base de datos, lista para guardar fuera de la Pi."""
    sello = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    destino = Path(tempfile.gettempdir()) / f"entomira_{sello}.db"
    destino.unlink(missing_ok=True)
    try:
        # VACUUM INTO no admite parametros: la ruta se compone aqui, y viene de tempfile,
        # no de la peticion, asi que no hay entrada del usuario en la sentencia.
        session.exec(text(f"VACUUM INTO '{destino.as_posix()}'"))
        session.commit()
    except Exception as e:
        raise HTTPException(500, f"No se pudo generar la copia: {e}")
    if not destino.exists():
        raise HTTPException(500, "La copia no se generó")
    return FileResponse(destino, media_type="application/octet-stream",
                        filename=destino.name,
                        headers={"Cache-Control": "no-store"})
