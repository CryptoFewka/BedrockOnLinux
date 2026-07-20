#!/usr/bin/env bash
# Build the pinned WineGDK source in an unprivileged Debian 11 (Bullseye)
# chroot.  The resulting Unix ELF files must not require glibc newer than 2.31.
#
# Usage:
#   scripts/build-winegdk-bullseye.sh WORK_ROOT WINEGDK_SOURCE_REPO
#
# WORK_ROOT must be an empty directory outside both repositories.  Every file
# created by this script (rootfs, source snapshot, build tree, install prefix,
# logs and reports) stays below WORK_ROOT.  The source repository is read only.
# This script never packages, publishes, pushes or installs an engine.
set -Eeuo pipefail

umask 022
export LC_ALL=C
export LANG=C
export TZ=UTC

SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"; readonly SCRIPT_PATH
PROJECT_ROOT="$(cd -- "$(dirname -- "$SCRIPT_PATH")/.." && pwd -P)"; readonly PROJECT_ROOT
readonly EXPECTED_COMMIT="75637b674e1f191e65753663c4c0c32bea05ba6e"
readonly PUBLIC_BASE_COMMIT="e75ddb5f5d8874eecf8e8c1742e6aaa4db9cd4a3"
readonly EXPECTED_SOURCE_DATE_EPOCH="1784308597"
readonly VENDORED_BASE_PATCH="$PROJECT_ROOT/third_party/winegdk-r12/online-patches-after-user-ready.patch"
readonly VENDORED_BASE_PATCH_SHA256="ee0543f11737a11f5edec389967bb41482c7f5eda3807c24d171dd6bf6301274"
readonly VENDORED_PATCH="$PROJECT_ROOT/third_party/winegdk-native5/0001-winegdk-native5-Xbox-and-file-picker-runtime.patch"
readonly VENDORED_PATCH_SHA256="d5630af845064b50780665e8ba335d4484a4c0b68eb396f195f840f0d2689a8b"
readonly SOURCE_SHA256SUMS="$PROJECT_ROOT/third_party/winegdk-native5/SOURCE-SHA256SUMS"
readonly SOURCE_SHA256SUMS_SHA256="80a3d59d35ab642ec1f2546aa6ad2c180785f0e577f30d8eccdda2066d80c72e"
readonly GLIBC_CEILING="2.31"
readonly DEBIAN_SUITE="bullseye"
readonly DEBIAN_MIRROR="https://deb.debian.org/debian"
readonly DEBIAN_SECURITY_MIRROR="https://security.debian.org/debian-security"

readonly -a BUILD_PACKAGES=(
  build-essential
  ca-certificates
  bison
  flex
  gettext
  pkg-config
  python3-minimal
  gcc-mingw-w64-i686
  gcc-mingw-w64-x86-64
  libasound2-dev
  libdbus-1-dev
  libegl1-mesa-dev
  libfontconfig1-dev
  libfreetype6-dev
  libgl1-mesa-dev
  libgnutls28-dev
  libgstreamer1.0-dev
  libgstreamer-plugins-base1.0-dev
  libkrb5-dev
  libpcap-dev
  libpulse-dev
  libsdl2-dev
  libudev-dev
  libunwind-dev
  libusb-1.0-0-dev
  libvulkan-dev
  libwayland-dev
  libx11-dev
  libxcomposite-dev
  libxcursor-dev
  libxext-dev
  libxfixes-dev
  libxft-dev
  libxi-dev
  libxinerama-dev
  libxkbcommon-dev
  libxrandr-dev
  libxrender-dev
  libxxf86vm-dev
  wayland-protocols
)

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat >&2 <<EOF
Usage: $(basename -- "$0") WORK_ROOT WINEGDK_SOURCE_REPO

Builds commit $EXPECTED_COMMIT in a Bullseye user-namespace chroot and writes
only below WORK_ROOT. WORK_ROOT must be empty and must not be inside either
the BedrockOnLinux or WineGDK repository.
EOF
  exit "${1:-2}"
}

