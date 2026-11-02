"""El worker analiza 10 min después de cada despertar de las trampas en campo (17/10/2026)."""
from __future__ import annotations

from conftest import ADMIN, DEVICE_KEY


def _trampa(cliente, jpeg, dev):
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("c.jpg", jpeg, "image/jpeg")}, data={"device_id": dev})
    assert r.status_code == 201
    return r.json()["trap_id"]


def test_horas_de_analisis_son_los_despertares_de_campo_mas_diez(cliente, jpeg):
    campo = _trampa(cliente, jpeg, "zero-HORAS-CAMPO")
    banco = _trampa(cliente, jpeg, "zero-HORAS-BANCO")
    assert cliente.put(f"/api/v1/traps/{campo}/config", auth=ADMIN, json={
        "deep_sleep": 1, "horas_despertar": ["09:30", "23:55"]}).status_code == 200
    assert cliente.put(f"/api/v1/traps/{banco}/config", auth=ADMIN, json={
        "deep_sleep": 0, "horas_despertar": ["07:00"]}).status_code == 200
    horas = cliente.get("/api/v1/worker/params", auth=ADMIN).json()["horas_analisis"]
    assert "09:40" in horas and "00:05" in horas          # pasa de medianoche bien
    assert "07:10" not in horas                            # la de banco no cuenta
    assert horas == sorted(horas)
    cliente.put(f"/api/v1/traps/{campo}/config", auth=ADMIN, json={"deep_sleep": 0})


def test_la_telemetria_del_worker_cuenta_como_visto(cliente):
    """Con franjas el worker pasa horas sin pedir trabajo: su telemetría lo mantiene «visto»."""
    from app.services import estado as estado_svc
    estado_svc._worker["visto"] = None
    r = cliente.post("/api/v1/worker/stats", headers={"X-API-Key": "clave-worker-de-prueba"},
                     json={"gpu_name": "prueba"})
    assert r.status_code == 200
    assert estado_svc.worker_ausente_s() is not None and estado_svc.worker_ausente_s() < 5
    assert cliente.get("/api/v1/health/worker", auth=ADMIN).json()["online"] is True
