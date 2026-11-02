"""
Entrenamiento YOLO parametrizable para ENTOMIRA — GPU OBLIGATORIA (RTX 5060 Ti).

Verifica que CUDA está disponible y aborta si el entrenamiento caería a CPU.
Al terminar, versiona el mejor peso en  C:\\ento_ml\\models\\entomira\\vX\\best.pt
y guarda un JSON con las métricas (mAP50, mAP50-95, precision, recall).

Uso:
    python -m entomira_worker.train --data generic --model yolo11s.pt \
        --epochs 100 --imgsz 960 --batch 16 --patience 20
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import paths


def _assert_gpu() -> str:
    import torch
    if not torch.cuda.is_available():
        sys.exit("[ABORTADO] torch.cuda.is_available() == False. "
                 "El entrenamiento debe correr en GPU; instala PyTorch CUDA.")
    name = torch.cuda.get_device_name(0)
    print(f"[GPU] {name} | torch {torch.__version__} | CUDA build {torch.version.cuda}")
    return name


def _next_version(models_dir: Path) -> Path:
    n = 1
    while (models_dir / f"v{n}").exists():
        n += 1
    out = models_dir / f"v{n}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def resolve_data(data: str) -> Path:
    return {"generic": paths.DATA_GENERIC, "species": paths.DATA_SPECIES}.get(
        data, Path(data)
    )


def train(data: str, model: str, epochs: int, imgsz: int, batch: int,
          patience: int, name: str | None) -> dict:
    gpu = _assert_gpu()
    from ultralytics import YOLO

    data_yaml = resolve_data(data)
    if not data_yaml.exists():
        sys.exit(f"[ABORTADO] No existe el data.yaml: {data_yaml}")

    run_name = name or f"{data}_{Path(model).stem}_{datetime.now():%Y%m%d_%H%M%S}"
    print(f"[TRAIN] data={data_yaml} model={model} epochs={epochs} "
          f"imgsz={imgsz} batch={batch} patience={patience}")

    yolo = YOLO(model)
    results = yolo.train(
        data=str(data_yaml),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        patience=patience,
        device=0,               # GPU obligatoria
        project=str(paths.RUNS),
        name=run_name,
        exist_ok=False,
        verbose=True,
    )

    # Validación final para métricas limpias.
    metrics = yolo.val(data=str(data_yaml), imgsz=imgsz, device=0, split="test")
    box = metrics.box
    summary = {
        "dataset": data,
        "data_yaml": str(data_yaml),
        "model_base": model,
        "epochs_requested": epochs,
        "imgsz": imgsz,
        "batch": batch,
        "patience": patience,
        "gpu": gpu,
        "mAP50": round(float(box.map50), 4),
        "mAP50_95": round(float(box.map), 4),
        "precision": round(float(box.mp), 4),
        "recall": round(float(box.mr), 4),
        "run": run_name,
        "finished": datetime.now().isoformat(timespec="seconds"),
    }

    # Versionado del mejor peso.
    save_dir = Path(results.save_dir)
    best = save_dir / "weights" / "best.pt"
    vdir = _next_version(paths.MODELS)
    if best.exists():
        shutil.copy2(best, vdir / "best.pt")
    summary["version_dir"] = str(vdir)
    (vdir / "metrics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n[RESULTADO]\n" + json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"[OK] Peso versionado en: {vdir / 'best.pt'}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Entrenamiento YOLO ENTOMIRA (GPU)")
    ap.add_argument("--data", default="generic", help="'generic' | 'species' | ruta a data.yaml")
    ap.add_argument("--model", default="yolo11s.pt", help="pesos base")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--patience", type=int, default=20, help="early stopping")
    ap.add_argument("--name", default=None)
    args = ap.parse_args()
    train(args.data, args.model, args.epochs, args.imgsz, args.batch,
          args.patience, args.name)


if __name__ == "__main__":
    main()