require_command() {
  command -v -- "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

path_is_within() {
  local child="$1" parent="$2"
  [[ "$child" == "$parent" || "$child" == "$parent/"* ]]
}

find_subid_start() {
  local database="$1" name="$2" numeric_id="$3"
  awk -F: -v name="$name" -v numeric_id="$numeric_id" '
    ($1 == name || $1 == numeric_id) && $3 >= 65535 { print $2; exit }
  ' "$database"
}

assert_internal_namespace() {
  [[ "${BOL_WINEGDK_INTERNAL:-}" == 1 ]] || die "internal mode is private"
  [[ "$(id -u)" == 0 && "$(id -g)" == 0 ]] ||
    die "internal stage is not root inside its user namespace"
}

declare -a ACTIVE_MOUNTS=()

cleanup_mounts() {
  local index
  set +e
  for ((index=${#ACTIVE_MOUNTS[@]} - 1; index >= 0; index--)); do
    umount -R -l -- "${ACTIVE_MOUNTS[$index]}" 2>/dev/null || true
  done
}

bind_tree() {
  local source="$1" target="$2"
  mount --rbind -- "$source" "$target"
  mount --make-rslave -- "$target"
  ACTIVE_MOUNTS+=("$target")
}

bind_directory() {
  local source="$1" target="$2" mode="${3:-rw}"
  mount --bind -- "$source" "$target"
  ACTIVE_MOUNTS+=("$target")
  if [[ "$mode" == ro ]]; then
    mount -o remount,bind,ro -- "$target"
  fi
}

mount_chroot_runtime() {
  local rootfs="$1"
  mkdir -p -- "$rootfs/dev" "$rootfs/proc"
  bind_tree /dev "$rootfs/dev"
  bind_tree /proc "$rootfs/proc"
}

internal_foreign_stage() {
  local work_root="$1"
  local rootfs="$work_root/rootfs"

  assert_internal_namespace
  mkdir -p -- "$rootfs" "$work_root/cache/debootstrap" \
    "$work_root/tmp" "$work_root/home"
  export HOME="$work_root/home"
  export TMPDIR="$work_root/tmp"
  export XDG_CACHE_HOME="$work_root/cache"

  debootstrap \
    --arch=amd64 \
    --variant=minbase \
    --foreign \
    --components=main,contrib,non-free \
    --include=ca-certificates \
    --cache-dir="$work_root/cache/debootstrap" \
    "$DEBIAN_SUITE" "$rootfs" "$DEBIAN_MIRROR"
}

internal_prepare_stage() {
  local work_root="$1"
  local rootfs="$work_root/rootfs"

  assert_internal_namespace
  trap cleanup_mounts EXIT INT TERM
  mount_chroot_runtime "$rootfs"

  # Avoid a host-specific /run symlink and keep DNS configuration in rootfs.
  rm -f -- "$rootfs/etc/resolv.conf"
  cp -L -- /etc/resolv.conf "$rootfs/etc/resolv.conf"

  chroot "$rootfs" /usr/bin/env -i \
    HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LC_ALL=C LANG=C TZ=UTC \
    /debootstrap/debootstrap --second-stage

  cat >"$rootfs/etc/apt/sources.list" <<EOF
deb $DEBIAN_MIRROR $DEBIAN_SUITE main contrib non-free
deb $DEBIAN_MIRROR ${DEBIAN_SUITE}-updates main contrib non-free
deb $DEBIAN_SECURITY_MIRROR ${DEBIAN_SUITE}-security main contrib non-free
EOF

  # No daemon from a build dependency may start in the chroot.
  cat >"$rootfs/usr/sbin/policy-rc.d" <<'EOF'
#!/bin/sh
exit 101
EOF
  chmod 0755 "$rootfs/usr/sbin/policy-rc.d"

  chroot "$rootfs" /usr/bin/env -i \
    HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LC_ALL=C LANG=C TZ=UTC DEBIAN_FRONTEND=noninteractive \
    apt-get update
  chroot "$rootfs" /usr/bin/env -i \
    HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LC_ALL=C LANG=C TZ=UTC DEBIAN_FRONTEND=noninteractive \
    apt-get install -y --no-install-recommends "${BUILD_PACKAGES[@]}"
  chroot "$rootfs" apt-get clean
  chroot "$rootfs" dpkg-query -W -f='${binary:Package}\t${Version}\n' \
    >"$work_root/package-versions.tsv"
}

internal_build_stage() {
  local work_root="$1" source_date_epoch="$2" make_jobs="$3"
  local rootfs="$work_root/rootfs"

  assert_internal_namespace
  trap cleanup_mounts EXIT INT TERM
  mount_chroot_runtime "$rootfs"
  mkdir -p -- "$rootfs/work/source" "$rootfs/work/build" \
    "$rootfs/work/prefix" "$rootfs/work/tmp" "$rootfs/work/home"
  bind_directory "$work_root/source" "$rootfs/work/source" ro
  bind_directory "$work_root/build" "$rootfs/work/build"
  bind_directory "$work_root/prefix" "$rootfs/work/prefix"
  bind_directory "$work_root/tmp" "$rootfs/work/tmp"
  bind_directory "$work_root/home" "$rootfs/work/home"

  chroot "$rootfs" /usr/bin/env -i \
    HOME=/work/home TMPDIR=/work/tmp \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LC_ALL=C LANG=C TZ=UTC \
    SOURCE_DATE_EPOCH="$source_date_epoch" MAKE_JOBS="$make_jobs" \
    /bin/bash -Eeuo pipefail -c '
      cd /work/build
      /work/source/configure \
        --enable-archs=i386,x86_64 \
        --disable-tests \
        --prefix=/work/prefix
      make -j"$MAKE_JOBS"
      make install
      # ntdll links libunwind but the sniper runtime ships none; bundle the
      # bullseye libunwind so the engine is self-contained (matches the
      # container build and the upstream engine layout).
      luw="$(readlink -f /usr/lib/x86_64-linux-gnu/libunwind.so.8)"
      mkdir -p /work/prefix/lib/x86_64-linux-gnu
      cp -a "$luw" "/work/prefix/lib/x86_64-linux-gnu/$(basename "$luw")"
      ln -sf "$(basename "$luw")" /work/prefix/lib/x86_64-linux-gnu/libunwind.so.8
    '
}

version_is_greater() {
  local candidate="$1" ceiling="$2" greatest
  greatest="$(printf '%s\n%s\n' "$candidate" "$ceiling" | sort -V | tail -n1)"
  [[ "$candidate" != "$ceiling" && "$greatest" == "$candidate" ]]
}

scan_glibc_requirements() {
  local work_root="$1"
  local prefix="$work_root/prefix"
  local report="$work_root/glibc-requirements.tsv"
  local file versions maximum relative
  local elf_count=0 failures=0

  : >"$report"
  printf 'max_glibc\tpath\n' >>"$report"

  while IFS= read -r -d '' file; do
    if ! readelf -h -- "$file" >/dev/null 2>&1; then
      continue
    fi
    ((elf_count += 1))
    versions="$(
      readelf --version-info -- "$file" 2>/dev/null |
        grep -oE 'GLIBC_[0-9]+([.][0-9]+)*' || true
    )"
    maximum="$(printf '%s\n' "$versions" | sed -n 's/^GLIBC_//p' | sort -Vu | tail -n1)"
    relative="${file#"$prefix"/}"
    printf '%s\t%s\n' "${maximum:--}" "$relative" >>"$report"
    if [[ -n "$maximum" ]] && version_is_greater "$maximum" "$GLIBC_CEILING"; then
      printf 'ABI violation: %s requires GLIBC_%s (> GLIBC_%s)\n' \
        "$relative" "$maximum" "$GLIBC_CEILING" >&2
      ((failures += 1))
    fi
  done < <(find "$prefix" -type f -print0)

  ((elf_count > 0)) || die "no ELF file found below $prefix"
  [[ -n "$(find "$prefix" -type f -name wineserver -print -quit)" ]] ||
    die "installed prefix does not contain wineserver"
  [[ -n "$(find "$prefix" -type f -path '*/x86_64-unix/ntdll.so' -print -quit)" ]] ||
    die "installed prefix does not contain x86_64-unix/ntdll.so"
  ((failures == 0)) ||
    die "$failures ELF file(s) exceed the GLIBC_$GLIBC_CEILING ceiling"
}

finalize_build() {
  local work_root="$1"
  local package_versions_sha256

  [[ "${BOL_WINEGDK_INTERNAL:-}" == 1 ]] || die "internal mode is private"
  [[ -f "$work_root/package-versions.tsv" ]] ||
    die "package version record is missing below $work_root"

  printf '==> Enforcing GLIBC_%s ABI ceiling\n' "$GLIBC_CEILING"
  scan_glibc_requirements "$work_root"

  # Bind the installed bytes to the reviewed source/build inputs.  The engine
  # packager requires this machine-readable record; a caller-supplied commit
  # string alone is not accepted as provenance.
  package_versions_sha256="$(sha256sum "$work_root/package-versions.tsv" | cut -d' ' -f1)"
  install -m 0644 "$work_root/package-versions.tsv" \
    "$work_root/prefix/.bol-winegdk-package-versions.tsv"
  cat >"$work_root/prefix/.bol-winegdk-build.env" <<EOF
schema=1
winegdk_commit=$EXPECTED_COMMIT
source_date_epoch=$EXPECTED_SOURCE_DATE_EPOCH
debian_suite=$DEBIAN_SUITE
glibc_ceiling=$GLIBC_CEILING
package_versions_sha256=$package_versions_sha256
EOF
}

case "${1:-}" in
  --internal-foreign)
    [[ $# == 2 ]] || die "invalid internal foreign-stage arguments"
    internal_foreign_stage "$2"
    exit 0
    ;;
  --internal-prepare)
    [[ $# == 2 ]] || die "invalid internal prepare-stage arguments"
    internal_prepare_stage "$2"
    exit 0
    ;;
  --internal-build)
    [[ $# == 4 ]] || die "invalid internal build-stage arguments"
    internal_build_stage "$2" "$3" "$4"
    exit 0
    ;;
  --internal-finalize)
    [[ $# == 2 ]] || die "invalid internal finalize-stage arguments"
    finalize_build "$2"
    exit 0
    ;;
  -h|--help)
    usage 0
    ;;
esac

[[ $# == 2 ]] || usage 2

for command_name in awk chroot cp debootstrap find flock git grep id mount \
  newgidmap newuidmap readelf realpath sed sha256sum sort tail tar umount unshare; do
  require_command "$command_name"
done

CONFIG_COMMIT="$(
  awk -F'"' '/^WINEGDK_SOURCE_COMMIT = "/ { print $2; exit }' \
    "$PROJECT_ROOT/bol/config.py"
)"; readonly CONFIG_COMMIT
[[ "$CONFIG_COMMIT" == "$EXPECTED_COMMIT" ]] ||
  die "bol/config.py pins '$CONFIG_COMMIT', expected '$EXPECTED_COMMIT'"

SOURCE_REPO="$(realpath -- "$2")"
[[ -d "$SOURCE_REPO/.git" || -f "$SOURCE_REPO/.git" ]] ||
  die "not a Git repository: $SOURCE_REPO"

if [[ -L "$1" ]]; then
  die "WORK_ROOT must not be a symbolic link: $1"
fi
if [[ -e "$1" ]]; then
  [[ -d "$1" ]] || die "WORK_ROOT exists but is not a directory: $1"
  [[ -z "$(find "$1" -mindepth 1 -print -quit)" ]] ||
    die "WORK_ROOT must be empty: $1"
else
  mkdir -p -- "$1"
fi
WORK_ROOT="$(realpath -- "$1")"

[[ "$WORK_ROOT" != / ]] || die "refusing WORK_ROOT=/"
path_is_within "$WORK_ROOT" "$PROJECT_ROOT" &&
  die "WORK_ROOT must be outside the BedrockOnLinux repository"
path_is_within "$WORK_ROOT" "$SOURCE_REPO" &&
  die "WORK_ROOT must be outside the WineGDK source repository"

exec 9>"$WORK_ROOT/.build.lock"
flock -n 9 || die "another build owns $WORK_ROOT"

SOURCE_MODE=""
SOURCE_EXPORT_COMMIT=""
if GIT_OPTIONAL_LOCKS=0 git -C "$SOURCE_REPO" cat-file -e \
     "$EXPECTED_COMMIT^{commit}" 2>/dev/null; then
  ACTUAL_COMMIT="$(GIT_OPTIONAL_LOCKS=0 git -C "$SOURCE_REPO" rev-parse \
    "$EXPECTED_COMMIT^{commit}")"
  [[ "$ACTUAL_COMMIT" == "$EXPECTED_COMMIT" ]] ||
    die "commit resolution changed unexpectedly: $ACTUAL_COMMIT"
  SOURCE_MODE="target-commit"
  SOURCE_EXPORT_COMMIT="$EXPECTED_COMMIT"
  SOURCE_DATE_EPOCH="$(GIT_OPTIONAL_LOCKS=0 git -C "$SOURCE_REPO" show \
    -s --format=%ct "$EXPECTED_COMMIT")"
else
  # The local native-auth commit may not be available from every public clone.
  # Reproduce the exact target tree from its reviewed public parent plus the
  # hash-pinned r12 and native deltas without mutating the caller's repository.
  GIT_OPTIONAL_LOCKS=0 git -C "$SOURCE_REPO" cat-file -e \
    "$PUBLIC_BASE_COMMIT^{commit}" ||
    die "source repository contains neither target $EXPECTED_COMMIT nor "\
        "public base $PUBLIC_BASE_COMMIT"
  ACTUAL_BASE="$(GIT_OPTIONAL_LOCKS=0 git -C "$SOURCE_REPO" rev-parse \
    "$PUBLIC_BASE_COMMIT^{commit}")"
  [[ "$ACTUAL_BASE" == "$PUBLIC_BASE_COMMIT" ]] ||
    die "base commit resolution changed unexpectedly: $ACTUAL_BASE"
  [[ -f "$VENDORED_BASE_PATCH" ]] ||
    die "vendored WineGDK r12 base patch is missing"
  [[ "$(sha256sum "$VENDORED_BASE_PATCH" | cut -d' ' -f1)" == \
      "$VENDORED_BASE_PATCH_SHA256" ]] ||
    die "vendored WineGDK r12 base patch SHA-256 mismatch"
  [[ -f "$VENDORED_PATCH" ]] || die "vendored WineGDK native patch is missing"
  [[ "$(sha256sum "$VENDORED_PATCH" | cut -d' ' -f1)" == \
      "$VENDORED_PATCH_SHA256" ]] ||
    die "vendored WineGDK native patch SHA-256 mismatch"
  SOURCE_MODE="public-base+vendored-patches"
  SOURCE_EXPORT_COMMIT="$PUBLIC_BASE_COMMIT"
  SOURCE_DATE_EPOCH="$EXPECTED_SOURCE_DATE_EPOCH"
fi
[[ "$SOURCE_DATE_EPOCH" == "$EXPECTED_SOURCE_DATE_EPOCH" ]] ||
  die "WineGDK source timestamp changed: $SOURCE_DATE_EPOCH"

HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
HOST_USER="$(id -un)"
SUBUID_START="$(find_subid_start /etc/subuid "$HOST_USER" "$HOST_UID")"
SUBGID_START="$(find_subid_start /etc/subgid "$HOST_USER" "$HOST_GID")"
[[ "$SUBUID_START" =~ ^[0-9]+$ ]] ||
  die "need a /etc/subuid range of at least 65535 IDs for $HOST_USER"
[[ "$SUBGID_START" =~ ^[0-9]+$ ]] ||
  die "need a /etc/subgid range of at least 65535 IDs for $HOST_USER"

MAKE_JOBS="${MAKE_JOBS:-$(nproc)}"
[[ "$MAKE_JOBS" =~ ^[1-9][0-9]*$ ]] || die "invalid MAKE_JOBS: $MAKE_JOBS"

readonly -a USER_NAMESPACE_ARGS=(
  --user
  --map-users "0:${HOST_UID}:1"
  --map-users "1:${SUBUID_START}:65535"
  --map-groups "0:${HOST_GID}:1"
  --map-groups "1:${SUBGID_START}:65535"
  --setgid 0
  --setuid 0
)

# Fail before downloading anything if newuidmap/newgidmap or kernel policy do
# not permit the exact two-range mapping required by the build.
unshare "${USER_NAMESPACE_ARGS[@]}" -- /usr/bin/true ||
  die "the required user/subuid namespace mapping is unavailable"

mkdir -p -- "$WORK_ROOT/cache" "$WORK_ROOT/home" "$WORK_ROOT/tmp" \
  "$WORK_ROOT/source" "$WORK_ROOT/build" "$WORK_ROOT/prefix"

cat >"$WORK_ROOT/build-inputs.txt" <<EOF
winegdk_commit=$EXPECTED_COMMIT
source_mode=$SOURCE_MODE
source_export_commit=$SOURCE_EXPORT_COMMIT
public_base_commit=$PUBLIC_BASE_COMMIT
vendored_base_patch_sha256=$VENDORED_BASE_PATCH_SHA256
vendored_patch_sha256=$VENDORED_PATCH_SHA256
source_sha256sums_sha256=$SOURCE_SHA256SUMS_SHA256
source_date_epoch=$SOURCE_DATE_EPOCH
debian_suite=$DEBIAN_SUITE
debian_mirror=$DEBIAN_MIRROR
glibc_ceiling=$GLIBC_CEILING
host_uid=$HOST_UID
host_gid=$HOST_GID
subuid_start=$SUBUID_START
subgid_start=$SUBGID_START
make_jobs=$MAKE_JOBS
EOF

printf '==> Bullseye foreign bootstrap\n'
BOL_WINEGDK_INTERNAL=1 unshare "${USER_NAMESPACE_ARGS[@]}" \
  --mount --propagation private -- \
  "$SCRIPT_PATH" --internal-foreign "$WORK_ROOT"

printf '==> Bullseye second stage and build dependencies\n'
BOL_WINEGDK_INTERNAL=1 unshare "${USER_NAMESPACE_ARGS[@]}" \
  --mount --propagation private -- \
  "$SCRIPT_PATH" --internal-prepare "$WORK_ROOT"

printf '==> Exporting pinned WineGDK source without modifying its repository\n'
GIT_OPTIONAL_LOCKS=0 git -C "$SOURCE_REPO" archive --format=tar \
  "$SOURCE_EXPORT_COMMIT" | tar -xf - -C "$WORK_ROOT/source"
if [[ "$SOURCE_MODE" == "public-base+vendored-patches" ]]; then
  git -C "$WORK_ROOT/source" apply --check "$VENDORED_BASE_PATCH" ||
    die "vendored WineGDK r12 patch does not apply to the public base"
  git -C "$WORK_ROOT/source" apply "$VENDORED_BASE_PATCH" ||
    die "could not apply vendored WineGDK r12 patch"
  git -C "$WORK_ROOT/source" apply --check "$VENDORED_PATCH" ||
    die "vendored WineGDK native patch does not apply after the r12 delta"
  git -C "$WORK_ROOT/source" apply "$VENDORED_PATCH" ||
    die "could not apply vendored WineGDK native patch"
fi
[[ -f "$SOURCE_SHA256SUMS" ]] || die "WineGDK source hash manifest is missing"
[[ "$(sha256sum "$SOURCE_SHA256SUMS" | cut -d' ' -f1)" == \
    "$SOURCE_SHA256SUMS_SHA256" ]] ||
  die "WineGDK source hash manifest SHA-256 mismatch"
(
  cd "$WORK_ROOT/source"
  sha256sum --strict -c "$SOURCE_SHA256SUMS" >/dev/null
) || die "exported WineGDK source does not match the reviewed native delta"

printf '==> Building WineGDK in Bullseye\n'
BOL_WINEGDK_INTERNAL=1 unshare "${USER_NAMESPACE_ARGS[@]}" \
  --mount --propagation private -- \
  "$SCRIPT_PATH" --internal-build "$WORK_ROOT" \
  "$SOURCE_DATE_EPOCH" "$MAKE_JOBS"

BOL_WINEGDK_INTERNAL=1 "$SCRIPT_PATH" --internal-finalize "$WORK_ROOT"

printf '\nBuild complete (not packaged or published).\n'
printf 'Install prefix: %s\n' "$WORK_ROOT/prefix"
printf 'ABI report:     %s\n' "$WORK_ROOT/glibc-requirements.tsv"
