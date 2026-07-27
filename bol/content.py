"""bol.content — import of .mcpack/.mcworld/.mcaddon/.mcskin content."""
# SPDX-License-Identifier: MIT

import json
import os
import re
import shutil
import zipfile
from pathlib import Path

from .log import die, ok, warn
from .prefix import _mc_running, active_prefix

# Minecraft Bedrock normally imports these by "opening" the file, which has no
# handler under Wine — so worlds/packs can't be imported in-game. We unpack
# them straight into the game's com.mojang folders instead.
COM_MOJANG_REL = ("drive_c/users/steamuser/AppData/Roaming/Minecraft Bedrock/"
                  "Users/Shared/games/com.mojang")


def _mojang_dir(prefix=None):
    return (prefix or active_prefix()) / COM_MOJANG_REL


def _safe_component(name):
    """A filesystem-safe folder name derived from a pack/world name."""
    name = re.sub(r"[^\w .()\-]+", "_", (name or "").strip()) or "imported"
    return name[:96]


def _unique_path(p: Path):
    if not p.exists():
        return p
    for i in range(2, 1000):
        cand = p.with_name(f"{p.name} ({i})")
        if not cand.exists():
            return cand
    return p.with_name(f"{p.name} ({os.getpid()})")


def _pack_subfolder(manifest):
    """The com.mojang subfolder a pack belongs in, from its manifest modules."""
    types = set()
    try:
        for m in manifest.get("modules", []):
            t = (m.get("type") or "").lower()
            if t:
                types.add(t)
    except Exception:
        pass
    if types & {"skin_pack", "skins"}:
        return "skin_packs"
    if types & {"data", "script", "client_data"}:
        return "behavior_packs"
    if types & {"world_template"}:
        return "world_templates"
    return "resource_packs"


def _install_pack_tree(pack_root: Path, base: Path, fallback_name: str):
    """Move one extracted pack (dir containing manifest.json) into com.mojang."""
    manifest = {}
    mf = pack_root / "manifest.json"
    if mf.exists():
        try:
            manifest = json.loads(mf.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            manifest = {}
    sub = _pack_subfolder(manifest)
    try:
        nm = manifest.get("header", {}).get("name") or fallback_name
    except Exception:
        nm = fallback_name
    dest = _unique_path(base / sub / _safe_component(str(nm)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pack_root), str(dest))
    return sub, dest.name


def import_content(src, prefix=None):
    """Import a .mcpack/.mcaddon/.mcworld/.mctemplate/.mcskin into the game.

    Returns a list of human-readable result strings.
    """
    src = Path(src).expanduser()
    if not src.is_file():
        die(f"File not found: {src}")
    if not zipfile.is_zipfile(src):
        die(f"Not a Minecraft content file (not a zip): {src.name}")
    base = _mojang_dir(prefix)
    base.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()
    stem = src.stem
    results = []

    if ext in (".mcworld", ".mctemplate"):
        sub = "minecraftWorlds" if ext == ".mcworld" else "world_templates"
        dest = _unique_path(base / sub / _safe_component(stem))
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src) as z:
            z.extractall(dest)
        kind = "world" if ext == ".mcworld" else "world template"
        results.append(f"{kind}: {dest.name}")
        ok(f"Imported {kind} → {dest.name}")
        return results

    tmp = base / ".bol-import-tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        manifests = sorted(tmp.rglob("manifest.json"),
                           key=lambda p: len(p.parts))
        claimed, roots = [], []
        for mf in manifests:
            r = mf.parent
            if any(str(r).startswith(str(c) + os.sep) for c in claimed):
                continue
            claimed.append(r)
            roots.append(r)
        if not roots:
            die(f"No manifest.json in {src.name} — not a valid pack/addon.")
        for r in roots:
            sub, nm = _install_pack_tree(r, base, stem)
            label = sub.rstrip("s").replace("_", " ")
            results.append(f"{label}: {nm}")
            ok(f"Imported {label} → {nm}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return results


def cmd_import(paths):
    if not paths:
        die("Usage: bedrock-on-linux import "
            "<file.mcpack|.mcworld|.mcaddon|.mctemplate|.mcskin> …")
    if _mc_running():
        warn("Minecraft appears to be running — close it before importing so "
             "the game picks up new content on next launch.")
    total = []
    for p in paths:
        total += import_content(p)
    ok(f"Done — imported {len(total)} item(s) into {_mojang_dir()}")
