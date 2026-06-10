#!/usr/bin/env bash
# Package the OpenSSL XCurl set (the CA-injecting libcurl shim + its OpenSSL/curl
# DLL stack + the cryptbase RNG stub) into a reproducible tarball so end users
# get native PlayFab/online login WITHOUT the maintainer's local files.
#
# The launcher (ensure_openssl_xcurl_set) downloads the produced asset
#   openssl-xcurl-set-<rev>.tar.gz
# from the app's own GitHub releases and unpacks it into
#   ~/.local/share/bedrock-on-linux/xodus-xcurl/openssl-set/
# where _install_openssl_xcurl() / _install_cryptbase_in_prefix() consume it.
#
# Usage:
#   scripts/package-openssl-xcurl.sh [SET_DIR]
# SET_DIR defaults to the maintainer's installed set. Prints the content rev to
# paste into OPENSSL_XCURL_REV in bedrock-on-linux, and writes the tarball to
# dist/.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SET_DIR="${1:-$HOME/.local/share/bedrock-on-linux/xodus-xcurl/openssl-set}"
OUT="$SRC/dist"
mkdir -p "$OUT"

[[ -d "$SET_DIR" ]] || { echo "set dir not found: $SET_DIR" >&2; exit 1; }

# Runtime files only: every *.dll that is NOT a backup, plus the shim. Skip the
# *.bak / *.fwd-bak / *.1export-bak variants, the C source, and curl.exe (the
# launcher only ever loads DLLs by name).
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
n=0
while IFS= read -r -d '' f; do
  cp "$f" "$STAGE/"; n=$((n+1))
done < <(find "$SET_DIR" -maxdepth 1 -type f -name '*.dll' \
           ! -name '*.bak' ! -name '*.fwd-bak' ! -name '*.1export-bak' -print0)

# Sanity: the two files the shim path can't work without.
for must in libcurl-4.dll xcurl-cashim.dll cryptbase.dll; do
  [[ -f "$STAGE/$must" ]] || { echo "missing required file: $must" >&2; exit 1; }
done
echo "staged $n DLLs ($(du -sh "$STAGE" | cut -f1))"

# Deterministic tarball: sorted names, fixed owner/mtime, gzip -n (no timestamp)
# so the same contents always hash to the same rev.
TMP_TAR="$OUT/.openssl-xcurl-set.tmp.tar.gz"
tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime='2020-01-01 00:00:00' \
    --use-compress-program='gzip -n' \
    -cf "$TMP_TAR" -C "$STAGE" .

REV="$(sha256sum "$TMP_TAR" | cut -c1-12)"
ASSET="openssl-xcurl-set-$REV.tar.gz"
mv "$TMP_TAR" "$OUT/$ASSET"

echo
echo "  ✓ dist/$ASSET ($(du -sh "$OUT/$ASSET" | cut -f1))"
echo
echo "Set in bedrock-on-linux:"
echo "  OPENSSL_XCURL_REV = \"$REV\""
echo
echo "Publish on the app release:"
echo "  upload dist/$ASSET as a release asset on Wyze3306/BedrockOnLinux"
