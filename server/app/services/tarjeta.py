"""
Tarjetas adhesivas: el periodo de exposición al que pertenece cada conteo.

**El problema que resuelve.** Una trampa cromotrópica acumula. `num_insectos` es una integral,
no una medida instantánea: dice cuántos se han pegado desde que se puso el cartón. Sin saber
cuándo se puso, ese número no es comparable ni entre trampas ni entre semanas, y la caída a
cero al cambiar la tarjeta se lee como que la plaga ha desaparecido.

**La magnitud útil** —la que gobierna una decisión de tratamiento— es **capturas por día de
exposición**, que es la derivada de la curva de acumulación. Con la fecha de colocación sale
gratis.

**Regla de oro de este módulo:** una trampa tiene como mucho UNA tarjeta puesta (`retirada is
None`). Todo lo demás se deriva de ahí.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, func, select

from ..models.models import Imagen, Tarjeta


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite devuelve las fechas sin zona; compararlas con `now(utc)` revienta."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def actual(session: Session, trampa_id: int) -> Tarjeta:
    """La tarjeta puesta ahora mismo en esa trampa, creándola si no hay ninguna.

    Al crear la PRIMERA tarjeta de una trampa que ya tiene fotos, se fecha en la captura más
    antigua y se le adjudican todas las fotos sueltas. Así el histórico existente no se queda
    huérfano: es una suposición, pero es la única compatible con lo que se sabe (nadie ha
    cambiado el cartón desde que la trampa empezó a subir fotos), y es preferible a partir el
    corpus en un «antes» sin tarjeta y un «después» con ella.
    """
    t = session.exec(
        select(Tarjeta).where(Tarjeta.trampa_id == trampa_id, Tarjeta.retirada.is_(None))
        .order_by(Tarjeta.colocada.desc())).first()
    if t:
        return t

    primera = session.exec(
        select(func.min(Imagen.capturada)).where(Imagen.trampa_id == trampa_id)).one()
    t = Tarjeta(trampa_id=trampa_id,
                colocada=_aware(primera) or datetime.now(timezone.utc),
                nota="Creada automáticamente al empezar a llevar el registro de tarjetas.")
    session.add(t)
    session.commit()
    session.refresh(t)
    session.execute(
        Imagen.__table__.update()
        .where(Imagen.__table__.c.trampa_id == trampa_id,
               Imagen.__table__.c.tarjeta_id.is_(None))
        .values(tarjeta_id=t.id))
    session.commit()
    return t


def nueva(session: Session, trampa_id: int, nota: str = "") -> Tarjeta:
    """Retira la tarjeta puesta y coloca una nueva. Es el botón «tarjeta nueva».

    Se guarda el `conteo_final` antes de cerrar: es el resultado de todo el periodo y, si se
    borran las fotos por la purga, sería lo único que quedaría de él.
    """
    ahora = datetime.now(timezone.utc)
    vieja = session.exec(
        select(Tarjeta).where(Tarjeta.trampa_id == trampa_id,
                              Tarjeta.retirada.is_(None))).first()
    if vieja:
        vieja.retirada = ahora
        vieja.conteo_final = _ultimo_conteo(session, vieja.id)
        session.add(vieja)
        session.commit()

    t = Tarjeta(trampa_id=trampa_id, colocada=ahora, nota=nota)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


class FechaNoValida(ValueError):
    """La fecha de colocación corregida contradice lo que ya se sabe."""


def corregir_colocada(session: Session, trampa_id: int, colocada: datetime) -> Tarjeta:
    """Corrige cuándo se puso la tarjeta ACTUAL (p. ej. la creada automáticamente).

    Solo mueve la fecha; no reparte fotos entre tarjetas. Por eso se niega si quedaría alguna
    foto de esta tarjeta capturada ANTES de la nueva fecha (contaría insectos de otro cartón), si
    la fecha es futura o si pisa el periodo de la tarjeta anterior.
    """
    colocada = _aware(colocada).astimezone(timezone.utc)
    if colocada > datetime.now(timezone.utc):
        raise FechaNoValida("La fecha de colocación no puede ser futura.")
    t = actual(session, trampa_id)
    previa = session.exec(
        select(func.max(Tarjeta.retirada)).where(Tarjeta.trampa_id == trampa_id,
                                                 Tarjeta.id != t.id)).one()
    if previa is not None and colocada < _aware(previa):
        raise FechaNoValida("Es anterior a la retirada de la tarjeta previa.")
    antes = session.exec(
        select(func.count()).select_from(Imagen)
        .where(Imagen.tarjeta_id == t.id,
               Imagen.capturada < colocada.replace(tzinfo=None))).one()
    if antes:
        raise FechaNoValida(f"{antes} foto(s) de esta tarjeta son anteriores a esa fecha.")
    t.colocada = colocada
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _ultimo_conteo(session: Session, tarjeta_id: int) -> int | None:
    """Conteo de la última foto PROCESADA de esa tarjeta.

    Es el acumulado del periodo: como los insectos siguen pegados, la última foto ya contiene
    a todos los anteriores. Sumar las fotos multiplicaría los mismos insectos por el número
    de capturas.
    """
    return session.exec(
        select(Imagen.num_insectos)
        .where(Imagen.tarjeta_id == tarjeta_id, Imagen.estado == "procesada")
        .order_by(Imagen.capturada.desc()).limit(1)).first()


def resumen(session: Session, t: Tarjeta) -> dict:
    """Lo que hay que saber de una tarjeta: cuánto lleva puesta y a qué ritmo captura."""
    fin = _aware(t.retirada) or datetime.now(timezone.utc)
    dias = max((fin - _aware(t.colocada)).total_seconds() / 86400.0, 0.0)
    conteo = t.conteo_final if t.retirada else _ultimo_conteo(session, t.id)
    n_fotos = session.exec(select(func.count()).select_from(Imagen)
                           .where(Imagen.tarjeta_id == t.id)).one()
    # Por debajo de medio día el cociente se dispara (30 insectos en 2 h serían 360/día) y
    # daría una alarma falsa nada más cambiar el cartón. Mejor no dar la cifra que darla mal.
    por_dia = round(conteo / dias, 2) if (conteo is not None and dias >= 0.5) else None
    return {
        "id": t.id, "trampa_id": t.trampa_id,
        "colocada": t.colocada, "retirada": t.retirada,
        "puesta": t.retirada is None,
        "dias_expuesta": round(dias, 2),
        "conteo": conteo,
        "capturas_por_dia": por_dia,
        "num_imagenes": n_fotos,
        "nota": t.nota,
    }


def historial(session: Session, trampa_id: int, limit: int = 30) -> list[dict]:
    filas = session.exec(
        select(Tarjeta).where(Tarjeta.trampa_id == trampa_id)
        .order_by(Tarjeta.colocada.desc()).limit(limit)).all()
    return [resumen(session, t) for t in filas]
