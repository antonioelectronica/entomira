"""Tendencia diaria de mosca blanca y trips por trampa.

Para cada trampa y cada día (hora de Madrid): la MEDIA de WF y de TR de las fotos procesadas de
ese día. Con una foto al día es su conteo; con tres, la media de las tres (petición de Antonio,
2026-10-23). Se calcula en cada petición a partir de las detecciones, así que es **dinámica**: si
un modelo mejor reprocesa fotos del pasado, la serie cambia sola.

Ojo, como en todo lo demás: la tarjeta ACUMULA. La serie es el acumulado en la tarjeta, no las
capturas del día; por eso viajan también las fechas de cambio de tarjeta (un descenso brusco ahí
es el cartón nuevo, no la plaga). Un día sin fotos procesadas NO sale con 0: no sale, o sale con
`n_fotos = 0` si tiene fotos pendientes («sin dato» no es «cero»).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session, func, select

from ..models.models import Deteccion, Imagen, Tarjeta, Trampa

MADRID = ZoneInfo("Europe/Madrid")


def _dia(dt: datetime) -> date:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(MADRID).date()


def serie(session: Session, trap_ids: list[int], desde: date | None = None,
          hasta: date | None = None) -> list[dict]:
    out = []
    for tid in trap_ids:
        trap = session.get(Trampa, tid)
        if trap is None:
            continue
        imgs = session.exec(select(Imagen).where(Imagen.trampa_id == tid,
                                                 Imagen.es_demo == False)  # noqa: E712
                            .order_by(Imagen.capturada)).all()
        procesadas = [im.id for im in imgs if im.estado == "procesada"]
        cuenta: dict[int, dict[str, int]] = defaultdict(dict)
        for i in range(0, len(procesadas), 500):
            lote = procesadas[i:i + 500]
            for iid, clase, n in session.exec(
                    select(Deteccion.imagen_id, Deteccion.clase, func.count())
                    .where(Deteccion.imagen_id.in_(lote), Deteccion.clase.in_(("WF", "TR")))
                    .group_by(Deteccion.imagen_id, Deteccion.clase)).all():
                cuenta[iid][clase] = n
        dias: dict[date, dict] = {}
        for im in imgs:
            d = _dia(im.capturada)
            if (desde and d < desde) or (hasta and d > hasta):
                continue
            x = dias.setdefault(d, {"wf": [], "tr": [], "pendientes": 0, "modelos": set()})
            if im.estado == "procesada":
                c = cuenta.get(im.id, {})
                x["wf"].append(c.get("WF", 0))
                x["tr"].append(c.get("TR", 0))
                if im.modelo_version:
                    x["modelos"].add(im.modelo_version)
            elif im.estado == "pendiente":
                x["pendientes"] += 1
        filas = []
        for d in sorted(dias):
            x = dias[d]
            n = len(x["wf"])
            filas.append({
                "dia": d.isoformat(), "n_fotos": n, "pendientes": x["pendientes"],
                "wf_media": round(sum(x["wf"]) / n, 2) if n else None,
                "tr_media": round(sum(x["tr"]) / n, 2) if n else None,
                "modelos": sorted(x["modelos"]),
            })
        tarjetas = session.exec(select(Tarjeta).where(Tarjeta.trampa_id == tid)
                                .order_by(Tarjeta.colocada)).all()
        out.append({
            "trampa_id": tid, "alias": trap.alias or trap.device_id,
            "invernadero_id": trap.invernadero_id,
            "dias": filas,
            "tarjetas": [{"id": t.id, "colocada": _dia(t.colocada).isoformat(),
                          "retirada": _dia(t.retirada).isoformat() if t.retirada else None}
                         for t in tarjetas],
        })
    return out


def csv_texto(series: list[dict]) -> str:
    lineas = ["trampa_id,alias,dia,n_fotos,pendientes,wf_media,tr_media,modelos"]
    for s in series:
        for f in s["dias"]:
            vacio = lambda v: "" if v is None else str(v)  # noqa: E731
            alias = str(s["alias"]).replace(",", " ")
            lineas.append(f'{s["trampa_id"]},{alias},{f["dia"]},{f["n_fotos"]},{f["pendientes"]},'
                          f'{vacio(f["wf_media"])},{vacio(f["tr_media"])},{"|".join(f["modelos"])}')
    return "\n".join(lineas) + "\n"
