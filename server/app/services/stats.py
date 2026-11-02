"""Estadísticas globales: conteos, composición por especie, actividad.

⚠️ **Cómo se cuenta aquí, y por qué no es obvio.** Una trampa adhesiva ACUMULA: los insectos
siguen pegados de una foto a la siguiente. Por eso **nunca se suma `num_insectos` de varias
fotos de la misma trampa** — eso contaba el mismo bicho una vez por captura (con 108 fotos/día,
30 moscas se mostraban como 3.240) y el número cambiaba al tocar el intervalo de captura.

Las dos magnitudes correctas, y son distintas:

- **Acumulado** (`insectos`, `por_especie`): lo que hay pegado AHORA. Se lee de la **última**
  foto de cada trampa, porque ya contiene a todos los anteriores.
- **Ritmo** (`actividad_1d`): capturas NUEVAS en 24 h, es decir la derivada. Se calcula
  restando conteos dentro de cada tarjeta (ver `services/actividad.py`).

Sumar entre TRAMPAS sí es correcto: son insectos distintos.
"""
from __future__ import annotations

from sqlmodel import Session, func, select

from ..models.models import ConfigCamara, Imagen, Trampa
from . import actividad as act_svc
from . import traps as traps_svc

PLAGA = {"WF", "TR"}          # TR = trips. Solo se gestionan estas dos (2026-10-23)


def global_stats(session: Session, trap_ids: list[int] | None = None) -> dict:
    """KPIs globales. Si `trap_ids` se da (agricultor), se limita a esas trampas."""
    # Las imagenes de la vista Demo salen de un dataset publico: contarlas en los KPIs
    # inflaba los insectos con bichos que no estan en ninguna trampa del usuario.
    base = select(func.count()).select_from(Imagen).where(Imagen.es_demo == False)  # noqa: E712
    if trap_ids is not None:
        base = base.where(Imagen.trampa_id.in_(trap_ids or [-1]))
    total = session.exec(base).one()
    analizadas = session.exec(base.where(Imagen.estado == "procesada")).one()
    pendientes = session.exec(base.where(Imagen.estado == "pendiente")).one()

    tq = select(Trampa).where(Trampa.device_id != traps_svc.MANUAL_UPLOAD_DEVICE)
    if trap_ids is not None:
        tq = tq.where(Trampa.id.in_(trap_ids or [-1]))
    traps = session.exec(tq).all()
    activas = sum(1 for t in traps
                  if traps_svc.computed_estado(
                      t, session.get(ConfigCamara, t.id)) == "online")

    # Solo trampas reales: la de demostración no tiene tarjeta ni representa un invernadero.
    from .demo import DEMO_DEVICE
    ids = [t.id for t in traps if t.device_id != DEMO_DEVICE]

    # ACUMULADO: lo que hay pegado ahora en las tarjetas (última foto de cada trampa).
    total_insectos = act_svc.insectos_en_tarjetas(session, ids)
    por_especie = act_svc.especies_en_tarjetas(session, ids)
    wf = por_especie.get("WF", 0)
    tr = por_especie.get("TR", 0)
    generico = por_especie.get("insecto", 0)

    # RITMO: capturas nuevas en 24 h. Es la derivada, y es lo que decide un tratamiento.
    act = act_svc.nuevas_varias(session, ids, dias=1.0)

    return {
        "fotos": total, "analizadas": analizadas, "pendientes": pendientes,
        "insectos": total_insectos,
        "por_especie": {"WF": wf, "TR": tr, "insecto": generico},
        "mosca_blanca": wf, "trips": tr, "plagas": wf + tr,
        "trampas": len(traps), "trampas_activas": activas,
        "actividad_1d": act,
    }
