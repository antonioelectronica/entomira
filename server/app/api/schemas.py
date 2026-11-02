"""Esquemas de request/response de la API."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Annotated

from pydantic import BaseModel, Field, model_validator

from ..core import config
# Los valores admitidos viven en el modelo, no aqui: si se duplicaran, la API y la base de
# datos podrian discrepar sin que nada fallara hasta que una trampa recibiera algo imposible.
from ..models.models import BALANCES_VALIDOS, ENFOQUES_VALIDOS


# Cada despertar cuesta arranque, 4G, foto y subida (y hasta 5 min de reintento sin
# cobertura): dos horas más juntas que eso hacen que la segunda se salte sin aviso.
# No hay tope de cuántas: Antonio quiere poder poner tantas horas concretas como necesite, y
# la separación ya lo acota (como mucho 96 al día).
SEPARACION_MIN_CAMPO = 15
HoraCampo = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]


class ConfigOut(BaseModel):
    version: int
    # UN CAMPO, UN SIGNIFICADO. `intervalo_seg` es **lo que hay que hacer**: al dispositivo se
    # le sirve ya recortado por la politica de datos, para que una trampa con firmware viejo
    # --que ignore los campos nuevos-- obedezca el tope igual. Los otros dos son para el panel:
    # lo que el usuario configuro, y por que no coincide.
    intervalo_seg: int
    intervalo_config_seg: int | None = None
    intervalo_efectivo_seg: int | None = None
    intervalo_motivo: str | None = None
    ancho: int = 4608
    alto: int = 2592
    calidad_jpeg: int = 100
    brillo: float = 0.0
    contraste: float = 1.0
    saturacion: float = 1.0
    ev: float = 0.0
    balance: str = "auto"
    enfoque: str = "auto"
    lente_dioptrias: float = 0.0
    deep_sleep: int = 0
    horas_despertar: list[str] = Field(default_factory=lambda: ["08:00"])
    hora_despertar: str = "08:00"
    ventana_despierto_seg: int = 15


class ConfigUpdate(BaseModel):
    # Mínimo de 10 s: por debajo, una trampa llena el disco de la Pi en horas. A 12 MP
    # (1,4 MB/foto) 10 s son 12 GB/día — y el disco ya se llenó una vez, el 2026-07-31,
    # tumbando la ingesta tres días en silencio.
    intervalo_seg: int | None = Field(default=None, ge=10, le=86400)

    # Los topes no son decoración: esto lo escribe la web y lo obedece una máquina que está
    # colgada de un brazo en un invernadero. Un valor absurdo aquí es una trampa que no
    # captura, y no hay quien lo vea hasta que faltan las fotos.
    ancho: int | None = Field(default=None, ge=320, le=4608)
    alto: int | None = Field(default=None, ge=240, le=2592)
    calidad_jpeg: int | None = Field(default=None, ge=1, le=100)
    brillo: float | None = Field(default=None, ge=-1.0, le=1.0)
    contraste: float | None = Field(default=None, ge=0.0, le=2.0)
    saturacion: float | None = Field(default=None, ge=0.0, le=2.0)
    ev: float | None = Field(default=None, ge=-2.0, le=2.0)
    balance: Literal[BALANCES_VALIDOS] | None = None
    enfoque: Literal[ENFOQUES_VALIDOS] | None = None
    # 0 = infinito; 10 dioptrías = 10 cm, que es más cerca de lo que enfoca el módulo.
    lente_dioptrias: float | None = Field(default=None, ge=0.0, le=10.0)

    deep_sleep: int | None = Field(default=None, ge=0, le=1)
    hora_despertar: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    horas_despertar: list[HoraCampo] | None = Field(default=None, min_length=1)
    ventana_despierto_seg: int | None = Field(default=None, ge=5, le=300)

    @model_validator(mode="after")
    def normalizar_horarios(self):
        if "horas_despertar" in self.model_fields_set and self.horas_despertar is None:
            raise ValueError("Debe haber al menos una hora de despertar")
        if self.horas_despertar is not None:
            self.horas_despertar = sorted(set(self.horas_despertar))
            minutos = [int(h[:2]) * 60 + int(h[3:]) for h in self.horas_despertar]
            # Circular: la última del día también debe dejar margen hasta la primera de mañana.
            huecos = [b - a for a, b in zip(minutos, minutos[1:] + [minutos[0] + 1440])]
            if len(minutos) > 1 and min(huecos) < SEPARACION_MIN_CAMPO:
                raise ValueError("Deja al menos %d minutos entre dos horas de despertar"
                                 % SEPARACION_MIN_CAMPO)
            if self.hora_despertar is not None and self.hora_despertar != self.horas_despertar[0]:
                raise ValueError("La hora antigua debe coincidir con la primera de la lista")
            self.hora_despertar = self.horas_despertar[0]
        elif self.hora_despertar is not None:
            self.horas_despertar = [self.hora_despertar]
        return self


class IngestResponse(BaseModel):
    image_id: int
    trap_id: int
    registered: bool
    config: ConfigOut
    # true si esta captura ya estaba en el servidor y NO se ha vuelto a guardar. La trampa la
    # trata como subida buena —que lo es— y la borra de su tarjeta. Ver `POST /ingest`.
    duplicada: bool = False
    # true si alguien está viendo la cámara en vivo → la PCB no debe dormirse este ciclo
    keep_awake: bool = False


class TrapCreate(BaseModel):
    device_id: str
    alias: str = ""
    invernadero_id: int | None = None
    sector: str | None = None


class TrapUpdate(BaseModel):
    alias: str | None = None
    invernadero_id: int | None = None
    sector: str | None = None
    linea: int | None = None
    # Calibración óptica: se mide una vez por trampa (ver scripts/recorte_trampa.py).
    px_mm: float | None = Field(default=None, gt=0, le=200)
    distancia_cm: float | None = Field(default=None, gt=0, le=500)


class UltimaImagenOut(BaseModel):
    """Lo justo de la ultima foto para pintar una tarjeta sin pedir nada mas."""
    id: int
    capturada: datetime
    estado: str                        # pendiente | procesada | error
    num_insectos: int | None = None    # None mientras no este procesada: un 0 no es un cero
    procesada: datetime | None = None  # cuando la analizo la IA


class TrapOut(BaseModel):
    id: int
    device_id: str
    alias: str
    invernadero_id: int | None
    invernadero_nombre: str | None = None
    sector: str | None
    linea: int | None = None
    estado: str
    reposo_hasta: datetime | None = None
    reposo_modo: str | None = None
    reposo_error: str | None = None
    despertar_confirmado: datetime | None = None
    despertar_origen: str | None = None
    despertar_informado: datetime | None = None
    # ¿Ha dado señales dentro de su margen? Lo calcula el SERVIDOR, que es el único que sabe
    # cada cuánto le ha pedido hablar a esta trampa (cadencia + retención). Antes la web lo
    # recalculaba con 180 s escritos a pelo y se contradecía con `estado` en la misma tarjeta.
    # Va aparte de `estado` porque una trampa sin invernadero asignado sale «sin_asignar» y aun
    # así puede estar viva y emitiendo vídeo.
    en_linea: bool = False
    es_movil: bool = False          # True si es el móvil de un usuario (owner_id), no una trampa física
    ip: str | None = None
    bateria: int | None = None
    bateria_estimada: int | None = None  # aproximación por tensión, nunca SOC medido
    voltaje: float | None = None
    crate: float | None = None         # %/h: >0 cargando (panel solar o USB), <0 descargando
    carga_estado: str | None = None    # cargando|descargando|reposo|midiendo (rapido, por tension)
    carga_fuente: str | None = None    # de donde sale el veredicto: trampa:*, tension o crate
    carga_confianza: str | None = None  # alta / media / baja (solo si lo decide la trampa)
    tendencia_mv_h: int | None = None  # pendiente de la tension, mV/hora
    fw_version: str | None = None      # firmware que corre la placa (para el OTA)
    buffer_pend: int | None = None     # fotos guardadas en la tarjeta de la trampa
    # Datos móviles estimados de los últimos 30 días (0 si no va por 4G). Con tarifa de datos,
    # «cuánto llevo gastado» importa tanto como la batería.
    datos_mes_b: int | None = None
    # Ultima ventana de 5 min del medidor de consumo (INA226): mA y V que ENTRAN en la trampa.
    # `None` = no ha llegado ninguna, que no es lo mismo que «consume 0». Ver services/ina.py.
    consumo_ina: dict | None = None
    rssi: int | None = None
    wifi_pct: int | None = None        # 0..100 derivado del RSSI (de la red que use)
    red: str | None = None             # wifi | lte | usb: por donde llego el ultimo contacto
    # Lo que se USA y lo que esta DISPONIBLE son dos cosas distintas: con el servidor en la
    # misma red local, la trampa sube por WiFi aunque el 4G funcione perfectamente. `None` no
    # significa «no hay 4G», significa «no hay dato reciente»: el estado vive en memoria y
    # caduca a los 3 min, asi que tras reiniciar el servidor sale None hasta la siguiente
    # telemetria. Ver `aqui-un-cero-no-significa-cero`.
    lte_lista: bool | None = None
    ifaz: str | None = None
    operador: str | None = None        # nombre de la red movil, solo con 'lte'
    ultimo_visto: datetime | None
    num_imagenes: int = 0
    ultimo_conteo: int | None = None
    # LA ULTIMA IMAGEN, AQUI DENTRO. Sin esto, la vista Trampas pedia `/images?trampa=N&limit=1`
    # por CADA trampa: con 20 trampas son 20 peticiones simultaneas contra un pool de 15
    # conexiones, que es el fallo que ya mato la API en el ensayo de 20 (ver G15). Ademas trae
    # `procesada`, que no se exponia en ningun sitio: sin esa fecha, la tarjeta solo podia
    # decir «Fecha de analisis no disponible» -- un recuento sin saber de cuando es el analisis
    # no se puede interpretar.
    ultima_imagen: UltimaImagenOut | None = None
    px_mm: float | None = None
    distancia_cm: float | None = None
    config: ConfigOut | None = None


class GreenhouseIn(BaseModel):
    nombre: str
    ubicacion: str = ""
    num_lineos: int = 0
    responsable: str = ""
    contacto: str = ""
    email: str = ""
    tipo: str = ""
    cultivo: str = ""
    notas: str = ""


class GreenhouseOut(GreenhouseIn):
    id: int
    num_trampas: int = 0
    num_insectos: int = 0
    client_token: str = ""
    sectores: list[str] = config.SECTORES


class DetectionOut(BaseModel):
    clase: str
    confianza: float
    x: float
    y: float
    w: float
    h: float


class ImageOut(BaseModel):
    id: int
    trampa_id: int
    device_id: str
    alias: str
    invernadero_id: int | None
    invernadero_nombre: str | None
    sector: str | None
    estado: str
    num_insectos: int
    modelo_version: str
    es_demo: bool
    ancho: int
    alto: int
    capturada: datetime
    # Con qué se tomó. `None` en las fotos anteriores a que esto se registrara.
    condiciones: dict | None = None
    detecciones: list[DetectionOut] = []
    # Hay recorte de la tarjeta: /file lo sirve y /original da el JPEG sin recortar.
    recortada: bool = False


class InferenceResult(BaseModel):
    model_version: str = ""
    num_insectos: int = 0
    detections: list[DetectionOut] = []
