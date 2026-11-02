"""
Perfil de configuración: una configuración conocida y buena a la que poder volver.

Nace de un problema real: en una sola tarde de pruebas el intervalo de captura pasó por
600 → 200 → 30 → 20 → 15 → 600 s y la exposición por −2 → 0, todo a mano. Cuando algo empieza
a ir peor, sin una referencia guardada no hay forma de saber **qué se tocó**.

Aquí se guarda el conjunto completo de parámetros de cámara de una trampa, con una nota de
por qué es bueno, y se puede restaurar de una vez.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlmodel import Session

from ..core import config
from ..core.db import get_session
from ..core.deps import require_admin
from ..models.models import ConfigCamara, Trampa
from ..services import traps as traps_svc
from .serializers import config_out

router = APIRouter(prefix="/api/v1", tags=["perfil"])

FICHERO = config.DATA_DIR / "perfiles_camara.json"
CAMPOS = ("ancho", "alto", "calidad_jpeg", "brillo", "contraste", "saturacion",
          "ev", "balance", "enfoque", "lente_dioptrias",
          "intervalo_seg", "deep_sleep", "hora_despertar", "horas_despertar", "ventana_despierto_seg")


def _leer() -> dict:
    try:
        return json.loads(FICHERO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _escribir(d: dict) -> None:
    FICHERO.parent.mkdir(parents=True, exist_ok=True)
    FICHERO.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


@router.get("/traps/{trap_id}/perfil")
def ver_perfil(trap_id: int):
    """El perfil guardado de esta trampa, o `{}` si no hay ninguno."""
    return _leer().get(str(trap_id), {})


@router.post("/traps/{trap_id}/perfil", dependencies=[Depends(require_admin)])
def guardar_perfil(trap_id: int, nota: str = Body("", embed=True),
                   session: Session = Depends(get_session)):
    """Guarda la configuración ACTUAL como referencia, con una nota de por qué es buena."""
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")
    cfg = session.get(ConfigCamara, trap_id)
    if not cfg:
        raise HTTPException(404, "Esta trampa no tiene configuración todavía")

    perfil = {c: getattr(cfg, c) for c in CAMPOS}
    perfil["nota"] = nota.strip()
    perfil["guardado"] = datetime.now(timezone.utc).isoformat()
    todos = _leer()
    todos[str(trap_id)] = perfil
    _escribir(todos)
    return perfil


@router.post("/traps/{trap_id}/perfil/restaurar", dependencies=[Depends(require_admin)])
def restaurar_perfil(trap_id: int, session: Session = Depends(get_session)):
    """Devuelve la cámara al perfil guardado. Sube la versión: la placa lo aplicará."""
    perfil = _leer().get(str(trap_id))
    if not perfil:
        raise HTTPException(404, "Esta trampa no tiene ningún perfil guardado")
    trap = session.get(Trampa, trap_id)
    if not trap:
        raise HTTPException(404, "Trampa no encontrada")

    cfg = traps_svc.ensure_config(session, trap)
    if "horas_despertar" not in perfil:
        perfil["horas_despertar"] = None  # perfil antiguo: usar su hora única
    cambios = 0
    for c in CAMPOS:
        if c in perfil and getattr(cfg, c) != perfil[c]:
            setattr(cfg, c, perfil[c])
            cambios += 1
    if cambios:
        cfg.version += 1
        cfg.actualizado = datetime.now(timezone.utc)
        session.add(cfg)
        session.commit()
        session.refresh(cfg)
    return {"restaurados": cambios, "config": config_out(cfg)}
