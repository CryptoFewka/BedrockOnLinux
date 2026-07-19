"""bol.config — constants, paths, repos and URLs (no logic, no side effects)."""
# SPDX-License-Identifier: MIT

import os
from pathlib import Path

APP = "bedrock-on-linux"
PRETTY = "BedrockOnLinux"
VERSION = "2.0.0"

HOME = Path.home()
DATA = Path(os.environ.get("BOL_HOME", HOME / ".local/share" / APP))
PROTON_DIR = DATA / "proton"
UMU_DIR = DATA / "umu"
COMPAT = DATA / "compatdata"
PFX = COMPAT / "pfx"
GAMES = DATA / "games"
CONTENT = DATA / "content"
CACHE = DATA / "cache"
LOGS = DATA / "logs"
MSA_DIR = DATA / "msa"
SETTINGS = DATA / "settings.json"

GDK_PROTON_REPO = "Weather-OS/GDK-Proton"
UMU_REPO = "Open-Wine-Components/umu-launcher"
UMU_VERSION = "1.4.0"
UMU_ASSET = "umu-launcher-1.4.0-zipapp.tar"
UMU_ARCHIVE_SHA256 = \
    "138ce4b8843608a257d4bee88191ca78a989778bcefd8abb3c1d1aaac3ac6fb8"
UMU_RUN_SHA256 = \
    "bdac203e74f77b8b375ce72de0671c5a227815b912faacd09ecf450ee6650f62"
GAME_ARCHIVE_REPO = "bubbles-wow/mcbe-gdk-unpack-archive"
MINGW_CURL = "https://mirror.msys2.org/mingw/mingw64/mingw-w64-x86_64-curl-8.17.0-1-any.pkg.tar.zst"
CACERT_URL = "https://curl.se/ca/cacert.pem"

# In-game Microsoft login: WineGDK's XUser has no sign-in UI — it reads an
# MSA OAuth refresh token from the prefix registry (WINEGDK_REG) and does the
# Xbox Live exchange itself. The launcher only runs the device-code flow and
# seeds that token. MSA_CLIENT_ID must equal WineGDK's hardcoded msaAppId or
# the refresh is rejected.
MSA_CLIENT_ID = "0000000048183522"
MSA_SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"
MSA_CONNECT = "https://login.live.com/oauth20_connect.srf"
MSA_TOKEN = "https://login.live.com/oauth20_token.srf"
WINEGDK_REG = r"Software\Wine\WineGDK"

# Exact WineGDK source used for the reviewed native-online engine. It
# completes XGame/XUser identity, XSAPI context initialization, stable native
# task-queue bootstrap and Realms audience routing, implements the WinAppSDK
# file picker, and removes the Minecraft process-memory patcher.
WINEGDK_SOURCE_COMMIT = "75637b674e1f191e65753663c4c0c32bea05ba6e"
# OSS replacements for the game's GDK Xbox-Live DLLs (minecraft-linux project).
GDK_DEPS_URL = "https://github.com/minecraft-linux/mcpelauncher-gdk-dependencies/releases/download/v0.0.0"
GDK_DEPS_DLLS = ("libHttpClient.GDK.dll", "XCurl.dll")
# OpenSSL libcurl + CA-shim + cryptbase stub set: installed as XCurl.dll so
# Minecraft's PlayFab traffic goes over OpenSSL TLS instead of Wine secur32
# (whose handshake Azure Front Door silently FINs → endless sign-in loop).
# Too big to bundle (20 MB) — downloaded once from the app's releases as
# openssl-xcurl-set-<rev>.tar.gz. This fork builds it reproducibly from source
# (scripts/build-openssl-xcurl.sh: pinned msys2 curl closure + source shim +
# source cryptbase stub), so the rev/SHA below track our own build.
OPENSSL_XCURL_SET = DATA / "xodus-xcurl" / "openssl-set"
OPENSSL_XCURL_REV = "313c1409fbdf"
# Exact reviewed online-login payload. A filename/revision alone is not an
# integrity boundary; local siblings and downloaded assets must match this pin.
OPENSSL_XCURL_ARCHIVE_SHA256 = "313c1409fbdf4318a86c18b3ef19be2d65b25487ec5b26abd7944cb9ed2d0afb"
WINEGDK_OUT = PROTON_DIR / "GDK-Proton-xuser"
# Prebuilt engine: users download GDK-Proton-xuser-<build-rev>.tar.gz from the
# app's releases instead of compiling Wine.  Managed engines are fail-closed:
# the launcher only accepts the exact archive hash compiled into this version.
# Build locally with scripts/package-engine.sh, then pin its printed SHA-256.
# This fork consumes its own reproducibly CI-built engine (see
# .github/workflows/build-engine.yml), which reproduces the exact bytes below.
WINEGDK_PREBUILT_REPO = "CryptoFewka/BedrockOnLinux"
# Bump when the build/packaging method changes → forces a clean rebuild.
WINEGDK_BUILD_REV = "wow64-archs-native5"
# SHA-256 of the reviewed, deterministic engine archive. An invalid value makes
# the installer fail closed rather than accepting a differently packed engine.
WINEGDK_ARCHIVE_SHA256 = "0c2572619e24facf405170235023d8a5d11d22080dfe8c67975fd2c461201c4d"

# Decoupled from the engine repo: the launcher checks upstream for its own
# update notifications so this follow-upstream fork still learns about new
# upstream releases, while the engine above comes from our own CI build.
SELF_REPO = "Wyze3306/BedrockOnLinux"
