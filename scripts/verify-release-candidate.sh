#!/usr/bin/env bash
# Verify that packaged application candidates embed this checkout's metadata.
#
# With no arguments, require and inspect the current .deb, .rpm, .pyz and
# AppImage in dist/.  Explicit arguments verify exactly those supported
# artifacts, which is useful for build-release.sh when an optional format could
# not be produced.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$SRC/dist"

die() {
  echo "!! $*" >&2
  exit 1
}

read_config_metadata() {
  python3 - "$1" <<'PY'
import ast
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
try:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
except (OSError, SyntaxError, UnicodeError) as exc:
    raise SystemExit(f"cannot parse {path}: {exc}")

wanted = {
    "VERSION",
    "WINEGDK_BUILD_REV",
    "WINEGDK_SOURCE_COMMIT",
    "WINEGDK_ARCHIVE_SHA256",
    "OPENSSL_XCURL_REV",
    "OPENSSL_XCURL_ARCHIVE_SHA256",
    "UMU_VERSION",
    "UMU_ARCHIVE_SHA256",
    "UMU_RUN_SHA256",
}
values = {}
for node in tree.body:
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        continue
    target = node.targets[0]
    if isinstance(target, ast.Name) and target.id in wanted:
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
        if isinstance(value, str):
            values[target.id] = value

missing = sorted(wanted - values.keys())
if missing:
    raise SystemExit(f"{path} has no literal assignment for {', '.join(missing)}")
print("\t".join((values["VERSION"], values["WINEGDK_BUILD_REV"],
                 values["WINEGDK_SOURCE_COMMIT"],
                 values["WINEGDK_ARCHIVE_SHA256"],
                 values["OPENSSL_XCURL_REV"],
                 values["OPENSSL_XCURL_ARCHIVE_SHA256"],
                 values["UMU_VERSION"], values["UMU_ARCHIVE_SHA256"],
                 values["UMU_RUN_SHA256"])))
PY
}

expected_metadata="$(read_config_metadata "$SRC/bol/config.py")" \
  || die "could not read release metadata from bol/config.py"
IFS=$'\t' read -r expected_version expected_rev expected_source_commit \
  expected_archive_sha expected_xcurl_rev expected_xcurl_sha \
  expected_umu_version expected_umu_archive_sha expected_umu_run_sha \
  <<< "$expected_metadata"
[[ -n "$expected_version" && -n "$expected_rev" ]] \
  || die "empty VERSION or WINEGDK_BUILD_REV in bol/config.py"
[[ "$expected_archive_sha" =~ ^[0-9a-f]{64}$ ]] \
  || die "WINEGDK_ARCHIVE_SHA256 is not a lowercase SHA-256 pin"
for pin in "$expected_xcurl_sha" "$expected_umu_archive_sha" \
    "$expected_umu_run_sha"; do
  [[ "$pin" =~ ^[0-9a-f]{64}$ ]] \
    || die "application dependency metadata contains an invalid SHA-256 pin"
done

