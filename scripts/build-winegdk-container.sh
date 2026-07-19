#!/usr/bin/env bash
# Build WineGDK inside a Debian 11 (Bullseye) CONTAINER, as an alternative to
# build-winegdk-bullseye.sh's debootstrap + unprivileged user-namespace chroot,
# whose `unshare --setgid` path does not work on GitHub hosted runners.
#
# Run as root inside a debian:bullseye container (glibc 2.31). Produces an
# install prefix carrying the provenance files package-engine.sh requires, and
# enforces the same glibc-2.31 ABI ceiling. Deterministic paths + SOURCE_DATE_EPOCH
# keep the build reproducible across CI runs.
#
# Usage: build-winegdk-container.sh WINEGDK_SOURCE_REPO OUT_PREFIX
set -Eeuo pipefail
umask 022
export LC_ALL=C LANG=C TZ=UTC

SOURCE_REPO="${1:?usage: build-winegdk-container.sh WINEGDK_SOURCE_REPO OUT_PREFIX}"
PREFIX="${2:?usage: build-winegdk-container.sh WINEGDK_SOURCE_REPO OUT_PREFIX}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

EXPECTED_COMMIT="$(grep -m1 '^WINEGDK_SOURCE_COMMIT = ' \
  "$PROJECT_ROOT/bol/config.py" | cut -d'"' -f2)"
readonly EXPECTED_SOURCE_DATE_EPOCH="1784308597"
readonly GLIBC_CEILING="2.31"
readonly SOURCE_SHA256SUMS="$PROJECT_ROOT/third_party/winegdk-native5/SOURCE-SHA256SUMS"
# Fixed build paths so Wine's embedded __FILE__ strings are stable run to run.
readonly SRC=/winegdk/source
readonly BUILD=/winegdk/build

[ "$(id -u)" = 0 ] || { echo "!! must run as root in a bullseye container" >&2; exit 1; }

readonly -a BUILD_PACKAGES=(
  build-essential ca-certificates bison flex gettext pkg-config python3-minimal
  gcc-mingw-w64-i686 gcc-mingw-w64-x86-64 libasound2-dev libdbus-1-dev
  libegl1-mesa-dev libfontconfig1-dev libfreetype6-dev libgl1-mesa-dev
  libgnutls28-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev
  libkrb5-dev libpcap-dev libpulse-dev libsdl2-dev libudev-dev libunwind-dev
  libusb-1.0-0-dev libvulkan-dev libwayland-dev libx11-dev libxcomposite-dev
  libxcursor-dev libxext-dev libxfixes-dev libxi-dev libxinerama-dev
  libxkbcommon-dev libxrandr-dev libxrender-dev libxxf86vm-dev wayland-protocols
)

echo "== Installing build dependencies"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends git "${BUILD_PACKAGES[@]}" >/dev/null

echo "== Exporting pinned WineGDK source ($EXPECTED_COMMIT)"
git config --global --add safe.directory '*'
git -C "$SOURCE_REPO" cat-file -e "$EXPECTED_COMMIT^{commit}" \
  || { echo "!! source repo lacks $EXPECTED_COMMIT" >&2; exit 1; }
epoch="$(git -C "$SOURCE_REPO" show -s --format=%ct "$EXPECTED_COMMIT")"
[ "$epoch" = "$EXPECTED_SOURCE_DATE_EPOCH" ] \
  || { echo "!! source timestamp $epoch != $EXPECTED_SOURCE_DATE_EPOCH" >&2; exit 1; }
rm -rf "$SRC" "$BUILD"; mkdir -p "$SRC" "$BUILD" "$PREFIX"
git -C "$SOURCE_REPO" archive --format=tar "$EXPECTED_COMMIT" | tar -x -C "$SRC"

echo "== Verifying reviewed source hashes"
[ -f "$SOURCE_SHA256SUMS" ] || { echo "!! missing SOURCE-SHA256SUMS" >&2; exit 1; }
( cd "$SRC" && sha256sum --strict -c "$SOURCE_SHA256SUMS" >/dev/null ) \
  || { echo "!! exported source does not match the reviewed native delta" >&2; exit 1; }

echo "== Configuring + building WineGDK (i386 + x86_64)"
( cd "$BUILD"
  SOURCE_DATE_EPOCH="$EXPECTED_SOURCE_DATE_EPOCH" \
    "$SRC/configure" --enable-archs=i386,x86_64 --disable-tests --prefix="$PREFIX"
  SOURCE_DATE_EPOCH="$EXPECTED_SOURCE_DATE_EPOCH" make -j"$(nproc)"
  make install )
# Wine's installed headers embed the builder's absolute source path and are not
# runtime material; drop them so the prefix is relocatable + reproducible.
rm -rf "$PREFIX/include"

echo "== Enforcing GLIBC_$GLIBC_CEILING ABI ceiling"
version_is_greater() {  # $1 > $2 ?
  local greatest
  greatest="$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n1)"
  [ "$1" != "$2" ] && [ "$greatest" = "$1" ]
}
elf=0 failures=0
while IFS= read -r -d '' f; do
  readelf -h -- "$f" >/dev/null 2>&1 || continue
  elf=$((elf + 1))
  max="$(readelf --version-info -- "$f" 2>/dev/null \
    | grep -oE 'GLIBC_[0-9]+([.][0-9]+)*' | sed 's/GLIBC_//' | sort -Vu | tail -n1)"
  if [ -n "$max" ] && version_is_greater "$max" "$GLIBC_CEILING"; then
    echo "ABI violation: ${f#"$PREFIX"/} needs GLIBC_$max" >&2
    failures=$((failures + 1))
  fi
done < <(find "$PREFIX" -type f -print0)
[ "$elf" -gt 0 ] || { echo "!! no ELF file in prefix" >&2; exit 1; }
find "$PREFIX" -type f -name wineserver | grep -q . \
  || { echo "!! prefix has no wineserver" >&2; exit 1; }
find "$PREFIX" -type f -path '*/x86_64-unix/ntdll.so' | grep -q . \
  || { echo "!! prefix has no x86_64-unix/ntdll.so" >&2; exit 1; }
[ "$failures" = 0 ] || { echo "!! $failures ELF file(s) exceed GLIBC_$GLIBC_CEILING" >&2; exit 1; }

echo "== Writing provenance"
dpkg-query -W -f='${binary:Package}\t${Version}\n' > "$PREFIX/.bol-winegdk-package-versions.tsv"
pv_sha="$(sha256sum "$PREFIX/.bol-winegdk-package-versions.tsv" | cut -d' ' -f1)"
cat > "$PREFIX/.bol-winegdk-build.env" <<EOF
schema=1
winegdk_commit=$EXPECTED_COMMIT
source_date_epoch=$EXPECTED_SOURCE_DATE_EPOCH
debian_suite=bullseye
glibc_ceiling=$GLIBC_CEILING
package_versions_sha256=$pv_sha
EOF

echo "== WineGDK prefix ready: $PREFIX"
