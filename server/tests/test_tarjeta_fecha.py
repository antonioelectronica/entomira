"""Corregir la fecha de colocación de la tarjeta puesta (PATCH /traps/{id}/tarjeta).

Caso real (2026-10-23): la tarjeta de la trampa 10 se creó sola el 18/09 y Antonio la puso el
16/10 a las 09:45; con la fecha mala, las capturas por día salen divididas por un mes de más.
"""
from datetime import datetime, timedelta, timezone

from conftest import ADMIN, DEVICE_KEY


def _trampa_nueva(cliente, jpeg, dev):
    r = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                     files={"image": ("f.jpg", jpeg, "image/jpeg")}, data={"device_id": dev})
    assert r.status_code == 201, r.text
    return r.json()["trap_id"]


def test_se_corrige_la_fecha_sin_crear_otra_tarjeta(cliente, jpeg):
    tid = _trampa_nueva(cliente, jpeg, "esp32-TARJFECHA1")
    antes = cliente.get(f"/api/v1/traps/{tid}/tarjeta", auth=ADMIN).json()
    fecha = datetime.now(timezone.utc) - timedelta(days=3)
    r = cliente.patch(f"/api/v1/traps/{tid}/tarjeta", auth=ADMIN,
                      json={"colocada": fecha.isoformat()})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["id"] == antes["id"], "ha creado otra tarjeta en vez de corregir la puesta"
    assert 2.9 < d["dias_expuesta"] < 3.1
    assert d["num_imagenes"] == antes["num_imagenes"]
    hist = cliente.get(f"/api/v1/traps/{tid}/tarjetas", auth=ADMIN).json()["tarjetas"]
    assert len(hist) == 1


def test_no_deja_fotos_de_la_tarjeta_antes_de_su_colocacion(cliente, jpeg):
    tid = _trampa_nueva(cliente, jpeg, "esp32-TARJFECHA2")
    cliente.get(f"/api/v1/traps/{tid}/tarjeta", auth=ADMIN)
    despues_de_la_foto = datetime.now(timezone.utc) + timedelta(seconds=1)
    r = cliente.patch(f"/api/v1/traps/{tid}/tarjeta", auth=ADMIN,
                      json={"colocada": despues_de_la_foto.isoformat()})
    assert r.status_code == 409, "acepta una fecha que deja fotos de la tarjeta antes de ponerla"


def test_ni_futura_ni_sin_ser_admin(cliente, jpeg):
    tid = _trampa_nueva(cliente, jpeg, "esp32-TARJFECHA3")
    futura = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    assert cliente.patch(f"/api/v1/traps/{tid}/tarjeta", auth=ADMIN,
                         json={"colocada": futura}).status_code == 409
    pasada = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert cliente.patch(f"/api/v1/traps/{tid}/tarjeta",
                         json={"colocada": pasada}).status_code in (401, 403)
