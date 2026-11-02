"""Reposo remoto: solo el acuse crea estado y los ids cierran las carreras."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import ADMIN, DEVICE_KEY


def _tele(cliente, dev, ack=None):
    cuerpo = {"device_id": dev, "red": "wifi"}
    if ack is not None:
        cuerpo["reposo_ack"] = ack
    r = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY}, json=cuerpo)
    assert r.status_code == 200, r.text
    return r.json()


def _trampa(cliente, dev):
    return next(t for t in cliente.get("/api/v1/traps", auth=ADMIN).json()
                if t["device_id"] == dev)


def _pedir(cliente, tid, minutos=2):
    r = cliente.post(f"/api/v1/traps/{tid}/reposo?minutos={minutos}", auth=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["reposo"]


def _ack(orden):
    return {"id": orden["id"],
            "hasta": (datetime.now(timezone.utc) + timedelta(seconds=orden["seg"]))
            .isoformat(timespec="seconds")}


def test_respuesta_perdida_no_inventa_reposo_y_reintenta(cliente):
    dev = "zero-REST-PERDIDA"
    _tele(cliente, dev)
    tid = _trampa(cliente, dev)["id"]
    orden = _pedir(cliente, tid)
    assert _tele(cliente, dev)["reposo"] == orden       # simulamos que esta respuesta se pierde
    t = _trampa(cliente, dev)
    assert t["estado"] != "reposo" and t["en_linea"] is True
    assert _tele(cliente, dev)["reposo"] == orden       # sigue pendiente hasta el acuse


def test_doble_orden_y_ultima_frente_a_apagar_reiniciar(cliente):
    dev = "zero-REST-DOBLE"
    _tele(cliente, dev)
    tid = _trampa(cliente, dev)["id"]
    vieja = _pedir(cliente, tid, 2)
    nueva = _pedir(cliente, tid, 3)
    assert nueva["id"] > vieja["id"]
    j = _tele(cliente, dev, _ack(vieja))
    assert j["reposo"] == nueva and j["reposo_ack"] is None
    assert _trampa(cliente, dev)["estado"] != "reposo"

    cliente.post(f"/api/v1/traps/{tid}/apagar", auth=ADMIN)
    j = _tele(cliente, dev)
    assert j["apaga"] is True and j["reposo"] is None and j["reinicia"] is False

    _pedir(cliente, tid, 2)
    cliente.post(f"/api/v1/traps/{tid}/reiniciar", auth=ADMIN)
    j = _tele(cliente, dev)
    assert j["reinicia"] is True and j["reposo"] is None and j["apaga"] is False


def test_acuse_marca_reposo_y_telemetria_normal_lo_cierra(cliente):
    from app.services import live as live_svc

    dev = "zero-REST-ACK"
    _tele(cliente, dev)
    tid = _trampa(cliente, dev)["id"]
    orden = _pedir(cliente, tid)
    respuesta = _tele(cliente, dev, _ack(orden))
    assert respuesta["reposo"] is None and respuesta["reposo_ack"] == orden["id"]
    t = _trampa(cliente, dev)
    assert t["estado"] == "reposo" and t["en_linea"] is False and t["reposo_hasta"]
    assert t['reposo_modo'] == 'wifi'

    _tele(cliente, dev)                         # reintento solapado: todavia no la despierta
    assert _trampa(cliente, dev)["estado"] == "reposo"
    live_svc._reposo_activo[tid]["ack_en"] -= live_svc.REHABLA_S + 1
    _tele(cliente, dev)
    t = _trampa(cliente, dev)
    assert t["estado"] != "reposo" and t["en_linea"] is True


def test_reposo_suprime_sin_fotos_y_reinicio_pierde_el_estado(cliente):
    from app.core.db import sesion_corta
    from app.models.models import ConfigCamara, Imagen
    from app.services import live as live_svc

    dev = "zero-REST-REINICIO"
    _tele(cliente, dev)
    tid = _trampa(cliente, dev)["id"]
    with sesion_corta() as s:
        cfg = s.get(ConfigCamara, tid)
        cfg.intervalo_seg = 30
        s.add(cfg)
        s.add(Imagen(trampa_id=tid, ruta_fichero="reposo-vieja.jpg", estado="procesada",
                     capturada=datetime.now(timezone.utc)
                     - timedelta(minutes=30)))
        s.commit()
    orden = _pedir(cliente, tid)
    _tele(cliente, dev, _ack(orden))
    titulos = [a["titulo"] for a in cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]]
    assert not any(dev in x and "sin subir fotos" in x for x in titulos)

    # Estado intencionadamente volatil: documenta la decision y el falso aviso aceptado.
    live_svc._reposo_activo.clear()
    assert _trampa(cliente, dev)["estado"] != "reposo"


def test_fallo_de_apagado_cierra_pausa_real(cliente):
    dev = 'zero-REST-FALLO-CORTE'
    _tele(cliente, dev)
    tid = _trampa(cliente, dev)['id']
    orden = _pedir(cliente, tid)
    ack = _ack(orden)
    ack['modo'] = 'apagado'
    _tele(cliente, dev, ack)
    assert _trampa(cliente, dev)['reposo_modo'] == 'apagado'
    _tele(cliente, dev, {'id': orden['id'], 'error': 'apagado rechazado'})
    assert _trampa(cliente, dev)['estado'] != 'reposo'


def test_limites_y_autenticacion(cliente):
    dev = "zero-REST-LIMITES"
    _tele(cliente, dev)
    tid = _trampa(cliente, dev)["id"]
    assert cliente.post(f"/api/v1/traps/{tid}/reposo?minutos=0", auth=ADMIN).status_code == 422
    assert cliente.post(f"/api/v1/traps/{tid}/reposo?minutos=1", auth=ADMIN).status_code == 422
    assert cliente.post(f"/api/v1/traps/{tid}/reposo?minutos=1441", auth=ADMIN).status_code == 422
    assert cliente.post(f"/api/v1/traps/{tid}/reposo?minutos=5").status_code == 401


def test_apagar_y_despertar_exige_corte_real_y_publica_rechazo(cliente):
    dev = "zero-APAGAR-DESPERTAR"
    _tele(cliente, dev)
    tid = _trampa(cliente, dev)["id"]
    r = cliente.post(f"/api/v1/traps/{tid}/apagar/despertar?minutos=5", auth=ADMIN)
    assert r.status_code == 200
    orden = _tele(cliente, dev)["reposo"]
    assert orden == {"id": r.json()["reposo"]["id"], "seg": 300,
                     "exigir_apagado": True}
    _tele(cliente, dev, {"id": orden["id"], "error": "despertar físico sin validar"})
    assert _tele(cliente, dev)["reposo"] is None
    assert _trampa(cliente, dev)["reposo_error"] == "despertar físico sin validar"
    assert cliente.post(f"/api/v1/traps/{tid}/apagar/despertar?minutos=2", auth=ADMIN).status_code == 422
    assert cliente.post(f"/api/v1/traps/{tid}/apagar/despertar?minutos=1", auth=ADMIN).status_code == 422


def test_despertar_en_fecha_exacta_rechaza_acuse_de_otra_fecha(cliente):
    dev = "zero-FECHA-RTC"
    _tele(cliente, dev)
    tid = _trampa(cliente, dev)["id"]
    fecha = datetime.now(timezone.utc) + timedelta(minutes=5)
    ruta = f"/api/v1/traps/{tid}/apagar/despertar-en"
    r = cliente.post(ruta, params={"cuando": fecha.isoformat()}, auth=ADMIN)
    assert r.status_code == 200, r.text
    orden = _tele(cliente, dev)["reposo"]
    assert orden["target"] == int(fecha.timestamp()) and orden["exigir_apagado"] is True
    mala = {"id": orden["id"], "modo": "apagado",
            "hasta": (fecha + timedelta(hours=1)).isoformat()}
    assert _tele(cliente, dev, mala)["reposo_ack"] is None
    assert _trampa(cliente, dev)["estado"] != "reposo"
    buena = {"id": orden["id"], "modo": "apagado", "hasta": fecha.isoformat()}
    assert _tele(cliente, dev, buena)["reposo_ack"] == orden["id"]
    assert _trampa(cliente, dev)["reposo_modo"] == "apagado"
    assert cliente.post(ruta, params={"cuando": "2026-10-09T12:00:00"},
                        auth=ADMIN).status_code == 422


def test_modo_invernadero_usa_cadencia_diaria_en_estado_y_avisos(cliente):
    from app.core.db import sesion_corta
    from app.models.models import Imagen, ConfigCamara, Trampa
    from app.services import traps as traps_svc

    dev = "zero-CADENCIA-CAMPO"
    _tele(cliente, dev)
    tid = _trampa(cliente, dev)["id"]
    with sesion_corta() as s:
        cfg = s.get(ConfigCamara, tid)
        cfg.intervalo_seg = 20
        cfg.deep_sleep = 1
        cfg.hora_despertar = "07:30"
        s.add(cfg)
        s.add(Imagen(trampa_id=tid, ruta_fichero="campo-vieja.jpg",
                     estado="procesada",
                     capturada=datetime.now(timezone.utc) - timedelta(minutes=5)))
        trap = s.get(Trampa, tid)
        trap.red = "lte"
        s.add(trap)
        s.commit()
        assert traps_svc.grace_seconds(cfg, trap) == 172800 + cfg.ventana_despierto_seg

    config = cliente.get(f"/api/v1/traps/{dev}/config",
                         headers={"X-API-Key": DEVICE_KEY}).json()
    assert config["intervalo_efectivo_seg"] == 86400
    assert "07:30" in config["intervalo_motivo"]
    titulos = [a["titulo"] for a in cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]]
    assert not any(dev in x and "sin subir fotos" in x for x in titulos)
    assert not any(dev in x and "agotar el plan" in x for x in titulos)


def test_campo_anula_video_y_keep_awake_aunque_el_panel_siga_abierto(cliente, jpeg):
    from app.services import live as live_svc
    from app.services import traps as traps_svc

    dev = "zero-CAMPO-PRIORIDAD"
    _tele(cliente, dev)
    tid = _trampa(cliente, dev)["id"]
    ruta = f"/api/v1/traps/{tid}"
    assert cliente.post(f"{ruta}/live-ping", auth=ADMIN).status_code == 200
    traps_svc.marcar_keep_awake(tid)
    assert live_svc.quiere_vivo(tid) and traps_svc.keep_awake_activo(tid)

    assert cliente.put(f"{ruta}/config", auth=ADMIN,
                       json={"deep_sleep": 1, "hora_despertar": "14:00"}).status_code == 200
    assert not live_svc.quiere_vivo(tid)
    assert not traps_svc.keep_awake_activo(tid)
    assert cliente.post(f"{ruta}/live-ping", auth=ADMIN).status_code == 409
    assert cliente.get(f"{ruta}/stream", auth=ADMIN).status_code == 409
    assert cliente.post(f"{ruta}/keep-awake", auth=ADMIN).json()["segundos"] == 0

    # Incluso una ventana vieja que permanezca en memoria no puede vetar el corte.
    live_svc.pedir_vivo(tid)
    traps_svc.marcar_keep_awake(tid)
    respuesta = _tele(cliente, dev)
    assert respuesta["en_vivo"] is False and respuesta["keep_awake"] is False
    assert respuesta["cada"] == 60
    assert cliente.get(f"/api/v1/traps/{dev}/live",
                       headers={"X-API-Key": DEVICE_KEY}).json()["en_vivo"] is False
    subida = cliente.post("/api/v1/ingest", headers={"X-API-Key": DEVICE_KEY},
                          files={"image": ("campo.jpg", jpeg, "image/jpeg")},
                          data={"device_id": dev})
    assert subida.status_code == 201
    assert subida.json()["keep_awake"] is False


def test_perfil_conserva_hora_de_despertar(cliente, tmp_path, monkeypatch):
    from app.api import perfil

    monkeypatch.setattr(perfil, "FICHERO", tmp_path / "perfiles.json")
    dev = "zero-PERFIL-HORA"
    _tele(cliente, dev)
    tid = _trampa(cliente, dev)["id"]
    ruta = f"/api/v1/traps/{tid}"
    assert cliente.put(f"{ruta}/config", auth=ADMIN,
                       json={"deep_sleep": 1, "hora_despertar": "06:45"}).status_code == 200
    guardado = cliente.post(f"{ruta}/perfil", auth=ADMIN, json={"nota": "campo"})
    assert guardado.status_code == 200 and guardado.json()["hora_despertar"] == "06:45"
    assert cliente.put(f"{ruta}/config", auth=ADMIN,
                       json={"hora_despertar": "08:00"}).status_code == 200
    restaurado = cliente.post(f"{ruta}/perfil/restaurar", auth=ADMIN)
    assert restaurado.status_code == 200
    assert restaurado.json()["config"]["hora_despertar"] == "06:45"


def test_horarios_multiples_validacion_historial_y_perfil(cliente, tmp_path, monkeypatch):
    from app.api import perfil
    from app.models.models import ConfigHistorial
    from app.core.db import engine
    from sqlmodel import Session, select
    monkeypatch.setattr(perfil, "FICHERO", tmp_path / "perfil.json")
    dev = "zero-HORARIOS"
    _tele(cliente, dev)
    tid = _trampa(cliente, dev)["id"]
    ruta = f"/api/v1/traps/{tid}"
    horas = ["08:30", "11:00", "15:00", "17:00"]
    r = cliente.put(ruta + "/config", auth=ADMIN,
                    json={"deep_sleep": 1, "horas_despertar": ["17:00", "11:00", "15:00", "08:30", "11:00"]})
    assert r.status_code == 200
    assert r.json()["horas_despertar"] == horas
    assert r.json()["hora_despertar"] == "08:30"
    for invalida in ([], None, ["24:00"], ["8:30"], [3], "08:30"):
        assert cliente.put(ruta + "/config", auth=ADMIN, json={"horas_despertar": invalida}).status_code == 422
    # Separación mínima de 15 min, también al pasar la medianoche; sin tope de cuántas.
    for invalida in (["10:00", "10:14"], ["23:55", "00:05"]):
        assert cliente.put(ruta + "/config", auth=ADMIN, json={"horas_despertar": invalida}).status_code == 422
    assert cliente.put(ruta + "/config", auth=ADMIN,
                       json={"horas_despertar": ["10:00", "10:15", "23:45"]}).status_code == 200
    assert cliente.put(ruta + "/config", auth=ADMIN, json={"horas_despertar": horas}).status_code == 200
    assert cliente.post(ruta + "/perfil", auth=ADMIN, json={"nota": "horarios"}).status_code == 200
    with Session(engine) as s:
        h = s.exec(select(ConfigHistorial).where(ConfigHistorial.trampa_id == tid,
                                               ConfigHistorial.version == r.json()["version"])).one()
        assert h.horas_despertar == horas
    # Una edición parcial no borra horarios. La API antigua sigue permitiendo una sola hora.
    assert cliente.put(ruta + "/config", auth=ADMIN, json={"brillo": 0.1}).json()["horas_despertar"] == horas
    assert cliente.put(ruta + "/config", auth=ADMIN, json={"hora_despertar": "10:00"}).json()["horas_despertar"] == ["10:00"]
    assert cliente.post(ruta + "/perfil/restaurar", auth=ADMIN).json()["config"]["horas_despertar"] == horas
    assert cliente.get(f"/api/v1/traps/{dev}/config", headers={"X-API-Key": DEVICE_KEY}).json()["horas_despertar"] == horas
    for paso in (30, 15):                         # cada media hora y cada cuarto, todo el día
        todas = ["%02d:%02d" % divmod(m, 60) for m in range(0, 1440, paso)]
        r = cliente.put(ruta + "/config", auth=ADMIN, json={"horas_despertar": todas})
        assert r.status_code == 200 and len(r.json()["horas_despertar"]) == 1440 // paso


def test_migracion_horarios_aditiva_e_idempotente(tmp_path, monkeypatch):
    from app.core import db
    from sqlalchemy import create_engine, text
    viejo = create_engine("sqlite:///" + str(tmp_path / "antigua.db"))
    with viejo.begin() as c:
        for tabla in ("configcamara", "confighistorial"):
            c.execute(text(f"CREATE TABLE {tabla} (id INTEGER PRIMARY KEY, hora_despertar TEXT)"))
            c.execute(text(f"INSERT INTO {tabla} VALUES (1, '13:30')"))
    monkeypatch.setattr(db, "engine", viejo)
    db._migrate()
    db._migrate()
    with viejo.connect() as c:
        for tabla in ("configcamara", "confighistorial"):
            assert c.execute(text(f"SELECT hora_despertar, horas_despertar FROM {tabla}")).one() == ("13:30", None)
    viejo.dispose()
