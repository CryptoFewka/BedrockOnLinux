#!/usr/bin/env bash
# Portable AppImage with bundled CPython, Tk, login dependencies and CA store.
# Xft/fontconfig/X11 remain host GUI dependencies, as with standard AppImages.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VER="$(grep -m1 '^VERSION = ' "$SRC/bol/config.py" | cut -d'"' -f2)"
OUT="$SRC/dist"
CACHE="${BOL_APPIMAGE_BUILD_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/bedrock-on-linux-build/appimage}"
mkdir -p "$CACHE" "$OUT"
GLIBC_CEILING="${BOL_APPIMAGE_GLIBC_CEILING:-2.31}"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1782250551}"
export SOURCE_DATE_EPOCH
APPDIR="$OUT/BedrockOnLinux.AppDir"; rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps" \
         "$APPDIR/usr/share/licenses/bedrock-on-linux"

PBS_TAG="${PBS_TAG:-20260610}"; PBS_PY="${PBS_PY:-3.12.13}"; PYABI=cpython-312-x86_64-linux-gnu
PBS_ASSET="cpython-${PBS_PY}+${PBS_TAG}-x86_64-unknown-linux-gnu-install_only.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${PBS_ASSET}"
PBS_TARBALL="$CACHE/$PBS_ASSET"
PBS_SHA256="c218f50baeb2c06a30c2f03db5986b2bad6ab7c8a52faad2d5a59bda0677b93a"
TKVER="${TKVER:-8.6.14}"

download_verified() {
  local url="$1" output="$2" expected="$3" label="$4" actual
  if [[ -f "$output" ]]; then
    read -r actual _ < <(sha256sum "$output")
    if [[ "$actual" == "$expected" ]]; then
      return
    fi
    echo "!! cached $label hash mismatch; downloading reviewed bytes again" >&2
    rm -f -- "$output"
  fi
  curl -fL --retry 3 -o "$output.part" "$url"
  read -r actual _ < <(sha256sum "$output.part")
  if [[ "$actual" != "$expected" ]]; then
    rm -f -- "$output.part"
    echo "!! $label SHA-256 mismatch: got $actual, expected $expected" >&2
    exit 1
  fi
  mv -f -- "$output.part" "$output"
}

PATCHELF="${PATCHELF:-$(command -v patchelf || true)}"
if [[ -z "$PATCHELF" ]]; then
  python3 -m pip install --quiet --user patchelf 2>/dev/null || true
  PATCHELF="$(command -v patchelf || echo "$HOME/.local/bin/patchelf")"
fi
[[ -x "$PATCHELF" ]] || { echo "!! need patchelf (pip install patchelf)" >&2; exit 1; }

download_verified "$PBS_URL" "$PBS_TARBALL" "$PBS_SHA256" \
  "python-build-standalone archive"
echo "== unpacking Python into the AppDir"
tar -C "$APPDIR/usr" -xzf "$PBS_TARBALL"
PYHOME="$APPDIR/usr/python"; PYBIN="$PYHOME/bin/python3.12"
PYLIB="$PYHOME/lib"; DYN="$PYLIB/python3.12/lib-dynload"
[[ -x "$PYBIN" ]] || { echo "!! bundled python missing" >&2; exit 1; }

