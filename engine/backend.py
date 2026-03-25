"""
Backend abstraction : GPU (CuPy) si disponible, sinon CPU (NumPy).
Tout le code moteur importe `xp` et `ndtr` depuis ce module.
Ne jamais importer cupy ou numpy directement dans engine/, scoring/.
"""

try:
    import cupy as xp
    from cupyx.scipy.special import ndtr
    GPU_AVAILABLE = True
except ImportError:
    import numpy as xp  # type: ignore[no-redef]
    from scipy.stats import norm
    ndtr = norm.cdf  # type: ignore[assignment]
    GPU_AVAILABLE = False


def to_cpu(arr):
    """Convertit un array GPU en NumPy (no-op si déjà NumPy)."""
    if GPU_AVAILABLE and hasattr(arr, "get"):
        return arr.get()
    return arr


def to_xp(arr):
    """Convertit un array NumPy en array du backend actif (GPU ou CPU)."""
    if GPU_AVAILABLE:
        return xp.asarray(arr)
    return xp.asarray(arr)


def get_device_info() -> dict | None:
    """Retourne les infos GPU pour l'UI, ou None si pas de GPU."""
    if not GPU_AVAILABLE:
        return None
    try:
        props = xp.cuda.runtime.getDeviceProperties(0)
        mem = xp.cuda.runtime.memGetInfo()
        return {
            "name": props["name"].decode(),
            "vram_total_gb": props["totalGlobalMem"] / 1024**3,
            "vram_free_gb": mem[0] / 1024**3,
        }
    except Exception:
        return None
