#!/usr/bin/env bash
# Build the BedrockOnLinux Flatpak.
#
#   scripts/build-flatpak.sh             # local dev + nightly candidate: the
#                                        # app module is this working tree
#   scripts/build-flatpak.sh --resolve-only  # generate/check local manifest only
#   scripts/build-flatpak.sh --release   # exact Flathub manifest (needs the
#                                        # git tag referenced in the manifest,
#                                        # and a checkout equal to it)
#
# Output: dist/BedrockOnLinux-<ver>-x86_64.flatpak. Uses the flatpak-builder
# binary if present, else the no-root org.flatpak.Builder Flatpak.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VER="$(grep -m1 '^VERSION = ' "$SRC/bol/config.py" | cut -d'"' -f2)"
REV="$(grep -m1 '^WINEGDK_BUILD_REV = ' "$SRC/bol/config.py" | cut -d'"' -f2)"
APPID="io.github.wyze3306.BedrockOnLinux"
MANIFEST="$SRC/flatpak/$APPID.yml"
DEV_MANIFEST="$SRC/flatpak/.$APPID.resolved.yml"   # same dir → ../ paths resolve
RTVER="$(grep -m1 'runtime-version:' "$MANIFEST" | tr -d "'\" " | cut -d: -f2)"
OUT="$SRC/dist"
WORK="$OUT/flatpak-build"
BUNDLE="$OUT/BedrockOnLinux-${VER}-x86_64.flatpak"

[[ -n "$VER" && -n "$REV" ]] || {
  echo "could not read VERSION/WINEGDK_BUILD_REV from bol/config.py" >&2
  exit 1
}

MODE="local"
case "${1:-}" in
  "") ;;
  --resolve-only) MODE="resolve-only" ;;
  --release) MODE="release" ;;
  *)
    echo "usage: scripts/build-flatpak.sh [--resolve-only|--release]" >&2
    exit 2
    ;;
esac
(( $# <= 1 )) || {
  echo "usage: scripts/build-flatpak.sh [--resolve-only|--release]" >&2
  exit 2
}

if [[ "$MODE" == "release" ]]; then
  # The release path intentionally uses only the tracked Flathub manifest.  It
  # must never label an older pinned source with the checkout's current VERSION.
  python3 -c 'import yaml' 2>/dev/null || {
    echo "needs PyYAML (e.g. sudo apt install python3-yaml)" >&2; exit 1; }
  python3 - "$MANIFEST" "$VER" <<'PY'
import sys, yaml

manifest, version = sys.argv[1:]
with open(manifest, encoding="utf-8") as source:
    data = yaml.safe_load(source)
app = data["modules"][-1]
if app.get("name") != "bedrock-on-linux":
    raise SystemExit("app module must be last in the tracked manifest")
git_sources = [item for item in app.get("sources", [])
               if item.get("type") == "git"]
if len(git_sources) != 1:
    raise SystemExit("tracked manifest must contain exactly one app git source")
source = git_sources[0]
expected_tag = "v" + version
if source.get("tag") != expected_tag:
    raise SystemExit(
        f"tracked Flatpak manifest pins {source.get('tag')!r}, expected "
        f"{expected_tag!r}; refusing a mislabeled release build")
if not source.get("commit"):
    raise SystemExit("tracked Flatpak release source has no pinned commit")
print(f"tracked release manifest: {source['tag']} @ {source['commit']}")
PY
  BUILD_MANIFEST="$MANIFEST"
else
  # Local build: swap the app module's pinned git source for this working tree.
  python3 -c 'import yaml' 2>/dev/null || {
    echo "needs PyYAML (e.g. sudo apt install python3-yaml)" >&2; exit 1; }
  # Composing AppStream metadata needs appstreamcli; a plain dev host may have
  # no such helper, but CI and org.flatpak.Builder do. Keep the metainfo
  # whenever it can actually be composed, so a candidate bundle built from the
  # working tree carries the same store metadata as the released one.
  # BOL_FLATPAK_APPSTREAM=0/1 overrides the probe.
  case "${BOL_FLATPAK_APPSTREAM:-auto}" in
    auto) APPSTREAM="no"; command -v appstreamcli >/dev/null && APPSTREAM="yes" ;;
    0) APPSTREAM="no" ;;
    1) APPSTREAM="yes" ;;
    *)
      echo "BOL_FLATPAK_APPSTREAM must be 0, 1 or unset" >&2
      exit 2
      ;;
  esac
  python3 - "$MANIFEST" "$DEV_MANIFEST" "$SRC" "$APPSTREAM" <<'PY'