if (( $# == 0 )); then
  set -- \
    "$OUT/bedrock-on-linux_${expected_version}_amd64.deb" \
    "$OUT/bedrock-on-linux-${expected_version}-1.x86_64.rpm" \
    "$OUT/bedrock-on-linux-${expected_version}.pyz" \
    "$OUT/BedrockOnLinux-${expected_version}-x86_64.AppImage"
fi

tmp="$(mktemp -d "${TMPDIR:-/tmp}/bol-candidate-verify.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

index=0
for supplied in "$@"; do
  [[ -f "$supplied" ]] || die "candidate artifact missing: $supplied"
  artifact="$(readlink -f -- "$supplied")" \
    || die "could not resolve candidate path: $supplied"
  name="$(basename "$artifact")"
  work="$tmp/artifact-$index"
  root="$work/root"
  mkdir -p "$root"
  config=""
  license=""
  payload_bol=""
  desktop=""
  icon=""
  launcher=""

  case "$name" in
    *.pyz)
      [[ -x "$artifact" ]] || die "$name is not executable"
      config="$root/bol/config.py"
      license="$root/LICENSE"
      payload_bol="$root/bol"
      icon="$root/data/icon.png"
      launcher="$artifact"
      if ! python3 - "$artifact" "$root" <<'PY'
import pathlib
import stat
import sys
import zipfile

archive, destination = map(pathlib.Path, sys.argv[1:])
try:
    with zipfile.ZipFile(archive) as candidate:
        entries = candidate.infolist()
        licenses = [entry for entry in entries
                    if entry.filename == "LICENSE" and not entry.is_dir()]
        if len(licenses) != 1:
            raise ValueError("zipapp must contain exactly one LICENSE")
        (destination / "LICENSE").write_bytes(candidate.read(licenses[0]))
        icons = [entry for entry in entries
                 if entry.filename == "data/icon.png" and not entry.is_dir()]
        if len(icons) != 1:
            raise ValueError("zipapp must contain exactly one data/icon.png")
        icon = destination / "data/icon.png"
        icon.parent.mkdir(parents=True, exist_ok=True)
        icon.write_bytes(candidate.read(icons[0]))

        seen = set()
        for entry in entries:
            name = entry.filename
            pure = pathlib.PurePosixPath(name)
            if not pure.parts or pure.parts[0] != "bol":
                continue
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"unsafe bol/ zipapp member: {name}")
            if entry.is_dir():
                continue
            relative = pure.as_posix()
            if "__pycache__" in pure.parts or pure.suffix == ".pyc":
                continue
            mode = (entry.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            if file_type not in (0, stat.S_IFREG):
                raise ValueError(f"non-regular bol/ zipapp member: {name}")
            if relative in seen:
                raise ValueError(f"duplicate bol/ zipapp member: {relative}")
            seen.add(relative)
            output = destination.joinpath(*pure.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(candidate.read(entry))
except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
    raise SystemExit(f"cannot read required zipapp files from {archive}: {exc}")
PY
      then
        die "$name is not a valid BedrockOnLinux zipapp"
      fi
      ;;

    *.deb)
      command -v dpkg-deb >/dev/null \
        || die "dpkg-deb is required to inspect $name"
      deb_version="$(dpkg-deb -f "$artifact" Version)" \
        || die "could not read Debian control metadata from $name"
      deb_arch="$(dpkg-deb -f "$artifact" Architecture)" \
        || die "could not read Debian architecture from $name"
      [[ "$deb_version" == "$expected_version" ]] \
        || die "$name control Version=$deb_version, expected $expected_version"
      [[ "$deb_arch" == "amd64" ]] \
        || die "$name control Architecture=$deb_arch, expected amd64"
      dpkg-deb -x "$artifact" "$root" >/dev/null \
        || die "could not extract $name"
      config="$root/usr/lib/bedrock-on-linux/bol/config.py"
      payload_bol="$root/usr/lib/bedrock-on-linux/bol"
      license="$root/usr/share/doc/bedrock-on-linux/copyright"
      desktop="$root/usr/share/applications/bedrock-on-linux.desktop"
      icon="$root/usr/share/icons/hicolor/256x256/apps/bedrock-on-linux.png"
      launcher="$root/usr/lib/bedrock-on-linux/bedrock-on-linux"
      ;;

    *.rpm)
      command -v rpm >/dev/null || die "rpm is required to inspect $name"
      command -v rpm2cpio >/dev/null \
        || die "rpm2cpio is required to extract $name"
      command -v cpio >/dev/null || die "cpio is required to extract $name"
      # A candidate is never signed, so digests/signatures are not consulted;
      # the payload itself is compared against this checkout below.
      rpm_version="$(rpm -qp --nosignature --qf '%{VERSION}' "$artifact" \
        2>/dev/null)" || die "could not read RPM metadata from $name"
      rpm_arch="$(rpm -qp --nosignature --qf '%{ARCH}' "$artifact" \
        2>/dev/null)" || die "could not read RPM architecture from $name"
      [[ "$rpm_version" == "$expected_version" ]] \
        || die "$name header Version=$rpm_version, expected $expected_version"
      [[ "$rpm_arch" == "x86_64" ]] \
        || die "$name header Arch=$rpm_arch, expected x86_64"
      # The bundled wheels' dist-info must not turn into distribution
      # dependencies nobody can satisfy; the spec declares them by hand.
      if rpm -qp --nosignature --requires "$artifact" 2>/dev/null \
          | grep -q "^python3dist("; then
        die "$name requires generated python3dist() dependencies"
      fi
      ( cd "$root" && rpm2cpio "$artifact" | cpio -idm --quiet ) \
        || die "could not extract $name"
      config="$root/usr/lib/bedrock-on-linux/bol/config.py"
      payload_bol="$root/usr/lib/bedrock-on-linux/bol"
      license="$root/usr/share/licenses/bedrock-on-linux/LICENSE"
      desktop="$root/usr/share/applications/bedrock-on-linux.desktop"
      icon="$root/usr/share/icons/hicolor/256x256/apps/bedrock-on-linux.png"
      launcher="$root/usr/lib/bedrock-on-linux/bedrock-on-linux"
      ;;

    *.AppImage)
      [[ -x "$artifact" ]] || die "$name is not executable"
      # Never execute a candidate AppImage merely to inspect it. Locate its
      # embedded SquashFS superblock and let unsquashfs validate candidate
      # offsets; this keeps verification read-only even for a tampered runtime.
      command -v unsquashfs >/dev/null \
        || die "unsquashfs is required to inspect $name safely"
      mapfile -t squashfs_offsets < <(python3 - "$artifact" <<'PY'
import pathlib
import sys

data = pathlib.Path(sys.argv[1]).read_bytes()
start = 0
while True:
    offset = data.find(b"hsqs", start)
    if offset < 0:
        break
    print(offset)
    start = offset + 1
PY
      )
      extracted=false
      for offset in "${squashfs_offsets[@]}"; do
        rm -rf "$root"
        if unsquashfs -no-progress -o "$offset" -d "$root" "$artifact" \
            >/dev/null 2>&1 \
            && [[ -f "$root/usr/bin/bol/config.py" ]]; then
          extracted=true
          break
        fi
      done
      [[ "$extracted" == true ]] \
        || die "could not safely extract a BedrockOnLinux payload from $name"
      config="$root/usr/bin/bol/config.py"
      payload_bol="$root/usr/bin/bol"
      license="$root/usr/share/licenses/bedrock-on-linux/LICENSE"
      desktop="$root/bedrock-on-linux.desktop"
      icon="$root/bedrock-on-linux.png"
      launcher="$root/AppRun"
      [[ -x "$root/AppRun" \
         && "$(head -n1 "$root/AppRun" 2>/dev/null)" == '#!/bin/sh' ]] \
        || die "$name AppRun is not portable /bin/sh"
      [[ -x "$root/usr/bin/bedrock-on-linux" ]] \
        || die "$name embedded launcher is not executable"
      cmp -s "$desktop" \
        "$root/usr/share/applications/bedrock-on-linux.desktop" \
        || die "$name embeds inconsistent desktop entries"
      if find "$root" \( -name '*.pyc' -o -name __pycache__ \) -print -quit \
          | grep -q .; then
        die "$name contains build-host Python bytecode"
      fi
      command -v readelf >/dev/null \
        || die "readelf is required to audit $name ELF paths"
      python3 - "$root" <<'PY' \
        || die "${name} contains a non-relocatable ELF path"
