"""
Actividad: cuántos insectos han caído NUEVOS, que es lo que decide un tratamiento.

**El problema que resuelve.** Los indicadores del panel sumaban `num_insectos` de cada foto de
la ventana. Pero todas esas fotos son de la misma tarjeta y los insectos siguen pegados de una
a la siguiente, así que se contaba **el mismo bicho una vez por captura**. Medido en la Pi:
108 fotos procesadas al día; 30 moscas blancas se habrían mostrado como **3.240**. Y el número
cambiaba al cambiar el intervalo de captura, que no tiene nada que ver con la plaga.

**La magnitud correcta.** Una trampa adhesiva acumula, así que su conteo es una **integral**.
Lo que un agricultor necesita es la **derivada**: capturas nuevas en un periodo. Se obtiene
restando el conteo al principio del conteo al final, dentro de cada tarjeta:

    nuevas = conteo(final) - conteo(inicio)

**Por qué hay que trocear por tarjeta.** Si en medio de la ventana alguien cambió el cartón, el
conteo cae a cero sin que la plaga haya hecho nada. Tratado como una sola serie daría una
variación negativa enorme. Troceado por tarjeta, cada periodo aporta su propio incremento y una
tarjeta puesta dentro de la ventana parte de 0, que es la verdad: estaba limpia.

**Sobre el signo.** El incremento de una tarjeta NO se recorta a cero. Un conteo puede bajar
dentro de la misma tarjeta por ruido del detector, y recortar cada bajada a cero sesgaría el
total sistemáticamente hacia arriba — justo el sesgo que el artículo ya midió (el sistema tiende
a recomendar tratar). Solo se recorta el total, porque «−3 capturas nuevas» no significa nada.
"""
from __future__ import annotations

from .clases import gestionada

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, func, select

from ..models.models import Deteccion, Imagen, Tarjeta


def _naive(dt: datetime) -> datetime:
    """Normaliza a UTC sin zona, como devuelve SQLite los campos DateTime."""
    if dt.tzinfo:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _ultima_imagen(session: Session, trampa_id: int, tarjeta_id: int | None,
                   hasta: datetime | None = None) -> Imagen | None:
    """Última imagen procesada de esa trampa (y tarjeta) hasta ese momento.

    Es la que representa el estado de la tarjeta: como los insectos siguen pegados, la última
    foto ya contiene a todos los anteriores. Por eso NO se suman las fotos.
    """
    q = select(Imagen).where(Imagen.trampa_id == trampa_id,
                             Imagen.estado == "procesada",
                             Imagen.es_demo == False)  # noqa: E712
    if tarjeta_id is not None:
        q = q.where(Imagen.tarjeta_id == tarjeta_id)
    if hasta is not None:
        # SQLModel exige zona en el parámetro; al leer SQLite devuelve naive UTC.
        q = q.where(Imagen.capturada <= _naive(hasta).replace(tzinfo=timezone.utc))
    return session.exec(q.order_by(Imagen.capturada.desc()).limit(1)).first()


def conteo_actual(session: Session, trampa_id: int) -> int | None:
    """Insectos pegados AHORA en la tarjeta de esa trampa. `None` si no hay ninguna foto."""
    im = _ultima_imagen(session, trampa_id, None)
    return im.num_insectos if im else None


def nuevas(session: Session, trampa_id: int, desde: datetime,
           hasta: datetime | None = None) -> int | None:
    """Capturas NUEVAS de esa trampa en la ventana. `None` si no hay datos suficientes.

    Se trocea por tarjeta (ver la explicación del módulo). Una trampa sin ninguna tarjeta
    registrada se trata como un solo periodo: es peor, pero es lo único que se sabe de ella.
    """
    hasta = hasta or datetime.now(timezone.utc)
    d, h = _naive(desde), _naive(hasta)

    tarjetas = session.exec(
        select(Tarjeta).where(Tarjeta.trampa_id == trampa_id)
        .order_by(Tarjeta.colocada)).all()
    if not tarjetas:
        # Sin tarjetas: una única serie. La resta sigue siendo mucho mejor que la suma, pero
        # un cambio de cartón no registrado aparecería como una caída.
        fin = _ultima_imagen(session, trampa_id, None, h)
        if fin is None:
            return None
        ini = _ultima_imagen(session, trampa_id, None, d)
        return fin.num_insectos - (ini.num_insectos if ini else 0)

    total = 0
    hubo_datos = False
    for t in tarjetas:
        colocada = _naive(t.colocada)
        retirada = _naive(t.retirada) if t.retirada else h
        # ¿solapa esta tarjeta con la ventana?
        ini_periodo = max(d, colocada)
        fin_periodo = min(h, retirada)
        if fin_periodo <= ini_periodo:
            continue
        fin = _ultima_imagen(session, trampa_id, t.id, fin_periodo)
        if fin is None:
            continue
        hubo_datos = True
        if colocada >= d:
            base = 0            # la tarjeta se puso dentro de la ventana: empezó limpia
        else:
            im = _ultima_imagen(session, trampa_id, t.id, d)
            base = im.num_insectos if im else 0
        total += fin.num_insectos - base

    return total if hubo_datos else None


def nuevas_varias(session: Session, trap_ids: list[int], dias: float = 1.0) -> int:
    """Capturas nuevas sumadas sobre varias trampas. Sumar TRAMPAS sí es correcto."""
    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    total = 0
    for tid in trap_ids:
        n = nuevas(session, tid, desde)
        if n is not None:
            total += n
    return max(0, total)        # «−3 capturas nuevas» no significa nada


def insectos_en_tarjetas(session: Session, trap_ids: list[int]) -> int:
    """Insectos pegados ahora mismo en el conjunto de tarjetas. Es un acumulado, no un ritmo."""
    return sum((conteo_actual(session, tid) or 0) for tid in trap_ids)


def especies_en_tarjetas(session: Session, trap_ids: list[int]) -> dict[str, int]:
    """Reparto por especie de lo que hay AHORA en las tarjetas.

    Se cuentan las detecciones de la **última** foto de cada trampa, no las de todas. Contarlas
    todas multiplicaba cada bicho por el número de capturas, igual que el conteo total.
    """
    out: dict[str, int] = {}
    for tid in trap_ids:
        im = _ultima_imagen(session, tid, None)
        if im is None:
            continue
        filas = session.exec(
            select(Deteccion.clase, func.count()).where(Deteccion.imagen_id == im.id)
            .group_by(Deteccion.clase)).all()
        for clase, n in filas:
            if gestionada(clase):
                out[clase] = out.get(clase, 0) + n
    return out