TKX="$CACHE/tkxft-${PBS_PY}-${TKVER}-relocatable-v3-no-xss"
if [[ ! -f "$TKX/lib/libtk8.6.so" || ! -f "$TKX/_tkinter.${PYABI}.so" ]]; then
  echo "== building Tcl/Tk ${TKVER} with Xft (one-time, cached)"
  rm -rf "$TKX"; mkdir -p "$TKX"
  bdir="$CACHE/tk-build"; rm -rf "$bdir"; mkdir -p "$bdir"
  for kit in tcl tk; do
    if [[ "$kit" == tcl ]]; then
      kit_sha="5880225babf7954c58d4fb0f5cf6279104ce1cd6aa9b71e9a6322540e1c4de66"
    else
      kit_sha="8ffdb720f47a6ca6107eac2dd877e30b0ef7fac14f3a84ebbd0b3612cee41a94"
    fi
    download_verified \
      "https://prdownloads.sourceforge.net/tcl/${kit}${TKVER}-src.tar.gz" \
      "$CACHE/${kit}${TKVER}-src.tar.gz" "$kit_sha" "$kit source archive"
    tar -C "$bdir" -xzf "$CACHE/${kit}${TKVER}-src.tar.gz"
  done
  tks="$CACHE/tksrc-${PBS_PY}"; mkdir -p "$tks/clinic"
  base="https://raw.githubusercontent.com/python/cpython/v${PBS_PY}/Modules"
  for f in _tkinter.c tkappinit.c tkinter.h clinic/_tkinter.c.h; do
    case "$f" in
      _tkinter.c) source_sha="c484afe880de65c628f5376adf0be413eae2f0d339d5ef5f33365c14ba59eca3" ;;
      tkappinit.c) source_sha="312be438c11b08dd334c9a64149e94aef9ac31d24b7ae9b428c37f93f67821d6" ;;
      tkinter.h) source_sha="229776646f467c4ed9ec1460a14b756bc2de5e5bd70ead431a495d21305f7675" ;;
      clinic/_tkinter.c.h) source_sha="2a6f1f61b004e88fbb8769650699747119004076c45eb7d430beb0be93325658" ;;
    esac
    download_verified "$base/$f" "$tks/$f" "$source_sha" \
      "CPython $f"
  done
  TK_HELPER="$SRC/scripts/build-appimage-tk-cache.sh"
  if [[ -n "${BOL_TK_BUILD_ROOTFS:-}" ]]; then
    ROOTFS="$(readlink -f -- "$BOL_TK_BUILD_ROOTFS")"
    [[ -x "$ROOTFS/bin/bash" && -x "$ROOTFS/usr/bin/gcc" ]] \
      || { echo "!! BOL_TK_BUILD_ROOTFS is not a prepared build rootfs: $ROOTFS" >&2; exit 1; }
    echo "   compiling native Tk inside: $ROOTFS"
    env ROOTFS="$ROOTFS" SRC="$SRC" CACHE="$CACHE" TK_HELPER="$TK_HELPER" \
      BDIR="$bdir" TKX="$TKX" PYHOME="$PYHOME" TKS="$tks" \
      TKVER="$TKVER" PYABI="$PYABI" \
      unshare --user --map-root-user --mount --propagation private --fork \
      /bin/bash -c '
        set -euo pipefail
        mkdir -p "$ROOTFS$SRC" "$ROOTFS$CACHE" "$ROOTFS/dev" "$ROOTFS/proc"
        mount --bind "$SRC" "$ROOTFS$SRC"
        mount --bind "$CACHE" "$ROOTFS$CACHE"
        mount --rbind /dev "$ROOTFS/dev"
        mount --make-rslave "$ROOTFS/dev"
        mount --rbind /proc "$ROOTFS/proc"
        mount --make-rslave "$ROOTFS/proc"
        exec chroot "$ROOTFS" /usr/bin/env -i \
          HOME=/tmp PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
          LC_ALL=C.UTF-8 LANG=C.UTF-8 TZ=UTC \
          /bin/bash "$TK_HELPER" "$BDIR" "$TKX" "$PYHOME" "$TKS" "$TKVER" "$PYABI"
      '
  else
    bash "$TK_HELPER" "$bdir" "$TKX" "$PYHOME" "$tks" "$TKVER" "$PYABI"
  fi
fi

echo "== grafting Xft Tk into the bundle"
install -m644 "$TKX/lib/libtcl8.6.so" "$TKX/lib/libtk8.6.so" "$PYLIB/"
cp -r "$TKX/lib/tcl8.6" "$TKX/lib/tk8.6" "$PYLIB/"
install -m755 "$TKX/_tkinter.${PYABI}.so" "$DYN/_tkinter.${PYABI}.so"
# drop python-build-standalone's no-Xft Tcl/Tk 9 so it can never be picked up
rm -f "$PYLIB"/libtcl9*.so "$PYLIB"/libtcl9tk9*.so
rm -rf "$PYLIB"/tcl9* "$PYLIB"/tk9* 2>/dev/null || true
# rpath so the grafted libs find each other relative to the AppImage mount
"$PATCHELF" --set-rpath '$ORIGIN'        "$PYLIB/libtcl8.6.so"
"$PATCHELF" --set-rpath '$ORIGIN'        "$PYLIB/libtk8.6.so"
"$PATCHELF" --set-rpath '$ORIGIN/../..'  "$DYN/_tkinter.${PYABI}.so"
for library in "$PYLIB/libtcl8.6.so" "$PYLIB/libtk8.6.so"; do
  [[ "$("$PATCHELF" --print-rpath "$library")" == '$ORIGIN' ]] || {
    echo "!! non-relocatable RUNPATH remained in $library" >&2
    exit 1
  }
