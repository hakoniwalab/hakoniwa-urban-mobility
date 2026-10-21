#!/usr/bin/env python3
"""Compile only a City MJCF with fusestatic and emit its canonical MJCF."""

from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import mujoco


def main() -> None:
    source, output = map(Path, sys.argv[1:3])
    source_root = ET.parse(source).getroot()
    source_compiler = source_root.find("compiler")
    spec = mujoco.MjSpec.from_file(str(source.resolve()))
    spec.compiler.fusestatic = True
    model = spec.compile()
    output.parent.mkdir(parents=True, exist_ok=True)
    canonical = ET.fromstring(spec.to_xml())
    compiler = canonical.find("compiler")
    assetdir = source_compiler.get("assetdir") if source_compiler is not None else None
    for asset in canonical.findall(".//asset/*[@file]"):
        raw = Path(asset.get("file"))
        if raw.is_absolute():
            continue
        directory = assetdir
        if source_compiler is not None and asset.tag == "mesh":
            directory = source_compiler.get("meshdir") or directory
        if source_compiler is not None and asset.tag == "texture":
            directory = source_compiler.get("texturedir") or directory
        base = source.parent / directory if directory else source.parent
        asset.set("file", str((base / raw).resolve()))
    if compiler is not None:
        for attribute in ("assetdir", "meshdir", "texturedir"):
            compiler.attrib.pop(attribute, None)
    ET.indent(canonical, space="  ")
    ET.ElementTree(canonical).write(output, encoding="utf-8", xml_declaration=True)
    print(f"City fusestatic: {model.nbody} bodies")


if __name__ == "__main__":
    main()