import os
import re
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
forbidden = {"libcrypt.so.1", "libXss.so.1"}
for path in root.rglob("*"):
    if not path.is_file():
        continue
    try:
        if path.open("rb").read(4) != b"\x7fELF":
            continue
    except OSError:
        continue
    result = subprocess.run(
        ["readelf", "--dynamic", "--wide", str(path)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=True)
    for value in re.findall(
            r"\((?:RPATH|RUNPATH)\).*?\[([^]]*)\]", result.stdout):
        if any(os.path.isabs(item) for item in value.split(":") if item):
            raise SystemExit(f"absolute RPATH/RUNPATH in {path}: {value}")
    for dependency in re.findall(r"\(NEEDED\).*?\[([^]]+)\]", result.stdout):
        if dependency in forbidden:
            raise SystemExit(f"avoidable host dependency in {path}: {dependency}")
PY
      ;;

    *)
      die "unsupported candidate artifact: $name"
      ;;
  esac

  [[ -f "$config" ]] || die "$name does not contain bol/config.py"
  [[ -f "$license" ]] || die "$name does not contain the project LICENSE"
  cmp -s "$SRC/LICENSE" "$license" \
    || die "$name embeds a project LICENSE different from this checkout"
  [[ -x "$launcher" ]] || die "$name launcher is not executable"
  [[ -f "$icon" ]] || die "$name does not contain its application icon"
  cmp -s "$SRC/data/icon.png" "$icon" \
    || die "$name embeds an application icon different from this checkout"
  if [[ -n "$desktop" ]]; then
    [[ -f "$desktop" ]] || die "$name does not contain a desktop entry"
    cmp -s "$SRC/data/bedrock-on-linux.desktop" "$desktop" \
      || die "$name embeds a desktop entry different from this checkout"
    grep -qx 'Type=Application' "$desktop" \
      || die "$name desktop entry has no application type"
    grep -qx 'Exec=bedrock-on-linux gui' "$desktop" \
      || die "$name desktop entry has an unexpected launch command"
    grep -qx 'Terminal=false' "$desktop" \
      || die "$name desktop entry would open an unexpected terminal"
  fi
  actual_metadata="$(read_config_metadata "$config")" \
    || die "could not read embedded metadata from $name"
  IFS=$'\t' read -r actual_version actual_rev actual_source_commit \
    actual_archive_sha actual_xcurl_rev actual_xcurl_sha \
    actual_umu_version actual_umu_archive_sha actual_umu_run_sha \
    <<< "$actual_metadata"
  [[ "$actual_version" == "$expected_version" ]] \
    || die "$name embeds VERSION=$actual_version, expected $expected_version"
  [[ "$actual_rev" == "$expected_rev" ]] \
    || die "$name embeds WINEGDK_BUILD_REV=$actual_rev, expected $expected_rev"
  [[ "$actual_source_commit" == "$expected_source_commit" ]] \
    || die "$name embeds a WineGDK source commit different from bol/config.py"
  [[ "$actual_archive_sha" == "$expected_archive_sha" ]] \
    || die "$name embeds an engine archive SHA-256 different from bol/config.py"
  [[ "$actual_xcurl_rev:$actual_xcurl_sha" == \
      "$expected_xcurl_rev:$expected_xcurl_sha" ]] \
    || die "$name embeds a different OpenSSL XCurl payload pin"
  [[ "$actual_umu_version:$actual_umu_archive_sha:$actual_umu_run_sha" == \
      "$expected_umu_version:$expected_umu_archive_sha:$expected_umu_run_sha" ]] \
    || die "$name embeds different umu-launcher pins"

  # VERSION/revision pins are necessary but not sufficient: an older build can
  # carry the same metadata while omitting a newly added safety module. Compare
  # every regular bol/ payload byte against this checkout and reject missing,
  # stale, or unexpected files. Runtime bytecode/cache files are deliberately
  # ignored because they are neither source inputs nor portable artifacts.
  [[ -d "$payload_bol" ]] || die "$name does not contain a bol/ package"
  if ! python3 - "$SRC/bol" "$payload_bol" "$name" <<'PY'