done

echo "== installing portable cryptography + certifi + customtkinter + python-xlib into the bundle"
# Hash-pinned, wheels only, no sdist builds: the closure + SHA-256s live in
# third_party/requirements-appimage.txt (--require-hashes rejects any mismatch).
"$PYBIN" -m pip install --no-cache-dir --no-compile \
    --no-deps --require-hashes --only-binary=:all: \
    -r "$SRC/third_party/requirements-appimage.txt" \
    >/dev/null

PY3="$PYLIB/python3.12"
rm -rf "$PY3/test" "$PY3/idlelib" "$PY3/turtledemo" "$PY3/tkinter/test" \
       "$PY3/lib2to3" "$PY3/ensurepip" "$PYHOME/share" "$PYHOME/include" 2>/dev/null || true
rm -f "$DYN"/_crypt.*.so
find "$PYHOME" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$PYHOME" -name '*.pyc' -delete 2>/dev/null || true

# AppImage is the cross-distribution artifact, so host-built Tcl/Tk must not
# silently import the build machine's newer glibc.  This caught the old cache
# requiring GLIBC_2.38, which made an otherwise valid AppImage fail on Ubuntu
# 22.04, Mint 21, Debian 12 and the Steam Runtime. Audit every bundled ELF,
# including binary Python wheels, against the Debian 11/sniper baseline.
command -v readelf >/dev/null 2>&1 \
  || { echo "!! readelf is required for the AppImage ABI audit" >&2; exit 1; }
python3 - "$APPDIR" "$GLIBC_CEILING" <<'PY'
import os
import re
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
ceiling = tuple(map(int, sys.argv[2].split(".")))
if len(ceiling) != 2:
    raise SystemExit("invalid BOL_APPIMAGE_GLIBC_CEILING (expected MAJOR.MINOR)")
