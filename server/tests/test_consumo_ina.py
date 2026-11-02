"""Corriente del INA226 por la telemetría: se guarda, no se duplica y un hueco no es un cero."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import ADMIN, DEVICE_KEY


def _tele(cliente, dev, ina=None, **extra):
    cuerpo = {"device_id": dev, "red": "lte", **extra}
    if ina is not None:
        cuerpo["ina"] = ina
    return cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY}, json=cuerpo)


def _id(cliente, dev):
    return next(t["id"] for t in cliente.get("/api/v1/traps", auth=ADMIN).json()
                if t["device_id"] == dev)


def _v(fin, ma=250.0, **k):
    d = {"fin": fin.isoformat(timespec="seconds"), "n": 150, "v": 5.08, "v_min": 5.01,
         "ma": ma, "ma_min": 180.0, "ma_max": 640.0, "mwh": 106.0, "sat": 0}
    d.update(k)
    return d


def test_sin_medidor_no_hay_consumo(cliente):
    """Sin ventanas, la ficha dice «no sé», no «0 mA»."""
    assert _tele(cliente, "zero-INA0").status_code == 200
    tid = _id(cliente, "zero-INA0")
    assert cliente.get(f"/api/v1/traps/{tid}", auth=ADMIN).json()["consumo_ina"] is None
    r = cliente.get(f"/api/v1/traps/{tid}/consumo-ina", auth=ADMIN).json()
    assert r["ventanas"] == []


def test_ventanas_se_guardan_una_vez_y_la_ficha_lleva_la_ultima(cliente):
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    v1, v2 = _v(ahora - timedelta(minutes=10)), _v(ahora - timedelta(minutes=5), ma=300.0)
    assert _tele(cliente, "zero-INA1", ina=[v1, v2]).status_code == 200
    # La respuesta se perdió y la trampa reenvía: la misma ventana no se guarda dos veces.
    assert _tele(cliente, "zero-INA1", ina=[v2]).status_code == 200
    tid = _id(cliente, "zero-INA1")
    serie = cliente.get(f"/api/v1/traps/{tid}/consumo-ina", auth=ADMIN).json()["ventanas"]
    assert [w["ma_media"] for w in serie] == [250.0, 300.0]
    ult = cliente.get(f"/api/v1/traps/{tid}", auth=ADMIN).json()["consumo_ina"]
    assert ult["ma_media"] == 300.0 and ult["v_media"] == 5.08 and ult["ma_max"] == 640.0


def test_una_ventana_mala_no_tumba_la_telemetria(cliente):
    """Un 422 aquí la trampa lo repetiría en cada informe y parecería caída."""
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    malas = [{"fin": "ayer"}, "texto", _v(ahora, n=0), _v(ahora, sat=999),
             _v(ahora - timedelta(minutes=1), v=None, ma=None)]
    buena = _v(ahora - timedelta(minutes=2), ma=222.0)
    r = _tele(cliente, "zero-INA2", ina=malas + [buena])
    assert r.status_code == 200
    tid = _id(cliente, "zero-INA2")
    serie = cliente.get(f"/api/v1/traps/{tid}/consumo-ina", auth=ADMIN).json()["ventanas"]
    assert [w["ma_media"] for w in serie] == [222.0]


def test_saturada_es_hueco_no_cero(cliente):
    """Si todo saturó, la corriente es «más de lo que el shunt mide», no cero."""
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    v = _v(ahora, ma=None, ma_min=None, ma_max=None, mwh=None, sat=150)
    assert _tele(cliente, "zero-INA3", ina=[v]).status_code == 200
    tid = _id(cliente, "zero-INA3")
    ult = cliente.get(f"/api/v1/traps/{tid}", auth=ADMIN).json()["consumo_ina"]
    assert ult["ma_media"] is None and ult["mwh"] is None and ult["saturadas"] == 150


def test_sin_vbus_se_guarda_la_corriente(cliente):
    """Con el pin VBUS suelto llega corriente sin tensión: se guarda, y la energía es hueco."""
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    v = _v(ahora, v=None, v_min=None, mwh=None, ma=160.0)
    assert _tele(cliente, "zero-INA5", ina=[v]).status_code == 200
    ult = cliente.get(f"/api/v1/traps/{_id(cliente, 'zero-INA5')}", auth=ADMIN).json()["consumo_ina"]
    assert ult["ma_media"] == 160.0 and ult["v_media"] is None and ult["mwh"] is None


def test_ina_no_es_bateria(cliente):
    """El INA mide el raíl de 5 V: la batería de la ficha sigue sin dato."""
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    _tele(cliente, "zero-INA4", ina=[_v(ahora)])
    t = cliente.get(f"/api/v1/traps/{_id(cliente, 'zero-INA4')}", auth=ADMIN).json()
    assert t["voltaje"] is None and t["bateria"] is None


def test_una_ina_que_no_es_lista_no_da_422(cliente):
    """Un objeto donde va la lista: la telemetría sigue siendo 200 y no se guarda nada."""
    r = _tele(cliente, "zero-INA9", ina={"fin": "x"}, ina_ya=["no", "es", "dict"])
    assert r.status_code == 200


def test_ventanas_con_fecha_imposible_se_descartan(cliente):
    """Un reloj desajustado no puede dejar una ventana del futuro como «última» durante meses."""
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    futura, vieja = _v(ahora + timedelta(days=400), ma=111.0), _v(ahora - timedelta(days=30), ma=222.0)
    buena = _v(ahora - timedelta(minutes=5), ma=333.0)
    assert _tele(cliente, "zero-INA8", ina=[futura, vieja, buena]).status_code == 200
    tid = _id(cliente, "zero-INA8")
    ult = cliente.get(f"/api/v1/traps/{tid}", auth=ADMIN).json()["consumo_ina"]
    assert ult["ma_media"] == 333.0


def test_una_ventana_repetida_no_revienta_el_indice():
    """Aunque la comprobación previa no la vea (dos informes cruzados), el índice no da 500."""
    from sqlmodel import Session
    from app.core.db import engine
    from app.models.models import Trampa
    from app.services import ina as ina_svc
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    v = _v(ahora - timedelta(minutes=5))
    with Session(engine) as s:
        t = Trampa(device_id="zero-INA7", alias="zero-INA7")
        s.add(t); s.commit(); s.refresh(t)
        assert ina_svc.guardar(s, t.id, [v]) == 1
        s.commit()
        # Simula la carrera: la comprobación previa no la encuentra.
        orig = ina_svc.select
        try:
            ina_svc.select = lambda *a, **k: orig(*a, **k).where(False)
            assert ina_svc.guardar(s, t.id, [v]) == 0
        finally:
            ina_svc.select = orig
        s.commit()
