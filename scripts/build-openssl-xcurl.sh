#!/usr/bin/env bash
# Build the OpenSSL-XCurl online-login set entirely from pinned public sources:
#   - the msys2 MinGW curl package + its runtime dependency closure, each pinned
#     by exact filename + SHA-256 in third_party/xcurl-msys2.lock,
#   - the CA-injecting shim compiled from src/xcurl-cashim.c,
#   - the cryptbase RNG stub compiled from src/cryptbase-stub.c,
# then package it with scripts/package-openssl-xcurl.sh. Prints the resulting
# OPENSSL_XCURL_REV + SHA-256 to pin into bol/config.py.
#
# With the lockfile present the build is byte-reproducible (fixed package set +
# deterministic, timestamp-free DLLs). Regenerate the lock by deleting it and
# re-running (resolves current msys2), e.g. when a pinned package rotates off
# the mirror; then re-pin config to the new rev/SHA.
#
# No opaque prebuilt binary is used: the runtime DLLs are stock msys2 packages
# (verified by SHA), and the two BedrockOnLinux DLLs are built from source.
set -Eeuo pipefail

WORK="${1:?usage: build-openssl-xcurl.sh WORKDIR}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CC="${CC:-x86_64-w64-mingw32-gcc}"
MSYS2_REPO="${MSYS2_REPO:-https://repo.msys2.org/mingw/mingw64}"
ROOT_PKG="mingw-w64-x86_64-curl"
LOCK="${XCURL_LOCK:-$SRC/third_party/xcurl-msys2.lock}"

for t in "$CC" curl tar zstd gzip sha256sum awk; do
  command -v "$t" >/dev/null || { echo "!! need $t" >&2; exit 1; }
done

CURL=(curl -fsSL --retry 5 --retry-connrefused --retry-delay 3 --connect-timeout 30)

mkdir -p "$WORK/pkgs" "$WORK/root" "$WORK/set"
SET="$WORK/set"

declare -A PKG_FILE PKG_SHA
order=()

if [ -f "$LOCK" ]; then
  echo "== Using pinned package lock: $LOCK"
  while read -r pkg file sha; do
    [ -n "$pkg" ] || continue
    order+=("$pkg"); PKG_FILE["$pkg"]="$file"; PKG_SHA["$pkg"]="$sha"
  done < <(grep -vE '^\s*(#|$)' "$LOCK")
  echo "   ${#order[@]} pinned packages"
