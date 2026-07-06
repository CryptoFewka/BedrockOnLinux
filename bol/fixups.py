"""bol.fixups — in-prefix / in-game fixups (curl SSL, DLLs, OpenSSL XCurl, cryptbase, UI)."""
# SPDX-License-Identifier: MIT

import re
import shutil
import struct
import tarfile
from pathlib import Path

from .config import (
    CACERT_URL,
    CACHE,
    GDK_DEPS_DLLS,
    GDK_DEPS_URL,
    MINGW_CURL,
    OPENSSL_XCURL_REV,
    OPENSSL_XCURL_SET,
    WINEGDK_PREBUILT_REPO,
)
from .log import BolError, info, ok, warn
from .pe import apply_patch
from .prefix import active_prefix
from .util import asset_url, download, gh_releases, run

def fix_curl_ssl(game_dir: Path):
    """GDK's XCurl.dll is broken under Wine — swap in MinGW libcurl — and
    GDK-Proton requires a CA bundle at etc/ssl/certs/ca-bundle.crt next to
    the game, else every TLS call (Xbox/online) fails and the server join
    hangs forever. Cert step runs every time (idempotent)."""
    cacert = CACHE / "cacert.pem"
    if not cacert.exists():
        download(CACERT_URL, cacert, "certificats SSL")
    for base in (game_dir, game_dir.parent):
        crt = base / "etc" / "ssl" / "certs" / "ca-bundle.crt"
        crt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cacert, crt)
    if (game_dir / "XCurl.dll.bol-orig").exists():
        return
    info("Installing libcurl + certificates …")
    pkg = CACHE / "mingw-curl.pkg.tar.zst"
    if not pkg.exists():
        download(MINGW_CURL, pkg, "libcurl")
    ex = CACHE / "mingw-curl"
    ex.mkdir(exist_ok=True)
    try:
        run(["tar", "--use-compress-program=unzstd", "-xf", str(pkg),
             "-C", str(ex)], capture_output=True)
    except Exception:
        run(["tar", "-xf", str(pkg), "-C", str(ex)])
    libcurl = next(ex.rglob("libcurl-4.dll"))
    if (game_dir / "XCurl.dll").exists():
        shutil.copy2(game_dir / "XCurl.dll", game_dir / "XCurl.dll.bol-orig")
    for nm in ("XCurl.dll", "Xcurl.dll"):
        shutil.copy2(libcurl, game_dir / nm)
    ok("libcurl ready")


def install_gdk_xbox_dlls(game_dir: Path):
    """Drop the OSS GDK Xbox-Live DLLs into the game folder
    (libHttpClient.GDK.dll, XCurl.dll), backing up originals. The XCurl
    backup also makes fix_curl_ssl skip its libcurl swap so this one
    stays. Idempotent."""
    for nm in GDK_DEPS_DLLS:
        dst = game_dir / nm
        bak = Path(str(dst) + ".bol-orig")
        if bak.exists():
            continue
        cached = CACHE / ("gdkdeps-" + nm)
        if not cached.exists():
            download(f"{GDK_DEPS_URL}/{nm}", cached, nm)
        if dst.exists():
            shutil.copy2(dst, bak)
        shutil.copy2(cached, dst)
    ok("Xbox-Live OSS DLLs installed")
    _install_openssl_xcurl(game_dir)
    _patch_lhc_xcurl_gate(game_dir)
    _patch_hbui_signin_gate(game_dir)


