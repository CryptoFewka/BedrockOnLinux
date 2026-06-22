"""bol.config — constants, paths, repos and URLs (no logic, no side effects)."""
# SPDX-License-Identifier: MIT

import os
from pathlib import Path

APP = "bedrock-on-linux"
PRETTY = "BedrockOnLinux"
VERSION = "1.2.3"

HOME = Path.home()
DATA = Path(os.environ.get("BOL_HOME", HOME / ".local/share" / APP))
PROTON_DIR = DATA / "proton"
UMU_DIR = DATA / "umu"
COMPAT = DATA / "compatdata"          # Proton-managed Wine prefix lives here
PFX = COMPAT / "pfx"
GAMES = DATA / "games"
CONTENT = DATA / "content"
CACHE = DATA / "cache"
LOGS = DATA / "logs"
MSA_DIR = DATA / "msa"                 # native (no-ProxyPass) login token
SETTINGS = DATA / "settings.json"

GDK_PROTON_REPO = "Weather-OS/GDK-Proton"
UMU_REPO = "Open-Wine-Components/umu-launcher"
GAME_ARCHIVE_REPO = "bubbles-wow/mcbe-gdk-unpack-archive"
MINGW_CURL = "https://mirror.msys2.org/mingw/mingw64/mingw-w64-x86_64-curl-8.17.0-1-any.pkg.tar.zst"
CACERT_URL = "https://curl.se/ca/cacert.pem"

# In-game Microsoft login: WineGDK's XUser has no sign-in UI — it reads an
# MSA OAuth refresh token from the prefix registry (WINEGDK_REG) and does the
# Xbox Live exchange itself. The launcher only runs the device-code flow and
# seeds that token. MSA_CLIENT_ID must equal WineGDK's hardcoded msaAppId or
# the refresh is rejected.
MSA_CLIENT_ID = "0000000048183522"     # Bedrock Android (matches WineGDK XUser.c)
MSA_SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"
MSA_CONNECT = "https://login.live.com/oauth20_connect.srf"
MSA_TOKEN = "https://login.live.com/oauth20_token.srf"
WINEGDK_REG = r"Software\Wine\WineGDK"  # value RefreshToken (REG_SZ)

# The WineGDK fork (XUser + request signing) the engine is built from when no
# prebuilt asset is available.
WINEGDK_REPO = "https://github.com/Wyze3306/WineGDK.git"
WINEGDK_BRANCH = "master"
# OSS replacements for the game's GDK Xbox-Live DLLs (minecraft-linux project).
GDK_DEPS_URL = "https://github.com/minecraft-linux/mcpelauncher-gdk-dependencies/releases/download/v0.0.0"
GDK_DEPS_DLLS = ("libHttpClient.GDK.dll", "XCurl.dll")
# OpenSSL libcurl + CA-shim + cryptbase stub set: installed as XCurl.dll so
# Minecraft's PlayFab traffic goes over OpenSSL TLS instead of Wine secur32
# (whose handshake Azure Front Door silently FINs → endless sign-in loop).
# Too big to bundle (20 MB) — downloaded once from the app's releases as
# openssl-xcurl-set-<rev>.tar.gz. Republish: scripts/package-openssl-xcurl.sh.
OPENSSL_XCURL_SET = DATA / "xodus-xcurl" / "openssl-set"
OPENSSL_XCURL_REV = "17bc4b81e178"
WINEGDK_SRC = DATA / "winegdk-src"
WINEGDK_OUT = PROTON_DIR / "GDK-Proton-xuser"
# Prebuilt engine: users download GDK-Proton-xuser-<build-rev>.tar.gz from the
# app's releases instead of compiling Wine; source build is the fallback.
# Republish: scripts/package-engine.sh.
WINEGDK_PREBUILT_REPO = "Wyze3306/BedrockOnLinux"
# Bump when the build/packaging method changes → forces a clean rebuild.
WINEGDK_BUILD_REV = "wow64-archs-r6"

# Build deps for the from-source fallback — a direct list, because
# `apt build-dep wine` needs deb-src entries Mint/LMDE don't ship.
WINE_BUILD_PKGS = (
    "flex bison gcc-mingw-w64-x86-64 gcc-mingw-w64-i686 pkg-config "
    "libx11-dev libxext-dev libxrandr-dev libxrender-dev libxi-dev "
    "libxcursor-dev libxcomposite-dev libxinerama-dev libxfixes-dev "
    "libxxf86vm-dev libfreetype-dev libfontconfig1-dev libgl1-mesa-dev "
    "libvulkan-dev libgnutls28-dev libpulse-dev libasound2-dev "
    "libudev-dev libsdl2-dev libusb-1.0-0-dev libpcap-dev "
    "libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev")


# ---- launcher self-update -------------------------------------------------
SELF_REPO = WINEGDK_PREBUILT_REPO          # the launcher's own GitHub repo
