"""Gestión de usuarios (solo admin): alta/baja de agricultores."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from ..core import passwords
from ..core.db import get_session
from ..core.deps import require_admin
from ..models.models import Invernadero, Trampa, Usuario

router = APIRouter(prefix="/api/v1", tags=["usuarios"])


class UserIn(BaseModel):
    username: str
    password: str
    rol: str = "agricultor"          # admin | agricultor
    invernadero_id: int | None = None
    nombre: str = ""


class UserOut(BaseModel):
    id: int
    username: str
    rol: str
    invernadero_id: int | None
    invernadero_nombre: str | None = None
    nombre: str
    activo: bool
    device_id: str | None = None      # identificador del móvil del usuario (si ya ha subido fotos)
    num_fotos: int = 0


def _out(session: Session, u: Usuario) -> UserOut:
    gh = session.get(Invernadero, u.invernadero_id) if u.invernadero_id else None
    trap = session.exec(select(Trampa).where(Trampa.owner_id == u.id)).first()
    n_fotos = 0
    if trap:
        from ..models.models import Imagen
        n_fotos = session.exec(
            select(func.count()).select_from(Imagen).where(Imagen.trampa_id == trap.id)).one()
    return UserOut(id=u.id, username=u.username, rol=u.rol,
                   invernadero_id=u.invernadero_id,
                   invernadero_nombre=gh.nombre if gh else None,
                   nombre=u.nombre, activo=u.activo,
                   device_id=trap.device_id if trap else None, num_fotos=n_fotos)


@router.get("/users", response_model=list[UserOut], dependencies=[Depends(require_admin)])
def list_users(session: Session = Depends(get_session)):
    return [_out(session, u) for u in session.exec(select(Usuario).order_by(Usuario.id)).all()]


@router.post("/users", response_model=UserOut, status_code=201,
             dependencies=[Depends(require_admin)])
def create_user(body: UserIn, session: Session = Depends(get_session)):
    if session.exec(select(Usuario).where(Usuario.username == body.username)).first():
        raise HTTPException(409, "Ese nombre de usuario ya existe.")
    u = Usuario(username=body.username, password_hash=passwords.hash_password(body.password),
                rol=body.rol if body.rol in ("admin", "agricultor") else "agricultor",
                invernadero_id=body.invernadero_id, nombre=body.nombre)
    session.add(u)
    session.commit()
    session.refresh(u)
    return _out(session, u)


class UserPatch(BaseModel):
    """Todo opcional: se toca solo lo que venga."""
    nombre: str | None = None
    invernadero_id: int | None = None
    password: str | None = None
    activo: bool | None = None


@router.patch("/users/{user_id}", response_model=UserOut,
              dependencies=[Depends(require_admin)])
def editar_usuario(user_id: int, body: UserPatch, session: Session = Depends(get_session)):
    """Edita un agricultor: nombre, invernadero, contraseña o alta/baja.

    **Desactivar en vez de borrar** es lo que faltaba: al agricultor que deja de trabajar en
    una finca hay que quitarle el acceso, pero borrarlo se lleva por delante la trazabilidad de
    las fotos que subió. `activo=false` le cierra la puerta (`authenticate_user` ya lo
    comprueba) y deja el histórico intacto.
    """
    u = session.get(Usuario, user_id)
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    if u.rol == "admin":
        raise HTTPException(400, "El administrador no se edita desde aquí.")

    if body.nombre is not None:
        u.nombre = body.nombre.strip()
    if body.invernadero_id is not None:
        u.invernadero_id = body.invernadero_id or None
    if body.activo is not None:
        u.activo = body.activo
    if body.password:
        if len(body.password) < 4:
            raise HTTPException(422, "La contraseña es demasiado corta.")
        u.password_hash = passwords.hash_password(body.password)

    session.add(u)
    session.commit()
    session.refresh(u)
    return _out(session, u)


@router.delete("/users/{user_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_user(user_id: int, session: Session = Depends(get_session)):
    u = session.get(Usuario, user_id)
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    # Desasigna sus trampas-móvil antes de borrar (evita violar la FK owner_id → usuario).
    for t in session.exec(select(Trampa).where(Trampa.owner_id == user_id)).all():
        t.owner_id = None
        session.add(t)
    session.commit()
    session.delete(u)
    session.commit()