def _patch_hbui_signin_gate(game_dir: Path):
    """Bypass HBUI's 'You need a Microsoft account' banner that blocks the
    Servers tab: its derived facet (wB() in the minified JS bundle) reports
    not-signed-in because XSAPI never completes init under Wine, even though
    the SISU/PlayFab auth works. An early `return ""` makes every consumer
    fall through to `default: return null`. Idempotent; skips silently when
    a Minecraft update rebundles the JS (pattern must then be re-found).

    NOTE: forcing the isLoggedInWithMicrosoftAccount facet true (to unlock
    Profiles / Skins / Realms / the Sign-in button) was tried and reverted —
    it only removes the UI gate, exposing that those features genuinely need
    XSAPI social/persona, which does not work under Wine (they then loop or
    crash). That's an engine-level (WineGDK) problem, not a UI patch."""
    import glob, re
    for js in glob.glob(str(game_dir / "data/gui/dist/hbui/index-*.js")):
        try:
            data = Path(js).read_text()
        except OSError:
            continue
        orig = data

        # (1) Servers-tab "need a Microsoft account" banner: wB() -> "" so every
        #     consumer hits `default: return null`. Legacy pattern; left as-is.
        needle = 'function wB(){return(0,l.useFacetMap)'
        if needle in data and 'function wB(){return"";return' not in data:
            data = data.replace(needle, 'function wB(){return"";return', 1)

        # (2) Remove the broken in-game "Sign in" button. The PlayScreen
        #     not-logged-in warning component (kB) renders a noticeTint with a
        #     sign-in link; under Wine the MSA facet never flips so it shows
        #     forever and the button does nothing useful. Make that component
        #     render null — AFTER its hooks (the useCallback whose body holds the
        #     stable "_NotLoggedInWarning_OreUI" sign-in source), so React's hook
        #     order is preserved (unlike a naive early return). Version-tolerant:
        #     anchored on the immutable signInSource string, minified names free.
        m = re.search(r'(_NotLoggedInWarning_OreUI`\)\}\),\[[^\]]*\]\);)'
                      r'(return r\.createElement\(sx,)', data)
        if m and 'return null;return r.createElement(sx,' not in data:
            data = data[:m.start()] + m.group(1) + 'return null;' + m.group(2) + data[m.end():]

        if data == orig:
            continue
        bak = js + ".bol-orig"
        if not Path(bak).exists():
            shutil.copy2(js, bak)
        Path(js).write_text(data)
        ok(f"HBUI sign-in gate + button patched in {Path(js).name}")
        return


def ensure_openssl_xcurl_set():
    """Fetch + unpack the OpenSSL XCurl set (release asset, 20 MB) into
    OPENSSL_XCURL_SET on first use. Idempotent via a .rev marker; any
    network/IO failure degrades quietly — _install_openssl_xcurl() then
    keeps the Schannel XCurl and warns."""
    marker = OPENSSL_XCURL_SET / ".rev"
    have = (OPENSSL_XCURL_SET / "libcurl-4.dll").exists() and \
           (OPENSSL_XCURL_SET / "xcurl-cashim.dll").exists()
    if have and marker.exists() and \
            marker.read_text().strip() == OPENSSL_XCURL_REV:
        return True
    asset = f"openssl-xcurl-set-{OPENSSL_XCURL_REV}.tar.gz"
    try:
        rels = gh_releases(WINEGDK_PREBUILT_REPO, 30)
    except Exception as e:
        warn(f"OpenSSL XCurl set lookup failed ({e}).")
        return have
    url = None
    for rel in rels or []:
        url, _name, _ = asset_url(rel, lambda n: n == asset)
        if url:
            break
    if not url:
        warn(f"OpenSSL XCurl set asset '{asset}' not published yet.")
        return have
    tar = CACHE / asset
    if not tar.exists():
        info("Downloading the online-login components (one-time) …")
        try:
            download(url, tar, "Online-login components")
        except BolError:
            return have
    tmp = OPENSSL_XCURL_SET.parent / ".set-dl"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(tar) as t:
            t.extractall(tmp)
    except Exception as e:
        warn(f"OpenSSL XCurl set archive unreadable ({e}).")
        shutil.rmtree(tmp, ignore_errors=True)
        tar.unlink(missing_ok=True)
        return have
    # Merge into the set dir (don't blow away a maintainer's working tree),
    # then stamp the rev so we skip next time.
    OPENSSL_XCURL_SET.mkdir(parents=True, exist_ok=True)
    for f in tmp.iterdir():
        if f.is_file():
            shutil.copy2(f, OPENSSL_XCURL_SET / f.name)
    shutil.rmtree(tmp, ignore_errors=True)
    marker.write_text(OPENSSL_XCURL_REV)
    ok("Online-login components ready.")
    return True


