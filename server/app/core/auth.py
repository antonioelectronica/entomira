"""Middleware de acceso al panel: sesión por cookie (login propio) + Basic Auth/API-Key.

- El **shell del SPA** (todo lo que no sea `/api/v1`) es público → la web puede mostrar su
  propia página de login. Los **datos** (`/api/v1/*`) requieren sesión.
- Sesión de navegador: cookie firmada `entomira_session` (login en `/api/v1/auth/login`).
- Compatibilidad: **Basic Auth** (app móvil, curl, scripts) y **X-API-Key** (PCB/Worker).
- Se responde 401 **sin** `WWW-Authenticate` para no disparar el diálogo nativo del navegador.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from sqlmodel import Session, select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from . import config, passwords
from .db import engine

COOKIE_NAME = "entomira_session"

# Fuerza bruta: con la API en internet, cada IP tiene FALLOS_MAX intentos fallidos por ventana.
# La IP es la real del cliente: uvicorn solo acepta X-Forwarded-For del VPS (10.77.0.1).
FALLOS_MAX = 10
FALLOS_VENTANA_S = 15 * 60
_fallos: dict[str, list[float]] = {}


def ip_cliente(request: Request) -> str:
    return request.client.host if request.client else "?"


def bloqueada(ip: str) -> bool:
    ahora = time.monotonic()
    marcas = [t for t in _fallos.get(ip, ()) if ahora - t < FALLOS_VENTANA_S]
    if marcas:
        _fallos[ip] = marcas
    else:
        _fallos.pop(ip, None)
    return len(marcas) >= FALLOS_MAX


def anotar_fallo(ip: str) -> None:
    _fallos.setdefault(ip, []).append(time.monotonic())
    if len(_fallos) > 5000:                     # que un barrido de IPs no llene la memoria
        for vieja in list(_fallos)[:1000]:
            _fallos.pop(vieja, None)


def _demasiados() -> Response:
    return Response(status_code=429, media_type="application/json",
                    content='{"detail":"Demasiados intentos fallidos; espera unos minutos"}')
# Secreto de firma estable por despliegue (no en código; deriva de las claves del .env).
_SECRET = hashlib.sha256(
    (config.DEVICE_API_KEY + config.ADMIN_PASS + "entomira-session-v1").encode()).digest()

# Endpoints de dispositivo/worker (X-API-Key). Rutas públicas de API (sin sesión).
_KEYED_PREFIXES = ("/api/v1/ingest", "/api/v1/inference", "/api/v1/telemetry")
_API_PUBLIC = ("/api/v1/auth/login", "/api/v1/client")


def make_session(username: str) -> str:
    sig = hmac.new(_SECRET, username.encode(), hashlib.sha256).hexdigest()
    return f"{username}|{sig}"


def _username_from_cookie(val: str) -> str | None:
    try:
        username, sig = val.rsplit("|", 1)
        exp = hmac.new(_SECRET, username.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, exp):
            return username
    except Exception:
        pass
    return None


def authenticate_user(user: str, pwd: str):
    """Valida credenciales (admin del .env o usuario de BD). Devuelve dict o None."""
    if config.ADMIN_PASS and secrets.compare_digest(user, config.ADMIN_USER) \
            and secrets.compare_digest(pwd, config.ADMIN_PASS):
        return {"id": None, "username": user, "rol": "admin", "invernadero_id": None,
                "nombre": user}
    from ..models.models import Usuario
    with Session(engine) as s:
        u = s.exec(select(Usuario).where(Usuario.username == user)).first()
        if u and u.activo and passwords.verify_password(pwd, u.password_hash):
            return {"id": u.id, "username": u.username, "rol": u.rol,
                    "invernadero_id": u.invernadero_id, "nombre": u.nombre}
    return None


def _user_by_username(username: str):
    """Reconstruye el dict de usuario desde el username (para la cookie de sesión)."""
    if config.ADMIN_USER and username == config.ADMIN_USER and config.ADMIN_PASS:
        return {"id": None, "username": username, "rol": "admin", "invernadero_id": None,
                "nombre": username}
    from ..models.models import Usuario
    with Session(engine) as s:
        u = s.exec(select(Usuario).where(Usuario.username == username)).first()
        if u and u.activo:
            return {"id": u.id, "username": u.username, "rol": u.rol,
                    "invernadero_id": u.invernadero_id, "nombre": u.nombre}
    return None


def _basic_creds(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Basic "):
        return None
    try:
        dec = base64.b64decode(auth[6:]).decode("utf-8")
        user, _, pwd = dec.partition(":")
        return user, pwd
    except Exception:
        return None


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.user = None
        path = request.url.path

        # 1) Shell del SPA y estáticos (todo lo que NO es API) → público.
        #    Aun así, si hay cookie válida, adjuntamos el usuario (para /cliente no hace falta).
        if not path.startswith("/api/v1"):
            cookie = request.cookies.get(COOKIE_NAME)
            if cookie:
                un = _username_from_cookie(cookie)
                if un:
                    request.state.user = _user_by_username(un)
            return await call_next(request)

        # 2) Endpoints públicos de API: login y vista de cliente.
        if any(path.startswith(p) for p in _API_PUBLIC):
            return await call_next(request)

        # 3) Dispositivos/worker con X-API-Key.
        key = request.headers.get("x-api-key", "")
        keyed = key and (secrets.compare_digest(key, config.DEVICE_API_KEY)
                         or secrets.compare_digest(key, config.WORKER_API_KEY))

        # 4) Sesión por cookie (navegador).
        cookie = request.cookies.get(COOKIE_NAME)
        if cookie:
            un = _username_from_cookie(cookie)
            user = _user_by_username(un) if un else None
            if user:
                request.state.user = user
                return await call_next(request)

        # 5) Basic Auth (app móvil, curl, scripts).
        creds = _basic_creds(request)
        if creds:
            ip = ip_cliente(request)
            if bloqueada(ip):
                return _demasiados()
            user = authenticate_user(*creds)
            if user:
                request.state.user = user
                return await call_next(request)
            anotar_fallo(ip)

        if keyed:
            return await call_next(request)
        if any(path.startswith(p) for p in _KEYED_PREFIXES) and key:
            return await call_next(request)
        if path.endswith("/config") and request.method == "GET" and key:
            return await call_next(request)

        # Sin protección configurada: admin anónimo SOLO en modo local explícito.
        if not config.ADMIN_PASS and config.MODO_LOCAL:
            request.state.user = {"id": None, "username": "local", "rol": "admin",
                                  "invernadero_id": None, "nombre": "local"}
            return await call_next(request)

        # 401 SIN WWW-Authenticate → el SPA muestra su propia página de login (no el popup).
        return Response(status_code=401, content='{"detail":"No autenticado"}',
                        media_type="application/json")
