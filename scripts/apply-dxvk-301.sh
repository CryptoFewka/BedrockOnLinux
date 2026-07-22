#!/usr/bin/env bash
# Swap the public GDK-Proton base's bundled DXVK (2.7.1) for the stock DXVK
# v3.0.1 release, preserving the base DLLs as *.bol-pre301 and writing the
# matching version file. This reproduces upstream's engine exactly: the public
# base ships DXVK 2.7.1, but upstream's engine used stock DXVK v3.0.1. Run on
# the extracted GDK-Proton base BEFORE scripts/package-engine.sh.
#
# The DXVK v3.0.1 release DLLs are byte-identical to upstream's, so the resulting
# files/lib/wine/dxvk tree matches the upstream engine byte-for-byte.
#
# Usage: apply-dxvk-301.sh ENGINE_BASE_DIR
set -Eeuo pipefail

BASE="${1:?usage: apply-dxvk-301.sh ENGINE_BASE_DIR}"
readonly DXVK_URL="https://github.com/doitsujin/dxvk/releases/download/v3.0.1/dxvk-3.0.1.tar.gz"
readonly DXVK_SHA256="bebb6284db590535b7b005c102ebf6850c98842cff0fded9aacb74babae14c49"
DXVK_DIR="$BASE/files/lib/wine/dxvk"
[ -d "$DXVK_DIR/x86_64-windows" ] && [ -d "$DXVK_DIR/i386-windows" ] \
  || { echo "!! base has no DXVK dir: $DXVK_DIR" >&2; exit 1; }

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
# Reuse a pre-downloaded tarball when provided (CI/offline); else fetch it.
tarball="${BOL_DXVK_301_ARCHIVE:-$work/dxvk-3.0.1.tar.gz}"
[ -f "$tarball" ] || curl -fsSL --retry 3 -o "$tarball" "$DXVK_URL"
echo "$DXVK_SHA256  $tarball" | sha256sum -c -
tar -xzf "$tarball" -C "$work"
dxvk="$work/dxvk-3.0.1"

# GDK-Proton uses x86_64-windows/i386-windows; DXVK's release ships x64/x32.
for pair in "x86_64-windows:x64" "i386-windows:x32"; do
  arch="${pair%%:*}"; src="${pair##*:}"
  for dll in d3d8 d3d9 d3d10core d3d11 dxgi; do
    dst="$DXVK_DIR/$arch/$dll.dll"
    [ -f "$dst" ] || { echo "!! base missing $dst" >&2; exit 1; }
    [ -f "$dxvk/$src/$dll.dll" ] || { echo "!! DXVK 3.0.1 lacks $src/$dll.dll" >&2; exit 1; }
    cp -a "$dst" "$dst.bol-pre301"        # preserve the base's 2.7.1 DLL
    cp -a "$dxvk/$src/$dll.dll" "$dst"    # install stock DXVK v3.0.1
  done
done
# openvr_api_dxvk.dll is not in the DXVK release; the base's copy stays as-is.
printf ' v3.0.1 dxvk (v3.0.1)\n' > "$DXVK_DIR/version"
echo "== DXVK swapped to v3.0.1 (base 2.7.1 preserved as *.bol-pre301)"
