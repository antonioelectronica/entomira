"""Log de la Zero subido al servidor: diagnóstico sin abrir SSH.

Orden de Antonio (15/10): «que el servidor, cuando se comunique con ella, le meta las
instrucciones y la Zero devuelva el log». Una sesión SSH abierta además impide el corte, así
que leer el log en remoto debe ser la excepción. La Zero sube su log justo antes de cada corte
(o en el arranque siguiente si entonces no había red) y cuando el panel lo pide.
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlmodel import select

from ..core import config
from ..core.db import sesion_corta
from ..core.deps import require_admin
from ..core.security import require_device
from ..models.models import Trampa
from ..services import live as live_svc

router = APIRouter(prefix="/api/v1", tags=["log de la trampa"])

LOGS_DIR = config.DATA_DIR / "logs_trampa"
CONSERVAR = 200                       # por trampa; los más viejos se borran
_NOMBRE = re.compile(r"^\d{8}T\d{6}_\d{6}Z_[a-z_]{1,20}\.log$")


class LogIn(BaseModel):
    boot: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=40)
    motivo: str = Field(pattern=r"^[a-z_]{1,20}$")
    texto: str = Field(max_length=300_000)
    # Hora (epoch) a la que la Zero acaba de armar su alarma. Solo en los logs de corte.
    despertar: int | None = None


CORTES = {"corte", "corte_panel", "emergencia", "apagado"}


def _id_de(device_id: str, body: "LogIn") -> int:
    """Id de la trampa y, si el log es de un corte, el despertar que la Zero acaba de armar.

    La sesión vive solo esta consulta: no se retiene mientras se escribe el fichero.
    """
    with sesion_corta() as session:
        trap = session.exec(select(Trampa).where(Trampa.device_id == device_id)).first()
        if trap is None:
            raise HTTPException(404, "Trampa no encontrada")
        if body.motivo in CORTES:
            ahora = datetime.now(timezone.utc)
            cuando = None
            if body.despertar is not None:
                cuando = datetime.fromtimestamp(body.despertar, timezone.utc)
                # Una hora imposible no se publica como confirmada.
                if not timedelta(minutes=-5) <= cuando - ahora <= timedelta(days=28):
                    cuando = None
            trap.despertar_confirmado = cuando
            trap.despertar_origen = body.motivo
            trap.despertar_informado = ahora
            session.add(trap)
            session.commit()
        return trap.id


def _carpeta(trap_id: int) -> Path:
    return LOGS_DIR / str(int(trap_id))


@router.post("/traps/{device_id}/log", dependencies=[Depends(require_device)])
def recibir_log(device_id: str, body: LogIn):
    trap_id = _id_de(device_id, body)
    carpeta = _carpeta(trap_id)
    carpeta.mkdir(parents=True, exist_ok=True)
    # Con microsegundos: dos subidas en el mismo segundo no se pisan (hallazgo de Codex).
    nombre = "%s_%s.log" % (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ"), body.motivo)
    cabecera = "# %s · %s · boot %s · motivo %s\n" % (
        device_id, body.version or "?", body.boot or "?", body.motivo)
    fd, tmp = tempfile.mkstemp(prefix=".log-", dir=carpeta)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(cabecera + body.texto)
        os.replace(tmp, carpeta / nombre)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise HTTPException(507, "No se pudo guardar el log")
    for viejo in sorted(p for p in carpeta.glob("*.log") if _NOMBRE.match(p.name))[:-CONSERVAR]:
        viejo.unlink(missing_ok=True)
    if body.motivo != "arranque_previo":   # un log atrasado no responde a una petición nueva
        live_svc.log_recibido(trap_id)
    return {"ok": True, "nombre": nombre}


def _logs(trap_id: int) -> list[Path]:
    carpeta = _carpeta(trap_id)
    if not carpeta.is_dir():
        return []
    return sorted(p for p in carpeta.glob("*.log") if _NOMBRE.match(p.name))


@router.get("/traps/{trap_id}/logs", dependencies=[Depends(require_admin)])
def listar_logs(trap_id: int):
    return [{"nombre": p.name, "bytes": p.stat().st_size} for p in reversed(_logs(trap_id))]


@router.get("/traps/{trap_id}/logs/ultimo", response_class=PlainTextResponse,
            dependencies=[Depends(require_admin)])
def ultimo_log(trap_id: int):
    logs = _logs(trap_id)
    if not logs:
        raise HTTPException(404, "Esta trampa aún no ha subido ningún log")
    return logs[-1].read_text(encoding="utf-8", errors="replace")


@router.get("/traps/{trap_id}/logs/{nombre}", response_class=PlainTextResponse,
            dependencies=[Depends(require_admin)])
def ver_log(trap_id: int, nombre: str):
    if not _NOMBRE.match(nombre):
        raise HTTPException(404, "Log no encontrado")
    ruta = _carpeta(trap_id) / nombre
    if not ruta.is_file():
        raise HTTPException(404, "Log no encontrado")
    return ruta.read_text(encoding="utf-8", errors="replace")


@router.post("/traps/{trap_id}/log/pedir", dependencies=[Depends(require_admin)])
def pedir_log(trap_id: int):
    live_svc.pedir_log(trap_id)
    return {"ok": True, "aviso": "la trampa lo subirá en su siguiente telemetría"}
