"""
Pruebas de lo añadido al arreglar los puntos débiles de la mentoría.

Cada una cubre una funcionalidad nueva **y** la razón por la que existe, para que si alguien
la simplifica en el futuro sepa qué se pierde.
"""
from __future__ import annotations

from conftest import ADMIN, DEVICE_KEY


# --------------------------------------------------------------------- avisos
def test_avisos_responde_una_lista(cliente):
    d = cliente.get("/api/v1/avisos", auth=ADMIN).json()
    assert isinstance(d["avisos"], list)
    assert d["total"] == len(d["avisos"])
    assert d["criticos"] == sum(1 for a in d["avisos"] if a["nivel"] == "critico")


def test_cada_aviso_dice_que_pasa_y_que_hacer(cliente, trampa):
    """Un aviso sin acción concreta se aprende a ignorar, y entonces no avisa de nada."""
    from app.services import estado as estado_svc
    estado_svc.marcar_disco_lleno(12.0)
    try:
        avisos = cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]
        disco = [a for a in avisos if "espacio" in a["titulo"].lower()]
        assert disco, "con el disco marcado lleno tiene que salir el aviso"
        a = disco[0]
        assert a["nivel"] == "critico"
        for campo in ("titulo", "detalle", "accion"):
            assert a[campo].strip(), f"el aviso no puede tener {campo} vacío"
    finally:
        estado_svc.marcar_disco_ok()


def test_al_liberarse_el_disco_el_aviso_desaparece(cliente):
    from app.services import estado as estado_svc
    estado_svc.marcar_disco_lleno(5.0)
    estado_svc.marcar_disco_ok()
    avisos = cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]
    assert not [a for a in avisos if "RECHAZANDO" in a["titulo"]]


def test_bateria_baja_genera_aviso(cliente, jpeg):
    cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                 files={"image": ("c.jpg", jpeg, "image/jpeg")},
                 data={"device_id": "esp32-SINBATERIA", "bateria": "9", "voltaje": "3.3"})
    avisos = cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]
    assert [a for a in avisos if "Batería crítica" in a["titulo"]]


# --------------------------------------------------------------------- perfil de config
def test_guardar_y_restaurar_el_perfil(cliente, trampa):
    buena = {"intervalo_seg": 600, "ev": -0.5, "calidad_jpeg": 95,
             "brillo": 0.1, "contraste": 1.25, "saturacion": 0.75,
             "enfoque": "manual", "lente_dioptrias": 3.3, "balance": "daylight"}
    cambio = cliente.put(f"/api/v1/traps/{trampa}/config", auth=ADMIN, json=buena)
    assert cambio.status_code == 200
    p = cliente.post(f"/api/v1/traps/{trampa}/perfil", auth=ADMIN,
                     json={"nota": "medido con foco fijo"}).json()
    assert all(p[k] == v for k, v in buena.items()) and p["nota"]

    # se estropea la configuración a mano, como pasó de verdad durante las pruebas
    cambio = cliente.put(f"/api/v1/traps/{trampa}/config", auth=ADMIN,
                         json={"intervalo_seg": 15, "ev": -2, "enfoque": "continuous",
                               "lente_dioptrias": 0, "balance": "auto", "calidad_jpeg": 60,
                               "brillo": 0, "contraste": 1, "saturacion": 1})
    assert cambio.status_code == 200
    r = cliente.post(f"/api/v1/traps/{trampa}/perfil/restaurar", auth=ADMIN).json()
    assert r["restaurados"] == len(buena)
    cfg = cliente.get(f"/api/v1/traps/{trampa}", auth=ADMIN).json()["config"]
    assert all(cfg[k] == v for k, v in buena.items())


def test_restaurar_sin_perfil_guardado_da_404(cliente, jpeg):
    cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                 files={"image": ("c.jpg", jpeg, "image/jpeg")},
                 data={"device_id": "esp32-SINPERFIL"})
    t = [x for x in cliente.get("/api/v1/traps", auth=ADMIN).json()
         if x["device_id"] == "esp32-SINPERFIL"][0]
    assert cliente.post(f"/api/v1/traps/{t['id']}/perfil/restaurar",
                        auth=ADMIN).status_code == 404


def test_el_perfil_solo_lo_guarda_el_admin(cliente, trampa):
    assert cliente.post(f"/api/v1/traps/{trampa}/perfil", json={"nota": "x"}).status_code == 401


# --------------------------------------------------------------------- copia de seguridad
def test_la_copia_de_la_bd_se_descarga_y_es_una_bd(cliente, trampa):
    r = cliente.get("/api/v1/backup/db", auth=ADMIN)
    assert r.status_code == 200
    # Cabecera de fichero SQLite: si copiásemos el .db en caliente podría venir incompleto;
    # por eso se usa VACUUM INTO.
    assert r.content[:16] == b"SQLite format 3\x00"
    assert len(r.content) > 1000


def test_la_copia_no_es_publica(cliente):
    assert cliente.get("/api/v1/backup/db").status_code == 401


# --------------------------------------------------------------------- índices
def test_existen_los_indices_de_la_galeria(cliente):
    """Sin índice en `capturada`, cada página de la galería ordena la tabla entera."""
    from sqlalchemy import text
    from app.core.db import engine
    with engine.connect() as c:
        nombres = {r[0] for r in c.execute(
            text("SELECT name FROM sqlite_master WHERE type='index'"))}
    assert "ix_imagen_capturada" in nombres
    assert "ix_imagen_es_demo" in nombres