def _install_openssl_xcurl(game_dir: Path):
    """Route Minecraft's PlayFab HTTP over OpenSSL instead of Wine secur32
    (whose Schannel TLS Azure Front Door silently FINs → login loops).

    Lands in the game dir: XCurl.dll = a CA-injecting shim (libHttpClient
    never sets CURLOPT_CAINFO, so the shim injects <dir>\\cacert.pem at
    curl_easy_init and forwards everything else), xcurl_real.dll = the real
    OpenSSL libcurl, plus cacert.pem and the libssl/zlib dependency set.
    Idempotent."""
    ensure_openssl_xcurl_set()                 # fetch the 20 MB set on first use
    s = OPENSSL_XCURL_SET
    libcurl = s / "libcurl-4.dll"
    shim = s / "xcurl-cashim.dll"
    if not libcurl.exists() or not shim.exists():
        warn(f"OpenSSL XCurl set incomplete at {s} — keeping the Schannel "
             "XCurl.dll (native PlayFab login will fail under Wine secur32).")
        return
    # Preserve the genuine original XCurl once (install_gdk_xbox_dlls usually
    # already did this; guard anyway so we never lose it).
    for nm in ("XCurl.dll", "Xcurl.dll"):
        dst = game_dir / nm
        bak = Path(str(dst) + ".bol-orig")
        if dst.exists() and not bak.exists():
            shutil.copy2(dst, bak)
    # The whole dependency set, EXCEPT the shim source/variants and cryptbase
    # (cryptbase belongs in system32, not the game dir).
    skip = {"cryptbase.dll", "xcurl-cashim.dll"}
    for dll in sorted(s.glob("*.dll")):
        if dll.name in skip or dll.name.endswith((".bak", ".fwd-bak",
                                                  ".1export-bak")):
            continue
        shutil.copy2(dll, game_dir / dll.name)
    # the real OpenSSL libcurl the shim forwards to
    shutil.copy2(libcurl, game_dir / "xcurl_real.dll")
    # the CA-injecting shim becomes XCurl.dll (both casings libHttpClient probes)
    for nm in ("XCurl.dll", "Xcurl.dll"):
        shutil.copy2(shim, game_dir / nm)
    # the CA bundle the shim points CURLOPT_CAINFO at (fix_curl_ssl downloads it)
    cacert = CACHE / "cacert.pem"
    if not cacert.exists():
        try:
            download(CACERT_URL, cacert, "certificats SSL")
        except Exception as e:
            warn(f"cacert download failed: {e}")
    if cacert.exists():
        shutil.copy2(cacert, game_dir / "cacert.pem")
    ok("OpenSSL XCurl (CA-injecting shim) + deps installed "
       "(PlayFab Azure Front Door bypass)")


def _install_cryptbase_in_prefix(pfx=None):
    """Install the cryptbase.dll RNG stub into the prefix system32 — Wine
    resolves advapi32's SystemFunction036 (RtlGenRandom) forward from there,
    not the game dir, and the OpenSSL XCurl aborts at its first TLS RNG call
    without it. Backs up any existing cryptbase once; idempotent."""
    src = OPENSSL_XCURL_SET / "cryptbase.dll"
    if not src.exists():
        # OpenSSL XCurl set unavailable (download failed, or the rev's asset
        # isn't published yet). Without ANY cryptbase, GDK-Proton's advapi32
        # forward of SystemFunction036 (RtlGenRandom) stays unresolved and the
        # game aborts on its first RNG call before opening a window — a dead
        # prefix / ghost window that masquerades as a GPU/display fault. The
        # cryptbase=n,b override only falls back to a real cryptbase.dll FILE in
        # the prefix, so seed one from GDK-Proton's own builtin (it exports
        # SystemFunction036 — verified). Enough to boot a window; the OpenSSL TLS
        # shim lives in a separate XCurl.dll and is unaffected.
        from .proton import proton_path
        pp = proton_path()
        builtin = (pp / "files/lib/wine/x86_64-windows/cryptbase.dll") if pp else None
        if not (builtin and builtin.exists()):
            return
        src = builtin
    pfx = pfx or active_prefix()
    sys32 = pfx / "drive_c/windows/system32"
    if not sys32.is_dir():
        warn(f"prefix system32 not found at {sys32} — cryptbase stub not "
             "installed (native PlayFab login may fail).")
        return
    dst = sys32 / "cryptbase.dll"
    try:
        import hashlib
        if dst.exists() and hashlib.sha1(dst.read_bytes()).digest() == \
                hashlib.sha1(src.read_bytes()).digest():
            return                                  # already our stub
        # An existing prefix cryptbase may be a non-functional placeholder —
        # replace it, keeping a one-time backup.
        bak = sys32 / "cryptbase.dll.bol-orig"
        if dst.exists() and not bak.exists():
            shutil.copy2(dst, bak)
        shutil.copy2(src, dst)
        ok("cryptbase RNG stub installed in prefix system32")
    except Exception as e:
        warn(f"cryptbase install failed: {e}")


def _patch_lhc_xcurl_gate(game_dir: Path):
    """Force libHttpClient.GDK onto the XCurl HTTP provider. Its console
    check (`add eax,-2 ; cmp eax,6 ; ja <winhttp>`) only takes the XCurl path
    for console enums 2..8 — under Wine it falls to WinHTTP → secur32 → the
    Azure wall. NOP the 6-byte `ja` so XCurl is always used. Idempotent."""
    dll = game_dir / "libHttpClient.GDK.dll"
    if not dll.exists():
        return
    data = dll.read_bytes()
    # add eax,-2 ; mov edx,4 ; lea rcx,[rip+imm32] ; cmp eax,6 ; ja rel32
    m = re.search(rb"\x83\xc0\xfe\xba\x04\x00\x00\x00\x48\x8d\x0d.{4}\x83\xf8\x06"
                  rb"\x0f\x87.{4}", data, re.S)
    if not m:
        warn("libHttpClient provider gate not found — XCurl routing patch "
             "skipped (native login may fall back to WinHTTP).")
        return
    ja_off = m.start() + 18          # past add(3)+mov(5)+lea(7)+cmp(3)
    expect = data[ja_off:ja_off + 6]
    if expect[:2] != b"\x0f\x87":
        warn("libHttpClient gate anchor misaligned — XCurl patch skipped.")
        return
    apply_patch(dll, ja_off, expect, b"\x90" * 6,
                "libHttpClient → force XCurl provider", strict=False)