else
  echo "== No lock; resolving the current msys2 closure of $ROOT_PKG"
  "${CURL[@]}" "$MSYS2_REPO/mingw64.db" -o "$WORK/mingw64.db"
  mkdir -p "$WORK/db"; tar -C "$WORK/db" -xf "$WORK/mingw64.db"

  # %DEPENDS%/%PROVIDES% may carry >=version constraints; strip them.
  parse_field() {
    awk -v f="%$1%" '$0==f{g=1;next} /^%/{g=0} g&&NF{sub(/[<>=].*/,"");print}' "$2"
  }
  declare -A PKG_DESC PROVIDES
  for d in "$WORK"/db/*/; do
    desc="$d/desc"; [ -f "$desc" ] || continue
    name="$(awk '/^%NAME%/{getline;print;exit}' "$desc")"
    file="$(awk '/^%FILENAME%/{getline;print;exit}' "$desc")"
    sha="$(awk '/^%SHA256SUM%/{getline;print;exit}' "$desc")"
    if [ -n "$name" ]; then
      PKG_FILE["$name"]="$file"; PKG_SHA["$name"]="$sha"; PKG_DESC["$name"]="$desc"
    fi
    while IFS= read -r p; do [ -n "$p" ] && PROVIDES["$p"]="$name"; done \
      < <(parse_field PROVIDES "$desc")
  done
  resolve() {  # map a dependency token to a concrete package name
    local tok="$1"
    [ -n "${PKG_FILE[$tok]:-}" ] && { echo "$tok"; return; }
    [ -n "${PROVIDES[$tok]:-}" ] && { echo "${PROVIDES[$tok]}"; return; }
    return 1
  }
  declare -A SEEN; queue=("$ROOT_PKG")
  while ((${#queue[@]})); do
    pkg="${queue[0]}"; queue=("${queue[@]:1}")
    [ -n "${SEEN[$pkg]:-}" ] && continue
    SEEN["$pkg"]=1; order+=("$pkg")
    desc="${PKG_DESC[$pkg]:-}"
    [ -n "$desc" ] && [ -f "$desc" ] || { echo "!! no desc for $pkg" >&2; exit 1; }
    while IFS= read -r dep; do
      [ -n "$dep" ] || continue
      if r="$(resolve "$dep")"; then
        queue+=("$r")
      else
        # Fail closed: a dropped dependency would silently produce an incomplete
        # closure. DEPENDS version constraints are already stripped by parse_field,
        # so an unresolved token means the msys2 set changed; review and re-pin.
        echo "!! unresolved dependency '$dep' of '$pkg'; closure would be incomplete" >&2
        exit 1
      fi
    done < <(parse_field DEPENDS "$desc")
  done
  echo "   resolved ${#order[@]} packages; writing $LOCK"
  {
    echo "# msys2 mingw64 runtime closure of $ROOT_PKG (name filename sha256)."
    echo "# Regenerate: delete this file and re-run scripts/build-openssl-xcurl.sh."
    for pkg in "${order[@]}"; do
      printf '%s %s %s\n' "$pkg" "${PKG_FILE[$pkg]}" "${PKG_SHA[$pkg]}"
    done
  } > "$LOCK"
fi

echo "== Downloading + verifying + extracting DLLs"
for pkg in "${order[@]}"; do
  file="${PKG_FILE[$pkg]}"; sha="${PKG_SHA[$pkg]}"
  [ -n "$file" ] && [ -n "$sha" ] || { echo "!! missing file/sha for $pkg" >&2; exit 1; }
  out="$WORK/pkgs/$file"
  [ -f "$out" ] || "${CURL[@]}" "$MSYS2_REPO/$file" -o "$out"
  got="$(sha256sum "$out" | cut -d' ' -f1)"
  [ "$got" = "$sha" ] || { echo "!! SHA mismatch for $file (want $sha, got $got)" >&2; exit 1; }
  tar -C "$WORK/root" -xf "$out" 2>/dev/null || \
    tar -C "$WORK/root" --use-compress-program=unzstd -xf "$out"
done
# The set is the runtime DLL closure that ships beside the game.
find "$WORK/root/mingw64/bin" -maxdepth 1 -name '*.dll' -exec cp -t "$SET" {} +

echo "== Building the source DLLs (shim + cryptbase)"
# For byte reproducibility: -s strips symbols; --image-base pins the base that
# MinGW otherwise randomizes per link (runtime ASLR is preserved via .reloc);
# --no-insert-timestamp + pe-zero-timestamps.py zero the PE timestamps.
# shellcheck disable=SC2054  # commas are intentional -Wl linker-arg separators
DET=(-s -Wl,--no-insert-timestamp -Wl,--image-base,0x180000000)
"$CC" -O2 -shared "${DET[@]}" -o "$SET/xcurl-cashim.dll" "$SRC/src/xcurl-cashim.c"
# -mrdrnd enables the RDRAND intrinsic; no -lbcrypt so cryptbase never imports
# bcrypt (whose RNG loops back to RtlGenRandom -> this stub -> stack overflow).
"$CC" -O2 -mrdrnd -shared "${DET[@]}" -o "$SET/cryptbase.dll" "$SRC/src/cryptbase-stub.c"

# Regression guard for that recursion: under Wine's advapi32->cryptbase forward,
# any RNG import (bcrypt) or self-forward (advapi32) makes cryptbase's
# RtlGenRandom re-enter itself and overflow the stack. Assert the built DLL
# imports neither -- a millisecond static check, no Wine or game run needed.
OBJDUMP="${CC%-gcc}-objdump"
command -v "$OBJDUMP" >/dev/null 2>&1 ||
  { echo "!! $OBJDUMP not found (install binutils-mingw-w64)" >&2; exit 1; }
cb_bad="$("$OBJDUMP" -p "$SET/cryptbase.dll" | awk '/DLL Name:/{print tolower($NF)}' \
  | grep -iE '^(bcrypt|advapi32)\.dll$' || true)"
if [ -n "$cb_bad" ]; then
  echo "!! cryptbase.dll imports '$cb_bad': its RtlGenRandom would recurse to a" \
       "stack overflow under Wine's advapi32->cryptbase forward. Keep the RNG" \
       "self-contained (source entropy without advapi32/bcrypt)." >&2
  exit 1
fi
echo "   cryptbase RNG self-contained: no bcrypt/advapi32 import (ok)"

python3 "$SRC/scripts/pe-zero-timestamps.py" \
  "$SET/xcurl-cashim.dll" "$SET/cryptbase.dll"
# Keep the shim source beside the set (matches the reviewed layout).
cp "$SRC/src/xcurl-cashim.c" "$SET/"

echo "== Packaging"
"$SRC/scripts/package-openssl-xcurl.sh" "$SET"
