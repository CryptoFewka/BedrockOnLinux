#!/usr/bin/env bash
# Build a .deb so BedrockOnLinux installs like a normal app (menu + search).
# Usage: scripts/build-deb.sh        -> dist/bedrock-on-linux_<ver>_amd64.deb
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VER="$(grep -m1 '^VERSION = ' "$SRC/bol/config.py" | cut -d'"' -f2)"
OUT="$SRC/dist"
PKG="$OUT/deb"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1782250551}"
[[ "$SOURCE_DATE_EPOCH" =~ ^[0-9]+$ ]] || {
  echo "SOURCE_DATE_EPOCH must be a non-negative integer" >&2
  exit 1
}
export SOURCE_DATE_EPOCH
rm -rf "$PKG"
mkdir -p "$OUT" \
  "$PKG/DEBIAN" \
  "$PKG/usr/lib/bedrock-on-linux/data" \
  "$PKG/usr/bin" \
  "$PKG/usr/share/applications" \
  "$PKG/usr/share/icons/hicolor/256x256/apps" \
  "$PKG/usr/share/doc/bedrock-on-linux"

[[ -f "$SRC/data/icon.png" ]] || { echo "data/icon.png missing" >&2; exit 1; }

install -m755 "$SRC/bedrock-on-linux" "$PKG/usr/lib/bedrock-on-linux/bedrock-on-linux"
cp -r "$SRC/bol"                       "$PKG/usr/lib/bedrock-on-linux/bol"
# Bundle the GUI toolkit (customtkinter + darkdetect + packaging — pure Python,
# not packaged by Debian) next to bol/ so it's on sys.path; a real dir means
# customtkinter's theme/font assets load fine. cryptography/tk stay apt deps.
# python-xlib (+ six) is bundled the same way for bol.x11's primary-monitor
# lookup; it's optional (bol.x11 falls back to the xrandr CLI without it).
# Hash-pinned, wheels only, no sdist builds: closure + SHA-256s live in
# third_party/requirements-deb.txt (--require-hashes rejects any mismatch).
python3 -m pip install --quiet --no-cache-dir --no-compile --no-deps \
  --require-hashes --only-binary=:all: --target \
  "$PKG/usr/lib/bedrock-on-linux" \
  -r "$SRC/third_party/requirements-deb.txt"
rm -rf "$PKG/usr/lib/bedrock-on-linux"/bin 2>/dev/null || true
find "$PKG/usr/lib/bedrock-on-linux" -name __pycache__ -type d -exec rm -rf {} +
for metadata in \
  "$PKG/usr/lib/bedrock-on-linux/customtkinter-5.2.2.dist-info" \
  "$PKG/usr/lib/bedrock-on-linux/darkdetect-0.8.0.dist-info" \
  "$PKG/usr/lib/bedrock-on-linux/packaging-26.2.dist-info" \
  "$PKG/usr/lib/bedrock-on-linux/python_xlib-0.33.dist-info" \
  "$PKG/usr/lib/bedrock-on-linux/six-1.17.0.dist-info"; do
  [[ -d "$metadata" ]] || {
    echo "missing pinned dependency metadata: $metadata" >&2
    exit 1
  }
  dependency_license="$(
    find "$metadata" -type f -iname 'LICENSE*' -print -quit
  )"
  [[ -n "$dependency_license" ]] || {
    echo "missing dependency licence in $metadata" >&2
    exit 1
  }
done
install -m644 "$SRC/data/icon.png"    "$PKG/usr/lib/bedrock-on-linux/data/icon.png"
ln -s /usr/lib/bedrock-on-linux/bedrock-on-linux "$PKG/usr/bin/bedrock-on-linux"
install -m644 "$SRC/data/icon.png" \
  "$PKG/usr/share/icons/hicolor/256x256/apps/bedrock-on-linux.png"
install -m644 "$SRC/data/bedrock-on-linux.desktop" \
  "$PKG/usr/share/applications/bedrock-on-linux.desktop"
install -m644 "$SRC/README.md" "$PKG/usr/share/doc/bedrock-on-linux/README.md"
install -m644 "$SRC/LICENSE" "$PKG/usr/share/doc/bedrock-on-linux/copyright"

cat > "$PKG/DEBIAN/control" <<EOF
Package: bedrock-on-linux
Version: ${VER}
Section: games
Priority: optional
Architecture: amd64
Depends: python3 (>= 3.9), python3-tk, python3-cryptography, tar, zstd, xdg-utils, x11-xserver-utils, ca-certificates, curl | wget
Recommends: mesa-vulkan-drivers | nvidia-driver
Maintainer: BedrockOnLinux contributors <noreply@bedrockonlinux.invalid>
Homepage: https://github.com/Wyze3306/BedrockOnLinux
Description: Run Minecraft Bedrock (Windows GDK) on Linux, multiplayer included
 One graphical launcher that downloads a reviewed WineGDK-based GDK-Proton,
 provides native Microsoft/Xbox identity, and enables Friends, Servers,
 Realms and file imports without a Minecraft process-memory patcher.
 Sign-in is direct (no relay or proxy).
 No game files are shipped; you supply your own.
EOF

cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
update-desktop-database -q /usr/share/applications 2>/dev/null || true
gtk-update-icon-cache -q -t /usr/share/icons/hicolor 2>/dev/null || true
exit 0
EOF

# Wheel archives occasionally carry host-specific modes or Finder metadata.
# Debian payloads should be stable and readable regardless of the build host.
find "$PKG" -type f -name '.DS_Store' -delete
find "$PKG" -type d -exec chmod 0755 {} +
find "$PKG" -type f -exec chmod 0644 {} +
chmod 0755 "$PKG/DEBIAN/postinst" \
  "$PKG/usr/lib/bedrock-on-linux/bedrock-on-linux"
# Normalise every payload and control timestamp explicitly. dpkg-deb also
# consumes SOURCE_DATE_EPOCH for its ar/tar metadata, making standalone and
# aggregate release builds byte-for-byte reproducible.
find "$PKG" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +

DEB="$OUT/bedrock-on-linux_${VER}_amd64.deb"
dpkg-deb --build --root-owner-group "$PKG" "$DEB" >/dev/null
rm -rf "$PKG"
echo "Built: $DEB"
dpkg-deb -I "$DEB" | sed -n '1,12p'
echo "Install:  sudo apt install $DEB     (or: sudo dpkg -i $DEB)"
