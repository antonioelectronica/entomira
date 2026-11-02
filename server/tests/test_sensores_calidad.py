"""Un sensor suelto se manda como 0 y parece una medida: el servidor tiene que avisar.

Fallo F6 del experimento del artículo B. Tan importante como avisar es NO avisar con datos
buenos: una tensión ausente (VBUS sin cablear), el ruido normal del INA o una celda llena.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import ADMIN, DEVICE_KEY


def _tele(cliente, dev, ina=None, **extra):
    cuerpo = {"device_id": dev, **extra}
    if ina is not None:
        cuerpo["ina"] = ina
    r = cliente.post("/api/v1/telemetry", headers={"X-API-Key": DEVICE_KEY}, json=cuerpo)
    assert r.status_code == 200, r.text


def _ventanas(n, **campos):
    ahora = datetime.now(timezone.utc)
    out = []
    for i in range(n):
        d = {"fin": (ahora - timedelta(minutes=5 * (n - i))).isoformat(timespec="seconds"),
             "n": 150, "v": 5.08 + i * 0.01, "v_min": 5.01, "ma": 150.0 + i * 3.7,
             "ma_min": 120.0 + i, "ma_max": 400.0 + i, "mwh": 60.0, "sat": 0}
        d.update(campos)
        out.append(d)
    return out


def _sospechas(cliente, dev):
    return [a["detalle"] for a in cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]
            if a["titulo"] == f"Sensor sospechoso en «{dev}»"]


def test_un_ina_suelto_que_lee_cero_da_aviso(cliente):
    _tele(cliente, "zero-SC1", ina=_ventanas(3, v=0.0, v_min=0.0))
    assert any("0 V" in d for d in _sospechas(cliente, "zero-SC1"))
    _tele(cliente, "zero-SC2", ina=_ventanas(3, ma=0.0, ma_min=0.0, ma_max=0.0))
    assert any("0 mA" in d for d in _sospechas(cliente, "zero-SC2"))


def test_un_ina_clavado_da_aviso(cliente):
    _tele(cliente, "zero-SC3", ina=[dict(w, v=5.1, ma=150.0, ma_min=120.0, ma_max=400.0)
                                    for w in _ventanas(6)])
    assert any("mismo valor" in d for d in _sospechas(cliente, "zero-SC3"))


def test_la_celda_a_cero_o_incoherente_da_aviso(cliente):
    _tele(cliente, "zero-SC4", voltaje=0.0, bateria=0)
    assert any("0 V" in d for d in _sospechas(cliente, "zero-SC4"))
    titulos = [a["titulo"] for a in cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]]
    assert not any("Batería" in t and "zero-SC4" in t for t in titulos), \
        "un sensor suelto no es una batería vacía"
    # Pero una batería baja de verdad conserva su crítico aunque la tensión venga rara.
    _tele(cliente, "zero-SC10", voltaje=0.0, bateria=8)
    titulos = [a["titulo"] for a in cliente.get("/api/v1/avisos", auth=ADMIN).json()["avisos"]]
    assert any("Batería crítica" in t and "zero-SC10" in t for t in titulos)
    _tele(cliente, "zero-SC5", voltaje=4.15, bateria=0)
    assert any("0 %" in d for d in _sospechas(cliente, "zero-SC5"))


def test_datos_buenos_no_dan_aviso(cliente):
    # Ruido normal, tensión AUSENTE (VBUS sin cablear), celda llena y pocas ventanas.
    _tele(cliente, "zero-SC6", ina=_ventanas(6), voltaje=4.2, bateria=100)
    _tele(cliente, "zero-SC7", ina=_ventanas(6, v=None, v_min=None))
    _tele(cliente, "zero-SC8", ina=_ventanas(2, v=0.0, v_min=0.0))     # 2 < 3: aún no
    # Sin VBUS y en reposo, las corrientes redondeadas se repiten: eso NO es un sensor clavado.
    _tele(cliente, "zero-SC9", ina=[dict(w, v=None, v_min=None, ma=150.0, ma_min=120.0,
                                         ma_max=400.0) for w in _ventanas(6)])
    for dev in ("zero-SC6", "zero-SC7", "zero-SC8", "zero-SC9"):
        assert _sospechas(cliente, dev) == [], dev
