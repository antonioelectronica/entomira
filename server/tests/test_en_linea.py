"""Una trampa que se calla tiene que avisar a la web, aunque no haya pasado nada más."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from conftest import DEVICE_KEY


def test_una_trampa_que_se_calla_anuncia_el_cambio(cliente):
    from app.core.db import engine
    from app.models.models import Trampa
    from app.services import cambios as cambios_svc
    from app.services import en_linea as en_linea_svc

    r = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY},
                     json={"device_id": "zero-CALLA1", "red": "wifi"})
    assert r.status_code == 200

    en_linea_svc._reiniciar_para_pruebas()
    assert en_linea_svc.revisar() is False          # la primera vez solo toma nota
    v0 = cambios_svc.estado()["v"]

    # Nada ha cambiado: no se anuncia nada (no repintar todas las pestañas por gusto).
    en_linea_svc._ultima_revision = 0.0
    assert en_linea_svc.revisar() is False
    assert cambios_svc.estado()["v"] == v0

    # Se calla: su último contacto queda muy atrás, y NADIE toca nada más.
    with Session(engine) as s:
        t = s.exec(select(Trampa).where(Trampa.device_id == "zero-CALLA1")).one()
        t.ultimo_visto = datetime.now(timezone.utc) - timedelta(days=1)
        s.add(t)
        s.commit()
    en_linea_svc._ultima_revision = 0.0
    assert en_linea_svc.revisar() is True
    assert cambios_svc.estado()["v"] > v0


def test_no_revisa_en_cada_sondeo(cliente):
    """`/cambios` se pide cada 4 s por pestaña: la consulta va como mucho cada 30 s."""
    from app.services import en_linea as en_linea_svc
    en_linea_svc._reiniciar_para_pruebas()
    en_linea_svc.revisar()
    assert en_linea_svc.revisar() is False          # recién revisado: ni mira la base
    assert cliente.get("/api/v1/cambios").status_code in (200, 401)
