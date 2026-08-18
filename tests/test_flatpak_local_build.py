"""Focused checks for the non-release Flatpak manifest resolution path."""
# SPDX-License-Identifier: MIT

from __future__ import annotations

import shutil
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - matches build script prerequisite
    yaml = None

from bol.config import VERSION, WINEGDK_BUILD_REV


ROOT = Path(__file__).resolve().parents[1]
APPID = "io.github.wyze3306.BedrockOnLinux"


def _flatpak_payload_check_code():
    """Extract the exact in-sandbox verifier without requiring Flatpak."""
    script = (ROOT / "scripts/build-flatpak.sh").read_text(encoding="utf-8")
    match = re.search(
        r'^PAYLOAD_CHECK_CODE="\$\(cat <<\'PY\'\n(.*?)\nPY\n\)"$',
        script,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise AssertionError("Flatpak payload verifier here-doc not found")
    return match.group(1)


def test_payload_audit_uses_finished_tree_without_builder_wrapper():
    script = (ROOT / "scripts/build-flatpak.sh").read_text(encoding="utf-8")
    audit = script[script.index("flatpak build \"$WORK/builddir\""):
                   script.index("flatpak build-bundle")]
    assert "/app/bin/python3 -c \"$PAYLOAD_CHECK_CODE\"" in audit
    assert '"${FB[@]}" --run' not in script


def _regular_hashes(root):
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_file() and not path.is_symlink():
            files[relative.as_posix()] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return files


class FlatpakPayloadAuditTests(unittest.TestCase):
    """Exercise the pre-bundle payload gate with ordinary temp directories."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.expected = self.root / "expected"
        self.expected.mkdir()
        (self.expected / "config.py").write_text(
            f'VERSION = "{VERSION}"\n'
            f'WINEGDK_BUILD_REV = "{WINEGDK_BUILD_REV}"\n',
            encoding="utf-8",
        )
        (self.expected / "gpu_safety.py").write_text(
            "# guarded launch\n", encoding="utf-8")
        (self.expected / "nested").mkdir()
        (self.expected / "nested/wine_registry.py").write_text(
            "# offline registry\n", encoding="utf-8")
        self.manifest = json.dumps(_regular_hashes(self.expected),
                                   sort_keys=True, separators=(",", ":"))

    def tearDown(self):
        self.tempdir.cleanup()

    def _run(self, actual):
        return subprocess.run(
            ["python3", "-c", _flatpak_payload_check_code(), str(actual),
             self.manifest, VERSION, WINEGDK_BUILD_REV],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_accepts_exact_regular_file_set_and_bytes(self):
        actual = self.root / "exact"
        shutil.copytree(self.expected, actual)
        result = self._run(actual)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verified byte-for-byte", result.stdout)

    def test_rejects_missing_stale_and_extra_files(self):
        mutations = {
            "missing": lambda root: (root / "gpu_safety.py").unlink(),
            "stale": lambda root: (root / "nested/wine_registry.py").write_text(
                "# stale\n", encoding="utf-8"),
            "extra": lambda root: (root / "obsolete.py").write_text(
                "# obsolete\n", encoding="utf-8"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                actual = self.root / label
                shutil.copytree(self.expected, actual)
                mutate(actual)
                result = self._run(actual)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(label + ":", result.stderr)

    def test_rejects_build_host_bytecode_even_when_sources_match(self):
        actual = self.root / "bytecode"
        shutil.copytree(self.expected, actual)
        cache = actual / "__pycache__"
        cache.mkdir()
        (cache / "config.cpython-313.pyc").write_bytes(b"host bytecode")
        result = self._run(actual)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("build-host bytecode", result.stderr)


@unittest.skipIf(yaml is None, "PyYAML not installed")
class FlatpakLocalManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.checkout = Path(self.tmpdir.name)
        (self.checkout / "scripts").mkdir()
        (self.checkout / "flatpak").mkdir()
        (self.checkout / "bol").mkdir()
        (self.checkout / "data").mkdir()

        shutil.copy2(ROOT / "scripts/build-flatpak.sh",
                     self.checkout / "scripts/build-flatpak.sh")
        shutil.copy2(ROOT / f"flatpak/{APPID}.yml",
                     self.checkout / f"flatpak/{APPID}.yml")
        (self.checkout / "bol/config.py").write_text(
            f'VERSION = "{VERSION}"\n'
            f'WINEGDK_BUILD_REV = "{WINEGDK_BUILD_REV}"\n',
            encoding="utf-8",
        )
        (self.checkout / "bedrock-on-linux").write_text(
            "#!/usr/bin/env python3\n", encoding="utf-8")
        (self.checkout / "LICENSE").write_text(
            "fixture license\n", encoding="utf-8")
        (self.checkout / "data/icon.png").write_bytes(b"fixture")
        (self.checkout / f"flatpak/{APPID}.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=fixture\n",
            encoding="utf-8",
        )
        (self.checkout / f"flatpak/{APPID}.metainfo.xml").write_text(
            "<component type=\"desktop-application\"/>\n", encoding="utf-8")

        # This intentionally has the same name as the maintainer's untracked
        # release draft. The local path must neither read nor rewrite it.
        self.release_draft = (
            self.checkout / f"flatpak/.{APPID}.release.yml")
        self.release_draft.write_text("DO NOT TOUCH\n", encoding="utf-8")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _run(self, option, appstream="0"):
        env = dict(os.environ, BOL_FLATPAK_APPSTREAM=appstream)
        return subprocess.run(
            ["bash", str(self.checkout / "scripts/build-flatpak.sh"), option],
            cwd=self.checkout,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def _resolved_app(self):
        resolved = self.checkout / f"flatpak/.{APPID}.resolved.yml"
        self.assertTrue(resolved.is_file())
        manifest = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        return manifest["modules"][-1]

    def test_resolve_only_uses_working_tree_and_preserves_release_draft(self):
        result = self._run("--resolve-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.release_draft.read_text(encoding="utf-8"),
                         "DO NOT TOUCH\n")

        resolved = self.checkout / f"flatpak/.{APPID}.resolved.yml"
        self.assertTrue(resolved.is_file())
        manifest = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        app = manifest["modules"][-1]
        self.assertEqual(app["name"], "bedrock-on-linux")
        self.assertEqual(
            app["sources"],
            [
                {"type": "file", "path": "../bedrock-on-linux"},
                {"type": "file", "path": "../LICENSE"},
                {"type": "dir", "path": "../bol", "dest": "bol"},
                {"type": "file", "path": "../data/icon.png",
                 "dest": "data"},
                {"type": "file", "path": f"{APPID}.desktop",
                 "dest": "flatpak"},
            ],
        )
        self.assertFalse(any(source.get("type") == "git"
                             for source in app["sources"]))
        commands = "\n".join(app["build-commands"])
        self.assertIn("-name __pycache__", commands)
        self.assertIn("-name '*.pyc' -delete", commands)
        bol_source = next(source for source in app["sources"]
                          if source.get("dest") == "bol")
        self.assertEqual((resolved.parent / bol_source["path"]).resolve(),
                         (self.checkout / "bol").resolve())
        self.assertIn(f"VERSION={VERSION}", result.stdout)
        self.assertIn(f"WINEGDK_BUILD_REV={WINEGDK_BUILD_REV}", result.stdout)
        self.assertIn("no build or release performed", result.stdout)
        self.assertNotIn("metainfo", "\n".join(app["build-commands"]))

    def test_working_tree_build_keeps_metainfo_when_appstream_is_available(self):
        # The nightly builds this path, and a bundle without AppStream data was
        # why the release build was pinned to the tracked manifest in the first
        # place; keep the metadata instead of losing it again.
        result = self._run("--resolve-only", appstream="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        app = self._resolved_app()
        self.assertIn(
            {"type": "file", "path": f"{APPID}.metainfo.xml",
             "dest": "flatpak"},
            app["sources"],
        )
        self.assertIn(f"{APPID}.metainfo.xml",
                      "\n".join(app["build-commands"]))
        self.assertFalse(any(source.get("type") == "git"
                             for source in app["sources"]))
        self.assertIn("metainfo=kept", result.stdout)

    def test_release_mode_refuses_old_tracked_tag_before_any_build(self):
        manifest_path = self.checkout / f"flatpak/{APPID}.yml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        app = manifest["modules"][-1]
        git_source = next(source for source in app["sources"]
                          if source.get("type") == "git")
        old_tag = "v0.0.0"
        self.assertNotEqual(old_tag, f"v{VERSION}")
        git_source["tag"] = old_tag
        manifest_path.write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

        result = self._run("--release")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"pins {old_tag!r}", result.stderr)
        self.assertIn(f"expected 'v{VERSION}'", result.stderr)
        self.assertIn("refusing a mislabeled release build", result.stderr)
        self.assertEqual(self.release_draft.read_text(encoding="utf-8"),
                         "DO NOT TOUCH\n")


if __name__ == "__main__":
    unittest.main()
