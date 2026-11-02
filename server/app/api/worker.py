"""Telemetría del Worker (PC/GPU) y de la Pi, y parámetros de inferencia."""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import __version__
from ..core import config
from ..core.deps import require_admin
from ..core.security import require_worker

router = APIRouter(prefix="/api/v1", tags=["worker"])

_START = time.time()
_worker_stats: dict = {}          # empujado por el Worker
_PARAMS_FILE = config.DATA_DIR / "worker_params.json"
_DEFAULT_PARAMS = {"conf": 0.25, "iou": 0.45, "intervalo": 2, "pausado": False}


def _load_params() -> dict:
    if _PARAMS_FILE.exists():
        try:
            return {**_DEFAULT_PARAMS, **json.loads(_PARAMS_FILE.read_text())}
        except Exception:
            pass
    return dict(_DEFAULT_PARAMS)


def _save_params(p: dict) -> None:
    _PARAMS_FILE.write_text(json.dumps(p), encoding="utf-8")


class WorkerStats(BaseModel):
    gpu_name: str = ""
    gpu_temp: float | None = None
    gpu_util: float | None = None
    gpu_mem_used: float | None = None
    gpu_mem_total: float | None = None
    gpu_power: float | None = None
    cpu: float | None = None
    ram_used: float | None = None
    ram_total: float | None = None


class Params(BaseModel):
    conf: float | None = None
    iou: float | None = None
    intervalo: int | None = None
    pausado: bool | None = None


@router.post("/worker/stats", dependencies=[Depends(require_worker)])
def push_stats(s: WorkerStats):
    global _worker_stats
    _worker_stats = {**s.model_dump(), "ts": datetime.now(timezone.utc).isoformat()}
    # Con el análisis por franjas (17/10/2026) el worker pasa horas sin pedir trabajo aunque
    # esté vivo: su telemetría, cada 15 s, también cuenta como «visto». Si no, el panel y los
    # avisos lo daban por caído toda la noche.
    from .health import mark_worker_seen
    mark_worker_seen()
    return {"ok": True}


@router.get("/worker/stats")
def get_stats():
    online = False
    if _worker_stats.get("ts"):
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(_worker_stats["ts"])).total_seconds()
        online = age < 30
    return {"online": online, **_worker_stats, "pi": _pi_stats()}


RETRASO_ANALISIS_MIN = 10   # el worker analiza 10 min después de cada despertar


def horas_analisis() -> list[str]:
    """Horas (HH:MM, Europe/Madrid) a las que el worker debe analizar: cada despertar de las
    trampas FÍSICAS en campo + 10 min (petición de Antonio, 17/10/2026). Sin trampas en campo,
    lista vacía: el worker sigue sondeando como siempre. Acordado con Codex."""
    from sqlmodel import Session, select
    from ..core.db import engine
    from ..models.models import ConfigCamara, Trampa
    from ..services import traps as traps_svc
    from ..services.demo import DEMO_DEVICE
    horas = set()
    with Session(engine) as s:
        filas = s.exec(select(ConfigCamara, Trampa).where(
            # Lo que decide es el modo campo, no el dueño: owner_id solo lo tienen los móviles,
            # que nunca duermen (aviso de Codex: no filtrar por ausencia de dueño).
            ConfigCamara.trampa_id == Trampa.id, ConfigCamara.deep_sleep == 1,
            Trampa.device_id != DEMO_DEVICE,
            Trampa.device_id != traps_svc.MANUAL_UPLOAD_DEVICE)).all()
    for cfg, _ in filas:
        for h in (cfg.horas_despertar or [cfg.hora_despertar]):
            try:
                hh, mm = map(int, str(h).split(":"))
            except ValueError:
                continue
            m = (hh * 60 + mm + RETRASO_ANALISIS_MIN) % (24 * 60)
            horas.add(f"{m // 60:02d}:{m % 60:02d}")
    return sorted(horas)