def bump_stack_reserve(exe: Path, target=0x1000000):
    """Enlarge Minecraft.Windows.exe's PE stack reserve so the game stops
    crashing on the settings/pause screens (issue #27).

    The crash is a stack overflow: proton.log ends with
        seh:call_seh_handlers invalid frame 00007FFFFE0FF3D0 (…FE102000-…FE200000)
        seh:NtRaiseException Exception frame is not in stack limits
    i.e. the establisher frame sits *below* the thread's stack limit. The exe
    ships SizeOfStackReserve = 0x100000 (1 MB) and the faulting frame overran
    that by only ~11 KB — a marginal overflow reached by a deep-but-bounded
    call chain the settings/pause UI walks (OreUI teardown / GDK cleanup). The
    Wine loader sizes the initial thread's stack (and every worker thread that
    passes dwStackSize=0) from this header field, so raising it to 16 MB gives
    the chain the headroom it needs. Address space is the only cost on x64, so
    this can't regress a working setup.

    In-place 8-byte header edit (no 292 MB rewrite), idempotent, and re-applied
    every launch so it survives a game reinstall/update or a version switch."""
    try:
        with open(exe, "r+b") as f:
            head = f.read(0x400)
            if head[:2] != b"MZ":
                return
            e = struct.unpack_from("<I", head, 0x3C)[0]
            if head[e:e + 4] != b"PE\0\0":
                return
            opt = e + 4 + 20                       # PE sig + COFF file header
            if struct.unpack_from("<H", head, opt)[0] != 0x20B:
                return                             # not PE32+ — leave it alone
            field = opt + 72                       # SizeOfStackReserve (PE32+)
            cur = struct.unpack_from("<Q", head, field)[0]
            if cur >= target:
                return                             # already roomy (idempotent)
            f.seek(field)
            f.write(struct.pack("<Q", target))
        ok(f"Stack reserve raised {cur // 1024} KB → {target // 1024} KB "
           "(settings/pause crash fix)")
    except OSError as e:                           # never block a launch over this
        warn(f"Could not raise the stack reserve: {e}")


def hide_signin_button(game_dir):
    """Hide the broken in-game title-screen 'Sign in' button (cosmetic).

    Under Wine there is no real Xbox Live session, so the dock 'Sign in'
    button on the start screen is dead weight. Hiding it is awkward because
    Bedrock renders the menu from a *compiled* UI binary
    (resource_packs/vanilla/__brarchive/ui.brarchive) that shadows the loose
    ui/*.json — so editing start_screen.json alone has no visible effect
    (loc strings are read loose, but UI structure comes from the archive).
    Fix: move the compiled UI aside so the loose JSON-UI loads, then flip the
    sign-in button's visibility binding (#sign_in_visible) to an always-false
    one (#edu_demo_only_ui_visible, false outside Education Edition). Idempotent
    and never fatal — a cosmetic best-effort."""
    import re
    try:
        vanilla = Path(game_dir) / "data" / "resource_packs" / "vanilla"
        bra = vanilla / "__brarchive" / "ui.brarchive"
        if bra.exists():
            bra.rename(bra.parent / "ui.brarchive.bol-bak")
        ss = vanilla / "ui" / "start_screen.json"
        if not ss.exists():
            return
        txt = ss.read_text(encoding="utf-8", errors="ignore")
        new, n = re.subn(
            r'("xbl_signin_button@start\.xbl_signin_button"\s*:\s*\{\}\s*\}\s*\]'
            r'\s*,\s*"bindings"\s*:\s*\[\s*\{\s*"binding_name"\s*:\s*)'
            r'"#sign_in_visible"',
            r'\g<1>"#edu_demo_only_ui_visible"', txt, count=1)
        if n:
            ss.write_text(new, encoding="utf-8")
            ok("Hid the broken in-game Sign-in button.")
    except Exception as e:                       # noqa: BLE001 — cosmetic, never fatal
        warn(f"hide_signin_button: {e}")
