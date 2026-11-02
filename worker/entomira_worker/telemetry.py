"""Telemetría del nodo de cómputo (GPU vía nvidia-smi, CPU/RAM vía psutil)."""
from __future__ import annotations

import os
import subprocess

# Evita que Windows abra una ventana de consola en cada llamada a nvidia-smi.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def gpu_stats() -> dict:
    q = ("name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw")
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6, creationflags=_NO_WINDOW)
        line = out.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        return {
            "gpu_name": parts[0],
            "gpu_temp": _f(parts[1]), "gpu_util": _f(parts[2]),
            "gpu_mem_used": _f(parts[3]) / 1024 if _f(parts[3]) else None,   # GB
            "gpu_mem_total": _f(parts[4]) / 1024 if _f(parts[4]) else None,
            "gpu_power": _f(parts[5]),
        }
    except Exception:
        return {}


def host_stats() -> dict:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {
            "cpu": psutil.cpu_percent(interval=None),
            "ram_used": round(vm.used / 1e9, 1),
            "ram_total": round(vm.total / 1e9, 1),
        }
    except Exception:
        return {}


def collect() -> dict:
    return {**gpu_stats(), **host_stats()}


def _f(s: str):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None