@router.get("/worker/params")
def get_params():
    return {**_load_params(), "horas_analisis": horas_analisis()}


@router.put("/worker/params", dependencies=[Depends(require_admin)])
def set_params(p: Params):
    cur = _load_params()
    for k, v in p.model_dump(exclude_none=True).items():
        cur[k] = v
    _save_params(cur)
    return cur


# Recorrer el árbol de imágenes cuesta segundos cuando hay decenas de miles de ficheros
# (con 119 k llegó a tardar 7 s y lo llama cada carga de la vista Trampas) → se cachea.
_IMG_SIZE_TTL_S = 120
_img_size_cache: dict = {"ts": 0.0, "bytes": 0, "n": 0}


def _images_size() -> tuple[int, int]:
    """(bytes, nº de ficheros) del árbol de imágenes, cacheado _IMG_SIZE_TTL_S segundos."""
    if time.time() - _img_size_cache["ts"] < _IMG_SIZE_TTL_S:
        return _img_size_cache["bytes"], _img_size_cache["n"]
    total = n = 0
    for f in config.IMAGES_DIR.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
                n += 1
        except OSError:
            pass
    _img_size_cache.update({"ts": time.time(), "bytes": total, "n": n})
    return total, n


@router.get("/system/health")
def system_health():
    du = shutil.disk_usage(str(config.DATA_DIR))
    db = config.DB_PATH
    db_mb = round(db.stat().st_size / 1e6, 2) if db.exists() else 0
    img_bytes, img_n = _images_size()
    used_pct = round(du.used / du.total * 100, 1)
    return {
        "version": __version__,
        "uptime_h": round((time.time() - _START) / 3600, 1),
        "disk_used_pct": used_pct,
        "disk_free_gb": round(du.free / 1e9, 1),
        "disk_used_gb": round(du.used / 1e9, 1),
        "disk_total_gb": round(du.total / 1e9, 1),
        # nivel de alerta para el gadget de la web: ok < 70 % <= aviso < 85 % <= critico
        "disk_nivel": "critico" if used_pct >= 85 else ("aviso" if used_pct >= 70 else "ok"),
        "db_mb": db_mb,
        # Conexiones del pool en uso. Vale para UNA cosa concreta y cara: el 2026-09-12 la API
        # se quedo atascada con el pool agotado (15 conexiones) y desde fuera solo se veia un
        # servicio «active» que no contestaba. Sin este numero, el centinela no puede
        # distinguir «va lento» de «esta a punto de dejar de responder».
        "db": _pool_stats(),
        "images_mb": round(img_bytes / 1e6, 2),
        "images_n": img_n,
        "pi": _pi_stats(),
    }


def _pool_stats() -> dict:
    """Cuantas conexiones del pool estan cogidas ahora mismo, y cuantas caben."""
    try:
        from ..core.db import engine
        p = engine.pool
        cabe = p.size() + getattr(p, "_max_overflow", 0)
        return {"conexiones_en_uso": p.checkedout(), "conexiones_max": cabe}
    except Exception:                                # noqa: BLE001 - informar no puede romper
        return {}


def _pi_stats() -> dict:
    """Telemetría de la Raspberry Pi (lecturas de /proc y /sys, sin dependencias)."""
    out = {}
    try:
        temp = Path("/sys/class/thermal/thermal_zone0/temp")
        if temp.exists():
            out["cpu_temp"] = round(int(temp.read_text().strip()) / 1000, 1)
    except Exception:
        pass
    try:
        load = float(Path("/proc/loadavg").read_text().split()[0])
        import os
        out["cpu"] = round(load / (os.cpu_count() or 1) * 100, 1)
    except Exception:
        pass
    try:
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":")
            mem[k.strip()] = int(v.strip().split()[0])
        total = mem.get("MemTotal", 0) / 1024
        avail = mem.get("MemAvailable", 0) / 1024
        out["ram_used"] = round(total - avail)
        out["ram_total"] = round(total)
    except Exception:
        pass
    return out
