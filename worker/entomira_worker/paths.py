"""Rutas canónicas del nodo de IA (fuera de OneDrive; ver Docs_Obsidian/01_Arquitectura.md D1)."""
from pathlib import Path

# Raíz de datos/modelos ML (adyacente al venv y a los datasets).
ML_ROOT = Path(r"C:\ento_ml")
DATASETS = ML_ROOT / "datasets"

# Datasets etiquetados (formato YOLO).
DS_GENERIC = DATASETS / "yellow_sticky"            # 1 clase: insecto
DS_SPECIES = DATASETS / "yellow_sticky_species"    # 3 clases: MR, NC, WF

DATA_GENERIC = DS_GENERIC / "data_entomira.yaml"
DATA_SPECIES = DS_SPECIES / "data_entomira.yaml"

# Runs de entrenamiento y modelos versionados.
RUNS = ML_ROOT / "runs_entomira"
MODELS = ML_ROOT / "models" / "entomira"

for _p in (RUNS, MODELS):
    _p.mkdir(parents=True, exist_ok=True)