import ast
from pathlib import Path
import sys
import yaml

src, dst, checkout, appstream = sys.argv[1:]
checkout = Path(checkout).resolve()
keep_metainfo = appstream == "yes"
with open(src, encoding="utf-8") as source:
    manifest = yaml.safe_load(source)

config_path = checkout / "bol/config.py"
tree = ast.parse(config_path.read_text(encoding="utf-8"), filename=str(config_path))
metadata = {}
for node in tree.body:
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        continue
    target = node.targets[0]
    if isinstance(target, ast.Name) and target.id in {
            "VERSION", "WINEGDK_BUILD_REV"}:
        value = ast.literal_eval(node.value)
        if isinstance(value, str):
            metadata[target.id] = value
missing = {"VERSION", "WINEGDK_BUILD_REV"} - metadata.keys()
if missing:
    raise SystemExit("bol/config.py lacks literal " + ", ".join(sorted(missing)))

required = [
    checkout / "bedrock-on-linux",
    checkout / "LICENSE",
    checkout / "bol",
    checkout / "data/icon.png",
    checkout / "flatpak/io.github.wyze3306.BedrockOnLinux.desktop",
]
if keep_metainfo:
    required.append(
        checkout / "flatpak/io.github.wyze3306.BedrockOnLinux.metainfo.xml")
for path in required:
    if not path.exists():
        raise SystemExit(f"local Flatpak source missing: {path}")

# Local builds run flatpak-builder as the real user (not uid-0 in a sandbox),
# so eu-strip cannot open Tcl/Tk's 0555-mode .so files read-write.
manifest["build-options"] = {"strip": False, "no-debuginfo": True}
app = manifest["modules"][-1]
if app.get("name") != "bedrock-on-linux":
    raise SystemExit("app module must be last")
app["sources"] = [
    {"type": "file", "path": "../bedrock-on-linux"},
    {"type": "file", "path": "../LICENSE"},
    {"type": "dir", "path": "../bol", "dest": "bol"},
    {"type": "file", "path": "../data/icon.png", "dest": "data"},
    {"type": "file", "path": "io.github.wyze3306.BedrockOnLinux.desktop",
     "dest": "flatpak"},
]
if keep_metainfo:
    app["sources"].append(
        {"type": "file",
         "path": "io.github.wyze3306.BedrockOnLinux.metainfo.xml",
         "dest": "flatpak"})
else:
    # Without appstreamcli, flatpak-builder's appstream compose stage fails on
    # a plain host. Drop the metainfo there; it is validated separately.
    app["build-commands"] = [command for command in app["build-commands"]
                             if "metainfo" not in command]
with open(dst, "w", encoding="utf-8") as output:
    yaml.safe_dump(manifest, output, sort_keys=False)
print(f"local manifest -> {dst}")
print(f"  VERSION={metadata['VERSION']}  "
      f"WINEGDK_BUILD_REV={metadata['WINEGDK_BUILD_REV']}  "
      f"metainfo={'kept' if keep_metainfo else 'dropped (no appstreamcli)'}")
PY
  BUILD_MANIFEST="$DEV_MANIFEST"
fi

if [[ "$MODE" == "resolve-only" ]]; then
  echo "Local Flatpak manifest resolved; no build or release performed."
  exit 0
fi

if command -v flatpak-builder >/dev/null; then
  FB=(flatpak-builder)
elif flatpak info org.flatpak.Builder >/dev/null 2>&1; then
  # --device=all gives /dev/fuse, which flatpak-builder's rofiles-fuse overlay
  # needs (org.flatpak.Builder ships none → eu-strip 'Permission denied').
  FB=(flatpak run --device=all org.flatpak.Builder)
else
  echo "– Flatpak skipped: no builder. Install one of:"
  echo "    flatpak install -y flathub org.flatpak.Builder      # no root"
  echo "    sudo apt install flatpak-builder                    # Debian/Ubuntu"
  exit 0
fi

mkdir -p "$WORK"

flatpak remote-add --user --if-not-exists flathub \
  https://flathub.org/repo/flathub.flatpakrepo 2>/dev/null || true
flatpak install -y --user --noninteractive flathub \
  org.freedesktop.Platform//"$RTVER" org.freedesktop.Sdk//"$RTVER" 2>/dev/null || \
  echo "  (runtime/SDK pre-install skipped — the builder will fetch them)"

# The checkout may live on eCryptfs or another FUSE-backed home directory.
# Nesting flatpak-builder's read-only FUSE overlay there can leave 0555 Tcl/Tk
# libraries inaccessible to eu-strip. The regular build sandbox still isolates
# every module; disabling only that optional overlay makes local release builds
# deterministic on both native and encrypted home filesystems.
BUILD_FLAGS=(--user --force-clean --disable-rofiles-fuse)

