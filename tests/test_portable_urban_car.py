from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import portable_urban_car


class PortableUrbanCarTest(unittest.TestCase):
    def test_foundation_core_mmap_is_relocated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            config = package / (
                "hakoniwa-business-pack/work/foundation/config/cpp_core_config.json"
            )
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps({"shm_type": "mmap", "core_mmap_path": "old"}),
                encoding="utf-8",
            )
            with mock.patch.object(portable_urban_car, "PACKAGE_ROOT", package):
                portable_urban_car.relocate_foundation_config()
            payload = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(
                Path(payload["core_mmap_path"]),
                package.resolve()
                / "hakoniwa-business-pack/work/foundation/runtime/mmap",
            )

    def test_replace_root_rewrites_values_and_dictionary_keys(self) -> None:
        source = r"C:\source\job\build"
        destination = r"D:\portable\data\build"
        value = {
            "mjcf": {"path": source + r"\world\city-world.xml"},
            source + r"\components\terrain\terrain.glb": 1,
        }
        actual = portable_urban_car._replace_root(value, source, destination)
        self.assertEqual(
            actual["mjcf"]["path"],
            str(Path(destination) / "world/city-world.xml"),
        )
        self.assertIn(
            str(Path(destination) / "components/terrain/terrain.glb"), actual
        )

    def test_relocate_receipt_uses_current_package_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            build = package / "portable-data/city-world/build"
            template = build / "world/city-world-receipt.json"
            template.parent.mkdir(parents=True)
            old = r"C:\old\job\build"
            template.write_text(
                json.dumps({"mjcf": {"path": old + r"\world\city-world.xml"}}),
                encoding="utf-8",
            )
            metadata = package / "portable-data/city-world.json"
            metadata.write_text(
                json.dumps(
                    {
                        "source_build_root": old,
                        "receipt_relative": "world/city-world-receipt.json",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(portable_urban_car, "PACKAGE_ROOT", package), mock.patch.object(
                portable_urban_car, "METADATA", metadata
            ):
                output = portable_urban_car.relocate_receipt()
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                Path(payload["mjcf"]["path"]),
                build.resolve() / "world/city-world.xml",
            )


if __name__ == "__main__":
    unittest.main()
