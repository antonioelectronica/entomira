"""Sesión del usuario: login por cookie + /auth/me."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlmodel import Session

from ..core import auth as auth_core
from ..core.db import get_session
from ..core.deps import current_user
from ..models.models import Invernadero

router = APIRouter(prefix="/api/v1", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, request: Request):
    ip = auth_core.ip_cliente(request)
    if auth_core.bloqueada(ip):
        return JSONResponse({"ok": False, "detail": "Demasiados intentos fallidos; espera unos minutos"},
                            status_code=429)
    user = auth_core.authenticate_user(body.username.strip(), body.password)
    if not user:
        auth_core.anotar_fallo(ip)
        return JSONResponse({"ok": False, "detail": "Usuario o contraseña incorrectos"},
                            status_code=401)
    resp = JSONResponse({"ok": True, "username": user["username"], "rol": user["rol"],
                         "nombre": user.get("nombre") or user["username"]})
    resp.set_cookie(auth_core.COOKIE_NAME, auth_core.make_session(user["username"]),
                    httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30, path="/",
                    secure=request.url.scheme == "https")
    return resp


@router.post("/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth_core.COOKIE_NAME, path="/")
    return resp


@router.get("/auth/me")
def me(request: Request, session: Session = Depends(get_session)):
    user = current_user(request)
    if not user:
        return {"authenticated": False}
    gh_nombre = None
    if user.get("invernadero_id"):
        gh = session.get(Invernadero, user["invernadero_id"])
        gh_nombre = gh.nombre if gh else None
    return {
        "authenticated": True,
        "username": user["username"],
        "rol": user["rol"],
        "nombre": user.get("nombre") or user["username"],
        "invernadero_id": user.get("invernadero_id"),
        "invernadero_nombre": gh_nombre,
    }
