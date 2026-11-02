"""Endurecimiento tras publicar la API en entomira.com (mentoría con Codex, 2026-09-30)."""
from __future__ import annotations

from app.core import auth as auth_core
from app.core import config
from app.core.red import ip_de_camara, ip_valida


def test_solo_se_contacta_una_ip_de_lan():
    assert ip_de_camara("192.168.1.147") == "192.168.1.147"
    for mala in ("127.0.0.1", "10.77.0.1", "169.254.169.254", "8.8.8.8", "100.101.1.2",
                 "192.168.1.1", "192.168.1.255", "10.0.0.5", "172.16.0.9",
                 "::1", "localhost", "192.168.1.1:81/x", "", None):
        assert ip_de_camara(mala) is None, mala
    assert ip_valida("no es una ip") is None and ip_valida(" 10.0.0.5 ") == "10.0.0.5"


def test_login_bloquea_tras_demasiados_fallos(cliente):
    auth_core._fallos.clear()
    try:
        for _ in range(auth_core.FALLOS_MAX):
            r = cliente.post("/api/v1/auth/login", json={"username": "admin", "password": "mala"})
            assert r.status_code == 401
        r = cliente.post("/api/v1/auth/login", json={"username": "admin", "password": "secreto"})
        assert r.status_code == 429            # bloqueada aunque ahora acierte
        r = cliente.get("/api/v1/traps", auth=("admin", "secreto"))
        assert r.status_code == 429            # también por Basic Auth
    finally:
        auth_core._fallos.clear()


def test_login_correcto_no_cuenta_como_fallo(cliente):
    auth_core._fallos.clear()
    r = cliente.post("/api/v1/auth/login", json={"username": "admin", "password": "secreto"})
    assert r.status_code == 200 and not auth_core._fallos


def test_sin_documentacion_publica(cliente):
    # El SPA puede devolver su index.html para rutas desconocidas; lo que no puede salir es
    # Swagger ni el esquema.
    for ruta in ("/docs", "/redoc", "/openapi.json"):
        r = cliente.get(ruta)
        assert '"openapi"' not in r.text and "swagger-ui" not in r.text.lower(), ruta


def test_claves_de_ejemplo_se_detectan(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PASS", "")
    monkeypatch.setattr(config, "DEVICE_API_KEY", "cambia-esta-clave-de-dispositivos")
    monkeypatch.setattr(config, "WORKER_API_KEY", "   ")
    assert set(config.claves_inseguras()) == {"ADMIN_PASS", "DEVICE_API_KEY", "WORKER_API_KEY"}


def test_sin_admin_pass_no_hay_admin_anonimo(cliente, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PASS", "")
    monkeypatch.setattr(config, "MODO_LOCAL", False)
    assert cliente.get("/api/v1/traps").status_code == 401
