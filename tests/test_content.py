"""Launcher-side Minecraft content import regressions."""
# SPDX-License-Identifier: MIT

import json
import zipfile

from bol import content


def test_mcskin_archive_is_installed_as_a_skin_pack(tmp_path):
    archive = tmp_path / "example.mcskin"
    manifest = {
        "format_version": 2,
        "header": {
            "name": "Example Skin",
            "description": "test",
            "uuid": "a598d32f-af25-4baa-8e03-b0115d761709",
            "version": [1, 0, 0],
            "min_engine_version": [1, 20, 0],
        },
        "modules": [{
            "type": "skin_pack",
            "uuid": "db908a93-7055-46a6-b716-7d4b6f6fa334",
            "version": [1, 0, 0],
        }],
    }
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))
        package.writestr("skins.json", "{}")

    prefix = tmp_path / "prefix"
    result = content.import_content(archive, prefix=prefix)

    destination = (
        content._mojang_dir(prefix) / "skin_packs" / "Example Skin"
    )
    assert result == ["skin pack: Example Skin"]
    assert json.loads((destination / "manifest.json").read_text()) == manifest
