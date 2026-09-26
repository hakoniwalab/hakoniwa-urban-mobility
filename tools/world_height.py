#!/usr/bin/env python3
"""Spawn ground height from a downward MuJoCo ray on the World (asset-contract 5.4).

The ray hits the top of the World collision geometry, so terrain and
buildings both count: a vehicle placed on a rooftop starts just above the
roof. The compiled World model is cached by a fingerprint of its MJCF
inputs (issue #5 principle 3), so the placement loop does not recompile it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
BUSINESS_PACK = ROOT.parent / "hakoniwa-business-pack"
CACHE_DIR = BUSINESS_PACK / "work/urban/cache/world-height"
# Rays start above any City geometry; MJCF Z is up in metres.
RAY_START_M = 10000.0
# A ray passes through at most this many non-colliding geoms before it
# gives up; City Worlds contain none, so this only bounds a pathological model.
MAX_PASS_THROUGH = 64


class WorldHeightError(RuntimeError):
    pass


FILE_REFERENCE = re.compile(r'\bfile="([^"]+)"')


def fingerprint(mjcf: Path) -> str:
    """Hash the MJCF and the files it references (terrain hfield, meshes).

    City World MJCFs reference their terrain relative to the MJCF directory.
    """
    text = mjcf.read_bytes()
    digest = hashlib.sha256(text)
    for reference in sorted(set(FILE_REFERENCE.findall(text.decode("utf-8", errors="replace")))):
        path = (mjcf.parent / reference.replace("\\", "/")).resolve()
        digest.update(reference.encode())
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
    return digest.hexdigest()


def load_model(mjcf: Path, *, cache_dir: Path = CACHE_DIR, mujoco=None):
    """Return the compiled World model, compiling the MJCF only on a cache miss."""
    if mujoco is None:
        import mujoco
    mjcf = mjcf.resolve()
    cached = cache_dir / f"{fingerprint(mjcf)}.mjb"
    if cached.is_file():
        return mujoco.MjModel.from_binary_path(str(cached))
    model = mujoco.MjModel.from_xml_path(str(mjcf))
    cache_dir.mkdir(parents=True, exist_ok=True)
    partial = cached.with_suffix(".mjb.partial")
    mujoco.mj_saveModel(model, str(partial), None)
    partial.replace(cached)
    return model


class WorldHeight:
    """ground(east_m, north_m) on a World whose MJCF frame is X=North, Y=-East, Z=Up."""

    def __init__(self, model, mujoco=None):
        if mujoco is None:
            import mujoco
        import numpy

        self._mujoco = mujoco
        self._numpy = numpy
        self.model = model
        self.data = mujoco.MjData(model)
        mujoco.mj_forward(model, self.data)

    def __call__(self, east_m: float, north_m: float) -> float:
        numpy = self._numpy
        point = numpy.array([north_m, -east_m, RAY_START_M], dtype=numpy.float64)
        down = numpy.array([0.0, 0.0, -1.0], dtype=numpy.float64)
        geom = numpy.array([-1], dtype=numpy.int32)
        for _ in range(MAX_PASS_THROUGH):
            distance = self._mujoco.mj_ray(self.model, self.data, point, down, None, 1, -1, geom)
            if distance < 0:
                raise WorldHeightError(f"no World geometry below east={east_m} m, north={north_m} m")
            hit = point[2] - distance
            index = int(geom[0])
            if self.model.geom_contype[index] or self.model.geom_conaffinity[index]:
                return float(hit)
            # A visual-only geom: continue below it.
            point[2] = hit - 1.0e-6
        raise WorldHeightError(f"too many non-colliding geoms above east={east_m} m, north={north_m} m")


def ray_ground(mjcf: Path, *, cache_dir: Path = CACHE_DIR) -> Callable[[float, float], float]:
    return WorldHeight(load_model(mjcf, cache_dir=cache_dir))