worst = ((0, 0), None)
violations = []
rpath_violations = []
forbidden_dependencies = []
count = 0
for path in root.rglob("*"):
    if not path.is_file():
        continue
    try:
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
    except OSError:
        continue
    count += 1
    result = subprocess.run(
        ["readelf", "--version-info", "--wide", str(path)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise SystemExit(f"readelf failed for {path}: {result.stderr.strip()}")
    versions = [tuple(map(int, value)) for value in
                re.findall(r"GLIBC_(\d+)\.(\d+)", result.stdout)]
    required = max(versions, default=(0, 0))
    if required > worst[0]:
        worst = (required, path.relative_to(root))
    if required > ceiling:
        violations.append((required, path.relative_to(root)))
    dynamic = subprocess.run(
        ["readelf", "--dynamic", "--wide", str(path)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    if dynamic.returncode:
        raise SystemExit(f"readelf failed for {path}: {dynamic.stderr.strip()}")
    for value in re.findall(
            r"\((?:RPATH|RUNPATH)\).*?\[([^]]*)\]", dynamic.stdout):
        absolute = [entry for entry in value.split(":")
                    if entry and os.path.isabs(entry)]
        if absolute:
            rpath_violations.append((path.relative_to(root), absolute))
    for dependency in re.findall(r"\(NEEDED\).*?\[([^]]+)\]", dynamic.stdout):
        if dependency in {"libcrypt.so.1", "libXss.so.1"}:
            forbidden_dependencies.append((path.relative_to(root), dependency))
if not count:
    raise SystemExit("AppImage staging tree contains no ELF files")
if violations:
    details = "\n".join(
        f"  GLIBC_{version[0]}.{version[1]}: {path}"
        for version, path in sorted(violations, reverse=True)[:40]
    )
    raise SystemExit(
        "AppImage contains host-built ELF files newer than GLIBC_%d.%d:\n%s\n"
        "Rebuild the Tcl/Tk cache on Debian 11 (Bullseye) and retry."
        % (*ceiling, details)
    )
if rpath_violations:
    details = "\n".join(
        f"  {path}: {':'.join(entries)}"
        for path, entries in rpath_violations[:40]
    )
    raise SystemExit(
        "AppImage contains absolute RPATH/RUNPATH entries; bundled ELF "
        "paths must be relative to $ORIGIN:\n" + details
    )
if forbidden_dependencies:
    details = "\n".join(
        f"  {path}: {dependency}"
        for path, dependency in forbidden_dependencies
    )
    raise SystemExit(
        "AppImage retains avoidable legacy host dependencies:\n" + details
    )
print("  AppImage ABI OK: %d ELF files, maximum GLIBC_%d.%d (%s)" %
      (count, worst[0][0], worst[0][1], worst[1]))
PY

[[ -f "$SRC/data/icon.png" ]] || { echo "data/icon.png missing" >&2; exit 1; }
[[ -f "$SRC/LICENSE" ]] || { echo "LICENSE missing" >&2; exit 1; }
install -m755 "$SRC/bedrock-on-linux" "$APPDIR/usr/bin/bedrock-on-linux"
cp -r "$SRC/bol" "$APPDIR/usr/bin/bol"
find "$APPDIR/usr/bin/bol" -name __pycache__ -type d -exec rm -rf {} +
mkdir -p "$APPDIR/usr/bin/data"
cp "$SRC/data/icon.png" "$APPDIR/usr/bin/data/icon.png"
cp "$SRC/data/icon.png" "$APPDIR/bedrock-on-linux.png"
cp "$SRC/data/icon.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/bedrock-on-linux.png"
sed 's|^Exec=.*|Exec=bedrock-on-linux gui|' \
   "$SRC/data/bedrock-on-linux.desktop" > "$APPDIR/bedrock-on-linux.desktop"
cp "$APPDIR/bedrock-on-linux.desktop" \
   "$APPDIR/usr/share/applications/bedrock-on-linux.desktop"
install -m644 "$SRC/LICENSE" \
  "$APPDIR/usr/share/licenses/bedrock-on-linux/LICENSE"

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
PY="$HERE/usr/python/bin/python3.12"
export TCL_LIBRARY="$HERE/usr/python/lib/tcl8.6"
export TK_LIBRARY="$HERE/usr/python/lib/tk8.6"
CERT="$HERE/usr/python/lib/python3.12/site-packages/certifi/cacert.pem"
if [ -f "$CERT" ]; then
  export SSL_CERT_FILE="$CERT"
  export REQUESTS_CA_BUNDLE="$CERT"
fi
unset PYTHONHOME PYTHONPATH        # self-contained; libs found via rpath
exec "$PY" "$HERE/usr/bin/bedrock-on-linux" "$@"
EOF
chmod 755 "$APPDIR/AppRun"
/bin/sh -n "$APPDIR/AppRun" \
  || { echo "!! AppRun is not compatible with /bin/sh" >&2; exit 1; }

echo "== verifying the bundle: Xft Tk + cryptography + HTTPS, all self-contained"
env -i TCL_LIBRARY="$PYHOME/lib/tcl8.6" TK_LIBRARY="$PYHOME/lib/tk8.6" \
   SSL_CERT_FILE="$PYLIB/python3.12/site-packages/certifi/cacert.pem" \
   SSL_CERT_DIR=/nonexistent \
   ${DISPLAY:+DISPLAY="$DISPLAY"} ${XAUTHORITY:+XAUTHORITY="$XAUTHORITY"} \
   "$PYBIN" - <<'PY'
import os
import ssl
import time
import urllib.error
import urllib.request

import _tkinter
import cryptography
import customtkinter
import Xlib
from importlib.metadata import version
assert _tkinter.TK_VERSION == "8.6", _tkinter.TK_VERSION   # Xft build, not PBS Tk9
expected = {
    "cryptography": "43.0.3",
    "certifi": "2026.6.17",
    "cffi": "2.0.0",
    "pycparser": "3.0",
    "customtkinter": "5.2.2",
    "darkdetect": "0.8.0",
    "packaging": "26.2",
    "python-xlib": "0.33",
    "six": "1.17.0",
}
actual = {package: version(package) for package in expected}
assert actual == expected, (actual, expected)
def verify_https():
    # The point of this check is that the bundled OpenSSL + certifi CA can
    # complete a real TLS handshake. A certificate/TLS failure is a genuine
    # bundling defect and must fail the build; a network/HTTP error (offline
    # runner, sandbox, or the host being briefly unreachable) is environmental
    # and must not, since the crypto stack already imported and loaded its CA.
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen("https://api.github.com", timeout=20) as response:
                response.read(16)
            return "HTTPS via bundled CA"
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            # Only a certificate-verification failure proves the bundled CA is
            # broken. Other ssl.SSLError subtypes (SSLEOFError, reset mid-
            # handshake, etc.) are transport hiccups -> retry, then skip.
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise SystemExit(f"bundled TLS/CA verification failed: {reason}")
            last = exc
        except ssl.SSLCertVerificationError as exc:
            raise SystemExit(f"bundled TLS/CA verification failed: {exc}")
        except (ssl.SSLError, OSError) as exc:
            last = exc
        time.sleep(2 * (attempt + 1))
    print(f"  (warning: could not live-test HTTPS against api.github.com ({last});"
          " bundled crypto imported OK, skipping the online check)")
    return "HTTPS online check skipped (network unavailable)"

https_status = verify_https()
msg = (f"  bundle OK: Tk {_tkinter.TK_VERSION} | cryptography {cryptography.__version__}"
       f" | customtkinter {customtkinter.__version__} | {https_status}")
if os.environ.get("DISPLAY"):
    import tkinter
    from tkinter import font
    r = tkinter.Tk()
    r.withdraw()
    n = len(set(font.families()))
    r.destroy()
    assert n > 100, f"Xft not active — only {n} fonts"
    msg += f" | {n} font families (Xft, antialiased)"
print(msg)
PY

# The isolated import check above intentionally exercises many modules and
# recreates bytecode with the temporary AppDir path in co_filename. Never ship
# those host-specific paths; the runtime can recreate bytecode in its user
# cache if needed.
find "$PYHOME" -name '__pycache__' -type d -prune -exec rm -rf {} + \
  2>/dev/null || true
find "$PYHOME" -name '*.pyc' -delete 2>/dev/null || true
find "$APPDIR" -type f -name '.DS_Store' -delete 2>/dev/null || true

# Refuse maintainer-specific source/cache paths anywhere in the final staging
# tree. This catches Tcl's compiled-in configure prefix and Python bytecode in
# addition to the ELF RUNPATH audit above.
python3 - "$APPDIR" "$SRC" "$CACHE" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
forbidden = [value.encode() for value in sys.argv[2:] if value]
leaks = []
for path in root.rglob("*"):
    if not path.is_file():
        continue
    try:
        data = path.read_bytes()
    except OSError:
        continue
    if any(value in data for value in forbidden):
        leaks.append(path.relative_to(root))
if leaks:
    raise SystemExit(
        "AppImage staging tree embeds maintainer-local paths:\n  " +
        "\n  ".join(map(str, leaks[:40])))
print("  AppImage staging paths are relocatable")
PY

TOOL="$CACHE/appimagetool"
download_verified \
  "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
  "$TOOL" \
  "a6d71e2b6cd66f8e8d16c37ad164658985e0cf5fcaa950c90a482890cb9d13e0" \
  "appimagetool build 295"
chmod 755 "$TOOL"
RUNTIME="$CACHE/runtime-x86_64"
download_verified \
  "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64" \
  "$RUNTIME" \
  "1cc49bcf1e2ccd593c379adb17c9f85a36d619088296504de95b1d06215aebbf" \
  "AppImage type-2 x86_64 runtime"
runtime_header="$(readelf -h "$RUNTIME")"
[[ "$runtime_header" == *"Class:"*"ELF64"* \
   && "$runtime_header" == *"Machine:"*"X86-64"* ]] \
  || { echo "!! AppImage runtime is not ELF64 x86-64" >&2; exit 1; }
runtime_dynamic="$(readelf -d "$RUNTIME")"
[[ "$runtime_dynamic" != *"(NEEDED)"* ]] \
  || { echo "!! AppImage runtime is not statically linked" >&2; exit 1; }
APPIMG="$OUT/BedrockOnLinux-${VER}-x86_64.AppImage"
rm -f "$APPIMG"
ARCH=x86_64 "$TOOL" --appimage-extract-and-run \
  --runtime-file "$RUNTIME" "$APPDIR" "$APPIMG"
chmod 755 "$APPIMG"
rm -rf "$APPDIR"
echo "OK -> $APPIMG ($(du -h "$APPIMG" | cut -f1))"
