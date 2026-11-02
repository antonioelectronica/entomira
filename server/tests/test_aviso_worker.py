# -*- coding: utf-8 -*-
"""Guardia del aviso «el worker de IA no está analizando las fotos».

**Por qué existe.** El 2026-09-19 el worker llevaba horas parado: 317 imágenes en `pendiente`
y CERO procesadas. La purga borra a los 7 días lo que sigue sin procesar, así que el sistema
estaba tirando las fotos **sin haberlas mirado nunca** — sin un error, sin un 5xx y sin que el
disco se resintiera. El dato para detectarlo ya existía (`/health/worker`); lo que faltaba era
que alguien lo mirara y lo convirtiera en un aviso visible.

Se comprueban las dos mitades, porque un aviso que salta siempre se aprende a ignorar y eso es
lo mismo que no tenerlo.
"""
from __future__ import annotations

from conftest import ADMIN, DEVICE_KEY

from sqlmodel import Session, select

from app.core.db import engine
from app.models.models import Imagen
from app.services import estado as estado_svc


def _avisos_worker(cliente):
    r = cliente.get("/api/v1/avisos", auth=ADMIN).json()
    return [a for a in r["avisos"] if "worker" in a["titulo"].lower()]


def _vaciar_cola():
    """Deja la cola a cero sin borrar nada.

    La base de pruebas es de sesión y la comparten todos los ficheros, así que otro test
    puede haber dejado una imagen en `pendiente`. Marcarlas como procesadas es mas honesto
    que borrarlas: no le quita las imagenes a quien venga despues.
    """
    with Session(engine) as s:
        for im in s.exec(select(Imagen).where(Imagen.estado == "pendiente")).all():
            im.estado = "procesada"
            s.add(im)
        s.commit()


def test_sin_fotos_pendientes_no_se_avisa_del_worker(cliente, trampa):
    """Un worker apagado y sin trabajo no es un problema todavía: no se molesta al usuario."""
    _vaciar_cola()
    estado_svc._worker["visto"] = None          # como si no hubiera aparecido nunca
    assert _avisos_worker(cliente) == [], "avisa del worker sin haber nada que procesar"


def test_con_fotos_esperando_y_el_worker_ausente_SI_se_avisa(cliente, jpeg, trampa):
    """Con fotos en cola y el worker sin aparecer, tiene que verse. Es el caso real."""
    cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                 files={"image": ("f.jpg", jpeg, "image/jpeg")},
                 data={"device_id": "esp32-PRUEBA01"})
    estado_svc._worker["visto"] = None

    avisos = _avisos_worker(cliente)
    assert avisos, "hay fotos esperando y el worker no aparece, y nadie avisa"
    a = avisos[0]
    assert a["nivel"] == "critico"
    # El aviso tiene que decir QUÉ hacer: uno vago se ignora igual que no tenerlo.
    assert "run_worker_consola" in a["accion"]
    # La purga por edad está apagada (2026-10-23): no puede amenazar con un borrado que no ocurre.
    if estado_svc.PURGA_POR_EDAD:
        assert str(estado_svc.PURGA_SIN_PROCESAR_DIAS) in a["accion"], \
            "no dice que la purga las borrará sin mirarlas, que es lo que lo hace urgente"
    else:
        assert "purga" not in a["accion"], "avisa de una purga que está apagada"


def test_si_el_worker_acaba_de_pasar_no_se_avisa(cliente, jpeg, trampa):
    """Recién visto, no se avisa aunque haya cola: está trabajando, la cola es normal."""
    cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                 files={"image": ("f.jpg", jpeg, "image/jpeg")},
                 data={"device_id": "esp32-PRUEBA01"})
    estado_svc.marcar_worker_visto()
    assert _avisos_worker(cliente) == [], "avisa de un worker que acaba de pedir trabajo"
