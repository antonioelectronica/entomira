"""Dependencias de FastAPI para el control de acceso por rol."""
from __future__ import annotations

from fastapi import HTTPException, Request
from sqlmodel import Session, select

from ..models.models import Trampa


def current_user(request: Request) -> dict | None:
    """Usuario autenticado (lo puso BasicAuthMiddleware) o None."""
    return getattr(request.state, "user", None)


def is_admin(user: dict | None) -> bool:
    return bool(user) and user.get("rol") == "admin"


def is_farmer(user: dict | None) -> bool:
    return bool(user) and user.get("rol") == "agricultor"


def owned_trap_ids(session: Session, user: dict) -> list[int]:
    """IDs de trampas que pertenecen al agricultor (sus subidas desde el móvil)."""
    return list(session.exec(
        select(Trampa.id).where(Trampa.owner_id == user["id"])).all())


def visible_trap_ids(session: Session, user: dict) -> list[int]:
    """Trampas que el agricultor puede VER: las suyas (subidas del móvil) y las de su invernadero.

    Solo para lectura (fotos, conteos, ficha). El control --directo, config, borrar, subir como
    la trampa-- sigue siendo del dueño o del admin. Bug del 2026-10-16: con solo `owner_id`, el
    agricultor no veía la trampa de campo de su propio invernadero.
    """
    from sqlalchemy import or_
    cond = Trampa.owner_id == user["id"]             # lo suyo, siempre (hallazgo de Codex)
    gh = user.get("invernadero_id")
    if gh is not None:                    # sin invernadero no se concede nada por coincidencia
        cond = or_(cond, _de_invernadero(gh))
    return list(session.exec(select(Trampa.id).where(cond)).all())


def _de_invernadero(gh_id: int):
    """Trampas reales de un invernadero: ni la demo ni la de subida manual del PC."""
    from sqlalchemy import and_
    from ..services import traps as traps_svc
    from ..services.demo import DEMO_DEVICE
    return and_(Trampa.invernadero_id == gh_id, Trampa.device_id != DEMO_DEVICE,
                Trampa.device_id != traps_svc.MANUAL_UPLOAD_DEVICE)


def greenhouse_trap_ids(session: Session, gh_id: int) -> list[int]:
    """Las trampas que enseña el enlace de cliente de ese invernadero: las mismas que ve su
    agricultor por ser de su invernadero (petición de Antonio, 2026-10-16)."""
    return list(session.exec(select(Trampa.id).where(_de_invernadero(gh_id))).all())


def require_admin(request: Request) -> dict:
    user = current_user(request)
    if not is_admin(user):
        raise HTTPException(403, "Solo el administrador puede hacer esto.")
    return user
