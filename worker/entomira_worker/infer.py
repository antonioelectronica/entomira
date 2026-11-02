"""Inferencia YOLO en GPU: devuelve detecciones normalizadas + conteo."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config

_model = None
_model_path = None


def load_model(model_path: str | None = None):
    """Carga (y cachea) el modelo YOLO en GPU."""
    global _model, _model_path
    from ultralytics import YOLO

    path = model_path or config.MODEL_PATH
    if _model is None or path != _model_path:
        _model = YOLO(path)
        _model_path = path
    return _model


def imgsz_inferencia(model_path: str) -> int | None:
    """Un modelo entrenado en teselas lleva inferencia.json al lado: se infiere a la foto
    entera a ese tamaño. Sin él, Ultralytics usa el imgsz de entrenamiento (lo de siempre)."""
    try:
        lado = Path(model_path).with_name("inferencia.json")
        return int(json.loads(lado.read_text(encoding="utf-8"))["imgsz_inferencia"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def infer_image(image_path: str, model_path: str | None = None,
                conf: float = 0.25, iou: float = 0.45) -> dict[str, Any]:
    """
    Infiere sobre una imagen y devuelve:
        { num_insectos, model_version, detections:[{clase,confianza,x,y,w,h}] }
    con las bboxes en formato xywh NORMALIZADO (0..1), independiente de la resolución.
    """
    model = load_model(model_path)
    extra = {}
    imgsz = imgsz_inferencia(model_path or config.MODEL_PATH)
    if imgsz:
        extra["imgsz"] = imgsz
    # max_det alto: en trampas con mosca blanca densa el default (300) TRUNCA el conteo.
    res = model.predict(source=image_path, device=config.DEVICE, conf=conf,
                        iou=iou, max_det=3000, verbose=False, **extra)[0]

    names = res.names
    detections = []
    if res.boxes is not None:
        for b in res.boxes:
            xywhn = b.xywhn[0].tolist()   # cx, cy, w, h normalizados
            detections.append({
                "clase": names[int(b.cls[0])],
                "confianza": round(float(b.conf[0]), 4),
                "x": round(xywhn[0], 5),
                "y": round(xywhn[1], 5),
                "w": round(xywhn[2], 5),
                "h": round(xywhn[3], 5),
            })

    return {
        "num_insectos": len(detections),
        "model_version": Path(model_path or config.MODEL_PATH).parent.name,
        "detections": detections,
    }