rm -f -- "$BUNDLE"
"${FB[@]}" "${BUILD_FLAGS[@]}" --repo="$WORK/repo" \
  "$WORK/builddir" "$BUILD_MANIFEST"

# Inspect the installed tree in flatpak-builder's sandbox, not merely the host
# source or output filename.  Bind the complete regular-file payload to the
# checkout after the build has finished: VERSION/revision equality alone cannot
# detect a cached bol/ tree which omitted a newly added safety module.
EXPECTED_BOL_HASHES="$(python3 - "$SRC/bol" <<'PY'
import hashlib
import json
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
files = {}
for path in sorted(root.rglob("*")):
    relative = path.relative_to(root)
    if "__pycache__" in relative.parts or path.suffix == ".pyc":
        continue
    mode = path.lstat().st_mode
    if stat.S_ISREG(mode):
        files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps(files, sort_keys=True, separators=(",", ":")))
PY
)"
[[ -n "$EXPECTED_BOL_HASHES" ]] || {
  echo "could not hash the checkout bol/ payload" >&2
  exit 1
}

PAYLOAD_CHECK_CODE="$(cat <<'PY'
import ast
import hashlib
import json
from pathlib import Path
import stat
import sys

root = Path(sys.argv[1])
try:
    expected_files = json.loads(sys.argv[2])
except (TypeError, ValueError) as exc:
    raise SystemExit(f"invalid expected bol/ payload manifest: {exc}")
if not isinstance(expected_files, dict) or not expected_files:
    raise SystemExit("expected bol/ payload manifest is empty or invalid")

actual_files = {}
for candidate in sorted(root.rglob("*")):
    relative = candidate.relative_to(root)
    if "__pycache__" in relative.parts or candidate.suffix == ".pyc":
        raise SystemExit(
            f"Flatpak bol/ payload contains build-host bytecode: {relative}")
    try:
        mode = candidate.lstat().st_mode
    except OSError as exc:
        raise SystemExit(f"cannot inspect Flatpak payload {candidate}: {exc}")
    if not stat.S_ISREG(mode):
        continue
    try:
        actual_files[relative.as_posix()] = hashlib.sha256(
            candidate.read_bytes()).hexdigest()
    except OSError as exc:
        raise SystemExit(f"cannot hash Flatpak payload {candidate}: {exc}")

missing = sorted(expected_files.keys() - actual_files.keys())
extra = sorted(actual_files.keys() - expected_files.keys())
stale = sorted(name for name in expected_files.keys() & actual_files.keys()
               if expected_files[name] != actual_files[name])
if missing or stale or extra:
    details = []
    for label, names in (("missing", missing), ("stale", stale),
                         ("extra", extra)):
        if names:
            details.append(f"{label}: " + ", ".join(names))
    raise SystemExit("Flatpak bol/ payload differs from checkout; "
                     + "; ".join(details))

path = root / "config.py"
tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
values = {}
for node in tree.body:
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        continue
    target = node.targets[0]
    if isinstance(target, ast.Name) and target.id in {
            "VERSION", "WINEGDK_BUILD_REV"}:
        value = ast.literal_eval(node.value)
        if isinstance(value, str):
            values[target.id] = value
expected = {"VERSION": sys.argv[3], "WINEGDK_BUILD_REV": sys.argv[4]}
if values != expected:
    raise SystemExit(f"Flatpak payload metadata mismatch: {values!r} != {expected!r}")
print("Flatpak payload verified byte-for-byte:", values["VERSION"],
      values["WINEGDK_BUILD_REV"], f"({len(actual_files)} bol/ files)")
PY
)"
# Run the finished tree with the host Flatpak command.  The
# org.flatpak.Builder wrapper can return status 1 after a successful --run
# when the local development manifest intentionally has no AppStream export;
# `flatpak build` reports the verifier's real exit status directly.
flatpak build "$WORK/builddir" \
  /app/bin/python3 -c "$PAYLOAD_CHECK_CODE" \
  /app/lib/bedrock-on-linux/bol "$EXPECTED_BOL_HASHES" "$VER" "$REV"

flatpak build-bundle "$WORK/repo" "$BUNDLE" "$APPID"
[[ -s "$BUNDLE" ]] || { echo "Flatpak bundle was not created" >&2; exit 1; }

echo
echo "Built: $BUNDLE"
echo "Install: flatpak install --user $BUNDLE"
echo "Run:     flatpak run $APPID"