import stat
import sys
from pathlib import Path

expected_root = Path(sys.argv[1])
actual_root = Path(sys.argv[2])
artifact_name = sys.argv[3]


def regular_files(root):
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise SystemExit(f"cannot inspect {path}: {exc}") from exc
        if not stat.S_ISREG(mode):
            continue
        try:
            files[relative.as_posix()] = path.read_bytes()
        except OSError as exc:
            raise SystemExit(f"cannot read {path}: {exc}") from exc
    return files


expected = regular_files(expected_root)
actual = regular_files(actual_root)
missing = sorted(expected.keys() - actual.keys())
extra = sorted(actual.keys() - expected.keys())
stale = sorted(path for path in expected.keys() & actual.keys()
               if expected[path] != actual[path])
if missing or stale or extra:
    print(f"{artifact_name} bol/ payload differs from this checkout:",
          file=sys.stderr)
    for label, paths in (("missing", missing), ("stale", stale),
                         ("extra", extra)):
        if paths:
            print(f"  {label}: " + ", ".join(paths), file=sys.stderr)
    raise SystemExit(1)
PY
  then
    die "$name application payload is incomplete or stale"
  fi

  echo "  OK  $name  VERSION=$actual_version  WINEGDK_BUILD_REV=$actual_rev"
  index=$((index + 1))
done

echo "Candidate metadata verified against bol/config.py; application payload matches checkout."
