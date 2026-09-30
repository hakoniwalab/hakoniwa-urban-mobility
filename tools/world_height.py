#!/usr/bin/env python3
"""Spawn ground height from a downward MuJoCo ray on the World (asset-contract 5.4).

The ray hits the top of the World collision geometry, so terrain and
buildings both count: a vehicle placed on a rooftop starts just above the
roof.

Compiling a City MJCF grows faster than linearly with its mesh count
(Shizuoka, 18k meshes: about 2 minutes). The height only needs the highest
hit, so the World is split into mesh chunks that compile in parallel
processes; the ray is cast on every chunk and the highest colliding hit
wins. The compiled chunks are cached by a fingerprint of the MJCF inputs
(issue #5 principle 3), so the placement loop only loads them.

Progress goes to stdout as plain lines and as `[HAKO_PROGRESS]` events (the
City World job progress format) so a browser job can relay it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Callable
import xml.etree.ElementTree as ET

import urban_manifest


ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = urban_manifest.work_dir() / "urban/cache/world-height"
# Rays start this far above the World's highest geom (MJCF Z is up in
# metres). Not from a fixed far height: MuJoCo's ray-mesh test misses a thin
# mesh (a 2 cm road slab) from a few hundred metres away, and the height
# would then be the terrain under the slab.
RAY_START_MARGIN_M = 1.0
# A ray passes through at most this many non-colliding geoms before it
# gives up; City Worlds contain none, so this only bounds a pathological model.
MAX_PASS_THROUGH = 64
# MuJoCo's geom groups (mjNGROUP): a ray can leave out whole groups.
GEOM_GROUPS = 6
# Worlds with fewer meshes compile quickly in one process.
MESHES_PER_CHUNK_MIN = 1000
MAX_CHUNKS = 8
PROGRESS_PHASE = "world_height_model"
CACHE_LAYOUT = 2

FILE_REFERENCE = re.compile(r'\bfile="([^"]+)"')


class WorldHeightError(RuntimeError):
    pass


def _progress(message: str, **event) -> None:
    print(message, flush=True)
    if event:
        print("[HAKO_PROGRESS] " + json.dumps({"phase": PROGRESS_PHASE, **event}, separators=(",", ":")), flush=True)


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


def chunk_count(mesh_count: int, cpu_count: int | None = None) -> int:
    cpus = cpu_count or os.cpu_count() or 1
    return max(1, min(MAX_CHUNKS, cpus, mesh_count // MESHES_PER_CHUNK_MIN))


def split_world(mjcf: Path, chunks: int | None = None) -> list[str]:
    """Split a World MJCF into chunk MJCF texts that together hold every geom.

    Chunk 0 keeps every non-mesh geom (terrain hfield, boxes) and its assets;
    the mesh assets and the geoms using them are divided evenly. File
    references become absolute because the chunks compile from memory.
    """
    root = ET.parse(mjcf).getroot()
    for element in root.iter():
        reference = element.get("file")
        if reference:
            element.set("file", (mjcf.parent / reference.replace("\\", "/")).resolve().as_posix())
    parents = {child: parent for parent in root.iter() for child in parent}
    meshes = [element for element in root.iter("mesh") if parents.get(element) is not None and parents[element].tag == "asset"]
    mesh_geoms: dict[str, list] = {}
    other_geoms = []
    for geom in root.iter("geom"):
        (mesh_geoms.setdefault(geom.get("mesh"), []) if geom.get("mesh") else other_geoms).append(geom)
    count = chunks or chunk_count(len(meshes))
    if count <= 1:
        return [ET.tostring(root, encoding="unicode")]

    # A skeleton without any geom or mesh; each chunk adds its share back.
    moved = set(meshes) | {geom for geoms in mesh_geoms.values() for geom in geoms} | set(other_geoms)
    for parent in {parents[element] for element in moved}:
        parent[:] = [child for child in parent if child not in moved]
    # Container paths are taken after the removal, in the skeleton's indexing.
    paths = {}

    def path_of(element) -> tuple[int, ...]:
        if element not in paths:
            parent = parents.get(element)
            paths[element] = () if parent is None else path_of(parent) + (list(parent).index(element),)
        return paths[element]

    placements = {element: path_of(parents[element]) for element in moved}
    skeleton = copy.deepcopy(root)

    def resolve(tree, path: tuple[int, ...]):
        node = tree
        for index in path:
            node = list(node)[index]
        return node

    # Meshes and primitive geoms are spread evenly; the terrain stays in chunk 0.
    size = -(-len(meshes) // count)
    terrain = [geom for geom in other_geoms if geom.get("type") == "hfield"]
    primitives = [geom for geom in other_geoms if geom.get("type") != "hfield"]
    primitive_size = -(-len(primitives) // count)
    texts = []
    for index in range(count):
        chunk = copy.deepcopy(skeleton)
        if index > 0:
            chunk_parents = {child: parent for parent in chunk.iter() for child in parent}
            for hfield in [element for element in chunk.iter("hfield")]:
                chunk_parents[hfield].remove(hfield)
        share = meshes[index * size:(index + 1) * size]
        elements = list(share) + [geom for mesh in share for geom in mesh_geoms.get(mesh.get("name"), [])]
        elements += primitives[index * primitive_size:(index + 1) * primitive_size]
        if index == 0:
            elements += terrain
        # Resolve every target before appending so indices stay valid.
        targets = [(resolve(chunk, placements[element]), element) for element in elements]
        for target, element in targets:
            target.append(element)
        texts.append(ET.tostring(chunk, encoding="unicode"))
    return texts


def compile_chunk(source: Path, output: Path) -> None:
    """Compile one chunk MJCF file to an MJB file (runs in a worker process)."""
    import mujoco

    model = mujoco.MjModel.from_xml_path(str(source))
    mujoco.mj_saveModel(model, str(output), None)


def _compile_in_process(index: int, source: Path, output: Path) -> tuple[int, float]:
    """Compile a chunk in a child Python process.

    A plain subprocess, unlike multiprocessing, does not re-run the caller's
    main module, so it works from any entrypoint including the portable
    package's embedded Python.
    """
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "compile-chunk", str(source), str(output)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise WorldHeightError(f"World chunk {index} failed to compile: {result.stderr.strip()[-2000:]}")
    return index, time.monotonic() - started


def load_models(mjcf: Path, *, cache_dir: Path = CACHE_DIR, mujoco=None) -> list:
    """Return the compiled World chunks, compiling them only on a cache miss."""
    if mujoco is None:
        import mujoco
    mjcf = mjcf.resolve()
    # An MJB only loads in the MuJoCo version that wrote it.
    key = f"{fingerprint(mjcf)}-mujoco-{getattr(mujoco, '__version__', 'unknown')}"
    cached = cache_dir / key
    manifest = cached / "manifest.json"
    if manifest.is_file():
        chunks = json.loads(manifest.read_text(encoding="utf-8"))
        if chunks.get("layout") == CACHE_LAYOUT:
            started = time.monotonic()
            models = [mujoco.MjModel.from_binary_path(str(cached / name)) for name in chunks["chunks"]]
            _progress(f"World height model: loaded from cache in {time.monotonic() - started:.1f}s ({cached})")
            return models

    started = time.monotonic()
    texts = split_world(mjcf)
    total = len(texts)
    _progress(
        f"World height model: compiling {mjcf.name} in {total} parallel chunk(s); "
        "this runs once per World and can take a while for a large City",
        current=0, total=total,
    )
    staging = cache_dir / f"{key}.partial"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    names = [f"chunk-{index:02d}.mjb" for index in range(total)]
    try:
        sources = []
        for index, text in enumerate(texts):
            source = staging / f"chunk-{index:02d}.xml"
            source.write_text(text, encoding="utf-8")
            sources.append(source)
        if total == 1:
            compile_chunk(sources[0], staging / names[0])
            _progress("World height model: compiled 1/1", current=1, total=1)
        else:
            with ThreadPoolExecutor(max_workers=total) as pool:
                futures = [
                    pool.submit(_compile_in_process, index, sources[index], staging / names[index])
                    for index in range(total)
                ]
                for done, future in enumerate(as_completed(futures), start=1):
                    index, seconds = future.result()
                    _progress(
                        f"World height model: compiled {done}/{total} "
                        f"(chunk {index} in {seconds:.1f}s, {time.monotonic() - started:.0f}s elapsed)",
                        current=done, total=total,
                    )
        for source in sources:
            source.unlink()
        (staging / "manifest.json").write_text(
            json.dumps({"layout": CACHE_LAYOUT, "mjcf": str(mjcf), "chunks": names}, indent=2) + "\n",
            encoding="utf-8",
        )
        shutil.rmtree(cached, ignore_errors=True)
        staging.replace(cached)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _progress(f"World height model: ready in {time.monotonic() - started:.1f}s ({cached})")
    return [mujoco.MjModel.from_binary_path(str(cached / name)) for name in names]


class WorldHeight:
    """ground(east_m, north_m) on World chunks whose MJCF frame is X=North, Y=-East, Z=Up."""

    def __init__(self, models: list, mujoco=None):
        if mujoco is None:
            import mujoco
        import numpy

        self._mujoco = mujoco
        self._numpy = numpy
        self.models = models
        self.datas = [mujoco.MjData(model) for model in models]
        for model, data in zip(models, self.datas):
            mujoco.mj_forward(model, data)
        self.masks = [self._visual_only_left_out(model) for model in models]
        self.ray_starts = [self._ray_start(model, data) for model, data in zip(models, self.datas)]

    def _ray_start(self, model, data) -> float:
        """Just above the highest point of the model's geoms: each geom's
        local bounding box (geom_aabb: centre, half sizes) turned into the
        world. A bounding sphere is not tight enough: a large terrain's
        radius would put the start hundreds of metres up again."""
        numpy = self._numpy
        if model.ngeom == 0:
            return RAY_START_MARGIN_M
        centre = model.geom_aabb[:, :3]
        half = model.geom_aabb[:, 3:]
        rotation = data.geom_xmat.reshape(-1, 3, 3)
        top = (data.geom_xpos[:, 2] + numpy.einsum("gj,gj->g", rotation[:, 2, :], centre)
               + numpy.einsum("gj,gj->g", numpy.abs(rotation[:, 2, :]), half))
        # A plane's bounding box is unbounded in its own plane; its height is
        # its position.
        planes = model.geom_type == self._mujoco.mjtGeom.mjGEOM_PLANE
        top[planes] = data.geom_xpos[planes, 2]
        return float(numpy.max(top)) + RAY_START_MARGIN_M

    def _visual_only_left_out(self, model):
        """A geom group mask that leaves out the visual-only geoms, moved to a
        group no colliding geom uses, or None when every group is taken.

        Passing through a visual-only geom by starting again just below where
        the ray met it can start the next ray inside a colliding geom under
        it (a lane line painted on a road), which then reports that geom's
        underside instead of its top."""
        numpy = self._numpy
        colliding = (model.geom_contype != 0) | (model.geom_conaffinity != 0)
        visual = numpy.flatnonzero(~colliding)
        if not len(visual):
            return None
        used = set(int(group) for group in model.geom_group[colliding])
        free = next((group for group in range(GEOM_GROUPS - 1, -1, -1) if group not in used), None)
        if free is None:
            return None
        model.geom_group[visual] = free
        mask = numpy.ones(GEOM_GROUPS, dtype=numpy.uint8)
        mask[free] = 0
        return mask

    def _hit(self, model, data, east_m: float, north_m: float, mask=None, start_m: float = 0.0) -> float | None:
        numpy = self._numpy
        point = numpy.array([north_m, -east_m, start_m], dtype=numpy.float64)
        down = numpy.array([0.0, 0.0, -1.0], dtype=numpy.float64)
        geom = numpy.array([-1], dtype=numpy.int32)
        for _ in range(MAX_PASS_THROUGH):
            distance = self._mujoco.mj_ray(model, data, point, down, mask, 1, -1, geom)
            if distance < 0:
                return None
            hit = point[2] - distance
            index = int(geom[0])
            if model.geom_contype[index] or model.geom_conaffinity[index]:
                return float(hit)
            # A visual-only geom: continue below it.
            point[2] = hit - 1.0e-6
        raise WorldHeightError(f"too many non-colliding geoms above east={east_m} m, north={north_m} m")

    def __call__(self, east_m: float, north_m: float) -> float:
        hits = [
            hit for hit in (self._hit(model, data, east_m, north_m, mask, start)
                            for model, data, mask, start in zip(self.models, self.datas, self.masks, self.ray_starts))
            if hit is not None
        ]
        if not hits:
            raise WorldHeightError(f"no World geometry below east={east_m} m, north={north_m} m")
        return max(hits)


def ray_ground(mjcf: Path, *, cache_dir: Path = CACHE_DIR) -> Callable[[float, float], float]:
    return WorldHeight(load_models(mjcf, cache_dir=cache_dir))


if __name__ == "__main__":
    # Worker entrypoint used by _compile_in_process.
    if len(sys.argv) != 4 or sys.argv[1] != "compile-chunk":
        raise SystemExit("usage: world_height.py compile-chunk <chunk.xml> <chunk.mjb>")
    compile_chunk(Path(sys.argv[2]), Path(sys.argv[3]))
