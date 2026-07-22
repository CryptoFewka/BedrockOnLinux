#!/usr/bin/env bash
# Point apt at a fixed snapshot.debian.org timestamp so the build toolchain is
# frozen. This makes the containerized builds reproducible across TIME, not just
# run-to-run: without it, `apt-get install` pulls whatever the live mirror
# currently serves, so a Debian package update would silently change the output
# bytes and trip the fail-closed SHA asserts. Run as root inside the build
# container, before `apt-get update`.
#
# Usage: pin-apt-snapshot.sh <bullseye|trixie>
set -Eeuo pipefail

SUITE="${1:?usage: pin-apt-snapshot.sh <suite>}"
# Pinned reproducibility constant, like SOURCE_DATE_EPOCH. Bump only alongside a
# deliberate re-baseline of the affected component hashes (see docs/BUILD.md).
readonly SNAPSHOT="20260701T000000Z"
readonly BASE="http://snapshot.debian.org/archive/debian/${SNAPSHOT}"
readonly SEC="http://snapshot.debian.org/archive/debian-security/${SNAPSHOT}"

# Replace whatever the base image ships (sources.list and/or deb822 .sources).
rm -f /etc/apt/sources.list \
      /etc/apt/sources.list.d/*.list \
      /etc/apt/sources.list.d/*.sources 2>/dev/null || true

cat > /etc/apt/sources.list <<EOF
deb [check-valid-until=no] ${BASE}/ ${SUITE} main
deb [check-valid-until=no] ${BASE}/ ${SUITE}-updates main
deb [check-valid-until=no] ${SEC}/ ${SUITE}-security main
EOF

echo "== apt pinned to snapshot.debian.org ${SNAPSHOT} (${SUITE})"
