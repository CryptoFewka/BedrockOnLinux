"""bol.config — constants, paths, repos and URLs (no logic, no side effects)."""
# SPDX-License-Identifier: MIT

import os
from pathlib import Path

APP = "bedrock-on-linux"
PRETTY = "BedrockOnLinux"
VERSION = "2.1.1"

HOME = Path.home()
XDG_DATA_HOME = Path(
    os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share"
).expanduser()
XDG_CONFIG_HOME = Path(
    os.environ.get("XDG_CONFIG_HOME") or HOME / ".config"
).expanduser()
DEFAULT_DATA = XDG_DATA_HOME / APP
LEGACY_DATA = HOME / ".local" / "share" / APP

# Resolve relocation before exporting DATA; imported path constants cannot be
# changed afterwards. BOL_HOME takes priority over the persistent pointer.
INSTALL_LOCATION_FILE = XDG_CONFIG_HOME / APP / "install_location"
LEGACY_INSTALL_LOCATION_FILE = HOME / ".config" / APP / "install_location"

_bol_home = os.environ.get("BOL_HOME", "").strip()
if _bol_home:
    _data_path = _bol_home
else:
    _data_path = str(DEFAULT_DATA)
    # Keep the pre-XDG pointer fallback so upgrades retain relocated data.
    _pointer_candidates = (INSTALL_LOCATION_FILE,)
    if LEGACY_INSTALL_LOCATION_FILE != INSTALL_LOCATION_FILE:
        _pointer_candidates += (LEGACY_INSTALL_LOCATION_FILE,)
    for _pointer in _pointer_candidates:
        try:
            if not _pointer.is_file():
                continue
            _custom_home = _pointer.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if _custom_home:
            _data_path = _custom_home
            break

DATA = Path(_data_path)
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
UMU_VERSION = "1.4.3"
UMU_ASSET = "umu-launcher-1.4.3-zipapp.tar"
UMU_ARCHIVE_SHA256 = \
    "3f8fdc033f547afdb3408ea48ad07194769405148dcfa2b2f945b7fb368a33bb"
UMU_RUN_SHA256 = \
    "577181dbff2eccdaa78b411c0fd1aa7fde574028449c3e0e99f508536a76870e"
GAME_ARCHIVE_REPO = "bubbles-wow/mcbe-gdk-unpack-archive"
MINGW_CURL = "https://mirror.msys2.org/mingw/mingw64/mingw-w64-x86_64-curl-8.17.0-1-any.pkg.tar.zst"
CACERT_URL = "https://curl.se/ca/cacert.pem"

# WineGDK reads the refresh token from this registry key and requires its
# hardcoded MSA application ID.
MSA_CLIENT_ID = "0000000048183522"
MSA_SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"
MSA_CONNECT = "https://login.live.com/oauth20_connect.srf"
MSA_TOKEN = "https://login.live.com/oauth20_token.srf"
WINEGDK_REG = r"Software\Wine\WineGDK"

# Exact WineGDK source used for the reviewed native-online engine.
WINEGDK_SOURCE_COMMIT = "75637b674e1f191e65753663c4c0c32bea05ba6e"
GDK_DEPS_URL = "https://github.com/minecraft-linux/mcpelauncher-gdk-dependencies/releases/download/v0.0.0"
GDK_DEPS_DLLS = ("libHttpClient.GDK.dll", "XCurl.dll")
# This OpenSSL XCurl payload avoids Wine secur32 failures against Azure and is
# built reproducibly by scripts/build-openssl-xcurl.sh.
OPENSSL_XCURL_SET = DATA / "xodus-xcurl" / "openssl-set"
OPENSSL_XCURL_REV = "504bb166e4e7"
# Integrity pin for the complete online-login payload.
OPENSSL_XCURL_ARCHIVE_SHA256 = "504bb166e4e737ad81c3ac8e7a917740b28478f69acd89e538c3bf921c29523f"
WINEGDK_OUT = PROTON_DIR / "GDK-Proton-xuser"
# Managed engines are accepted only when their archive and source identity
# match the reviewed pins below. This fork consumes its own reproducibly
# CI-built engine (.github/workflows/build-engine.yml), which reproduces the
# exact bytes pinned here, so the pins match upstream's while the source differs.
WINEGDK_PREBUILT_REPO = "CryptoFewka/BedrockOnLinux"
# The commit alone does not identify vendored follow-up patches.
WINEGDK_SOURCE_MANIFEST_SHA256 = "2dc69fe66823ab29cc3fd54b92605d9f9149eb8006486c0e7450192b2857cbb6"
WINEGDK_BUILD_REV = "wow64-archs-native12"
WINEGDK_ARCHIVE_SHA256 = "481738ec668377d68207d8fdcc7b59db6d3178a0c23bf17dca5981f3f6d351cf"
# Build workflows verify this deterministic intermediate before reusing it.
WINEGDK_PREFIX_SHA256 = "bcb5f9a2974c6159dca8006ffc158cfa183d43aadf079a90eac21124804c5da2"

# Decoupled from the engine repo: the launcher checks upstream for its own
# update notifications so this follow-upstream fork still learns about new
# upstream releases, while the engine above comes from our own CI build.
SELF_REPO = "Wyze3306/BedrockOnLinux"


def _legacy_install_location_file() -> Path:
    return HOME / ".config" / APP / "install_location"


def get_install_location() -> str:
    """Where the app's data directory currently resolves to."""
    return str(DATA)

def default_install_location() -> str:
    """The location used when no custom install location is set."""
    return str(DEFAULT_DATA)

def set_install_location(path) -> None:
    """Persist a custom data-directory location for future runs.

    Raises RuntimeError if BOL_HOME is set externally (relocation disabled).
    """
    if os.environ.get("BOL_HOME", "").strip():
        raise RuntimeError("Cannot change location when BOL_HOME is set externally")
    INSTALL_LOCATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    INSTALL_LOCATION_FILE.write_text(
        str(Path(path).expanduser()), encoding="utf-8")
    legacy = _legacy_install_location_file()
    if legacy != INSTALL_LOCATION_FILE:
        legacy.unlink(missing_ok=True)

def clear_install_location() -> None:
    """Revert to the default location.

    Raises RuntimeError if BOL_HOME is set externally (relocation disabled).
    """
    if os.environ.get("BOL_HOME", "").strip():
        raise RuntimeError("Cannot change location when BOL_HOME is set externally")
    INSTALL_LOCATION_FILE.unlink(missing_ok=True)
    legacy = _legacy_install_location_file()
    if legacy != INSTALL_LOCATION_FILE:
        legacy.unlink(missing_ok=True)

def is_relocation_allowed() -> bool:
    """Return True if the data directory can be relocated via the GUI."""
    return not bool(os.environ.get("BOL_HOME", "").strip())
