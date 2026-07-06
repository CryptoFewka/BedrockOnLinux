"""bol.auth — Microsoft / Xbox Live native login (MSA + pre-auth chain)."""
# SPDX-License-Identifier: MIT

import json
import os
import subprocess
import threading
import time

from .config import (
    DATA,
    LOGS,
    MSA_CLIENT_ID,
    MSA_CONNECT,
    MSA_DIR,
    MSA_SCOPE,
    MSA_TOKEN,
    WINEGDK_REG,
)
from .log import BolError, die, err, info, ok, warn
from .prefix import proton_umu_cmd
from .util import http_post_form

# ----------------------------------------------------------------------
# In-game Microsoft login (this branch: no ProxyPass at all)
# ----------------------------------------------------------------------


def msa_load():
    f = MSA_DIR / "token.json"
    if f.is_file():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return {}


def msa_save(tok):
    MSA_DIR.mkdir(parents=True, exist_ok=True)
    p = MSA_DIR / "token.json"
    p.write_text(json.dumps(tok, indent=2))
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass


def msa_signed_in():
    return bool(msa_load().get("refresh_token"))


def msa_logout():
    try:
        (MSA_DIR / "token.json").unlink(missing_ok=True)
    except Exception:
        pass


def msa_refresh(refresh_token):
    """Trade a refresh token for a fresh one (same shape WineGDK's XUser uses
    internally). Returns the token dict, or None if it was rejected."""
    t = http_post_form(MSA_TOKEN, {
        "client_id": MSA_CLIENT_ID, "scope": MSA_SCOPE,
        "grant_type": "refresh_token", "refresh_token": refresh_token})
    return t if t.get("refresh_token") else None


class NativeAuth:
    """MSA device-code login for the no-ProxyPass path. We only obtain an
    OAuth refresh token; WineGDK's XUser reads it from the prefix registry
    and performs the Xbox Live / XSTS exchange itself. GUI-compatible with
    ProxyPass (`.auth`, `.start`, `.stop`, `.running`)."""

    def __init__(self):
        self.auth = None        # (url, code)
        self.online = False
        self.proc = None        # GUI compatibility (no subprocess here)
        self.dest = None
        self._stop = False

    def running(self):
        return False

    def signed_in(self):
        return msa_signed_in()

    def start(self, on_auth=None, on_online=None, dest=None):
        if msa_signed_in():
            self.online = True
            if on_online:
                on_online()
            ok("Microsoft account already linked (in-game login)")
            return
        self._stop = False
        threading.Thread(target=self._flow, args=(on_auth, on_online),
                          daemon=True).start()

    def _flow(self, on_auth, on_online):
        try:
            d = http_post_form(MSA_CONNECT, {
                "client_id": MSA_CLIENT_ID, "scope": MSA_SCOPE,
                "response_type": "device_code"})
            if "device_code" not in d:
                die("Microsoft device-code request failed: "
                    f"{d.get('error_description') or d.get('error') or d}")
            url = d.get("verification_uri") or "https://www.microsoft.com/link"
            code = d.get("user_code")
            self.auth = (url, code)
            if on_auth:
                on_auth(url, code)
            info(f"Microsoft sign-in → {url} code {code}")
            interval = max(int(d.get("interval", 5) or 5), 1)
            deadline = time.time() + int(d.get("expires_in", 900) or 900)
            dc = d["device_code"]
            while not self._stop and time.time() < deadline:
                time.sleep(interval)
                # Legacy live.com grant string — matches WineGDK XUser.c.
                t = http_post_form(MSA_TOKEN, {
                    "client_id": MSA_CLIENT_ID,
                    "grant_type": "device_code", "device_code": dc})
                e = t.get("error")
                if e == "authorization_pending":
                    continue
                if e == "slow_down":
                    interval += 5
                    continue
                if e:
                    die(f"Microsoft sign-in failed: "
                        f"{t.get('error_description') or e}")
                if t.get("refresh_token"):
                    msa_save({"refresh_token": t["refresh_token"],
                              "obtained": int(time.time())})
                    self.auth = None
                    self.online = True
                    if on_online:
                        on_online()
                    ok("Microsoft account linked (in-game login)")
                    return
            if not self._stop:
                warn("Microsoft sign-in timed out — click 'Sign in' again.")
        except BolError:
            pass
        except Exception as ex:
            err(f"Native login error: {ex}")

    def stop(self):
        self._stop = True


class _HttpResp:
    """Minimal requests-style response built on urllib, so xbl_preauth can drop
    the third-party `requests` dependency — only cryptography remains."""

    def __init__(self, status_code, raw):
        self.status_code = status_code
        self._raw = raw
        self.text = raw.decode("utf-8", "replace")

    def json(self):
        return json.loads(self._raw)


def xbl_preauth(msa_access_token):
    """Run the whole Xbox Live auth chain (device + user + SISU tokens) from
    the host's OpenSSL stack and persist it as winegdk-preauth/device.json,
    where xgameruntime.dll short-circuits its own HTTP calls.

    Needed because Azure TCP-RSTs every *.auth.xboxlive.com / sisu call made
    through Wine's GnuTLS (fingerprinted as non-Schannel) — the same requests
    from the host succeed. Returns True if at least the device token landed;
    False lets the caller fall back to the Wine-side path."""
    import base64, uuid as _uuid
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import hashes, serialization
    except ImportError as e:
        warn(f"xbl_preauth: missing Python dep ({e}) — skipping")
        return False
    cache = DATA / "winegdk-preauth"
    cache.mkdir(parents=True, exist_ok=True)
    key_path = cache / "device-key.pem"
    out_path = cache / "device.json"
    # Reuse persisted EC P-256 key + UUID across launches so Xbox Live sees
    # the same device on every session.
    if key_path.exists() and (cache / "device-id.txt").exists():
        try:
            with open(key_path, "rb") as f:
                priv = serialization.load_pem_private_key(f.read(), password=None)
            device_id = (cache / "device-id.txt").read_text().strip()
        except Exception:
            priv = None; device_id = None
    else:
        priv = None; device_id = None
    if priv is None:
        priv = ec.generate_private_key(ec.SECP256R1())
        device_id = "{" + str(_uuid.uuid4()) + "}"
        with open(key_path, "wb") as f:
            f.write(priv.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
        (cache / "device-id.txt").write_text(device_id)
    pub_numbers = priv.public_key().public_numbers()
    x_b64 = base64.b64encode(pub_numbers.x.to_bytes(32, "big")).decode()
    y_b64 = base64.b64encode(pub_numbers.y.to_bytes(32, "big")).decode()
    proof_key = {"alg": "ES256", "crv": "P-256", "kty": "EC",
                 "use": "sig", "x": x_b64, "y": y_b64}
    # Build the Xbox Live signature blob — wire format is ver(4) + ts(8) +
    # raw ECDSA-P-256 r||s (64), 76 bytes total. The bytes SIGNED are a
    # hash input that puts 0x00 separators between every field:
    #   ver(4) || \0 || ts(8) || \0 || method || \0 || path || \0 || auth || \0 || body || \0
    # SHA-256 of this is what gets signed (matches Wine-side
    # DeviceAuth_SignRequest in dlls/xgameruntime/.../DeviceAuth.c).
    import time as _time
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    def _sign_header(method, path, body_bytes):
        now_ft = int((_time.time() + 11644473600) * 1e7)
        ver = (1).to_bytes(4, "big")
        ts = now_ft.to_bytes(8, "big")
        hash_input = (ver + b"\0" + ts + b"\0"
                      + method.encode() + b"\0"
                      + path.encode() + b"\0"
                      + b"" + b"\0"
                      + body_bytes + b"\0")
        sig_der = priv.sign(hash_input, ec.ECDSA(hashes.SHA256()))
        r2, s2 = decode_dss_signature(sig_der)
        sig_raw = r2.to_bytes(32, "big") + s2.to_bytes(32, "big")
        return base64.b64encode(ver + ts + sig_raw).decode()
    def _xbl_post(url, body_dict):
        import urllib.error
        import urllib.request
        from urllib.parse import urlparse
        body_bytes = json.dumps(body_dict, separators=(",", ":")).encode()
        path = urlparse(url).path
        req = urllib.request.Request(url, data=body_bytes, method="POST",
            headers={
                "User-Agent": "XAL Xbox Live Game (Windows; SDK; 1.0.0.0)",
                "Content-Type": "application/json",
                "x-xbl-contract-version": "1",
                "Signature": _sign_header("POST", path, body_bytes),
            })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return _HttpResp(resp.status, resp.read())
        except urllib.error.HTTPError as e:
            return _HttpResp(e.code, e.read())

    # ---- 1. /device/authenticate ----
    try:
        r = _xbl_post("https://device.auth.xboxlive.com/device/authenticate", {
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT",
            "Properties": {
                "AuthMethod": "ProofOfPossession",
                "Id": device_id,
                "DeviceType": "Win32",
                "Version": "10.0.22631",
                "ProofKey": proof_key,
            },
        })
    except Exception as e:
        warn(f"xbl_preauth: device.auth POST failed: {e}")
        return False
    if r.status_code != 200:
        warn(f"xbl_preauth: device.auth HTTP {r.status_code} — {r.text[:200]}")
        return False
    j = r.json()
    device_token = j["Token"]

    # ---- 2. /user/authenticate (AuthMethod=RPS, RpsTicket="t=<token>") ----
    user_token = None
    user_token_expiry = None
    if msa_access_token:
        try:
            ru = _xbl_post("https://user.auth.xboxlive.com/user/authenticate", {
                "RelyingParty": "http://auth.xboxlive.com",
                "TokenType": "JWT",
                "Properties": {
                    "AuthMethod": "RPS",
                    "SiteName": "user.auth.xboxlive.com",
                    "RpsTicket": "t=" + msa_access_token,
                },
            })
            if ru.status_code == 200:
                uj = ru.json()
                user_token = uj["Token"]
                user_token_expiry = uj.get("NotAfter", "")
            else:
                warn(f"xbl_preauth: user.auth HTTP {ru.status_code} — {ru.text[:200]}")
        except Exception as e:
            warn(f"xbl_preauth: user.auth POST failed: {e}")

    # ---- 3a. sisu /authorize for http://xboxlive.com ----
    # Returns the DisplayClaims (xid, gtg, agg, …) LoadDefaultUser needs to
    # populate the XUser handle (its own xsts.auth call would RST under Wine).
    def _sisu(rp):
        if not msa_access_token: return None
        try:
            r = _xbl_post("https://sisu.xboxlive.com/authorize", {
                "AccessToken": "t=" + msa_access_token,
                "AppId": "0000000048183522",
                "deviceToken": device_token,
                "Sandbox": "RETAIL",
                "UseModernGamertag": True,
                "SiteName": "user.auth.xboxlive.com",
                "RelyingParty": rp,
                "OfferTermsAcceptance": True,
                "AcceptOffers": True,
                "ProofKey": proof_key,
            })
            if r.status_code != 200:
                warn(f"xbl_preauth: sisu({rp}) HTTP {r.status_code} — {r.text[:200]}")
                return None
            return r.json()
        except Exception as e:
            warn(f"xbl_preauth: sisu({rp}) failed: {e}")
            return None

    xbl_sisu = _sisu("http://xboxlive.com") or {}
    xbl_auth = xbl_sisu.get("AuthorizationToken", {}) if xbl_sisu else {}
    xbl_token = xbl_auth.get("Token")
    xbl_expiry = xbl_auth.get("NotAfter", "") if xbl_auth else ""
    xbl_claims = {}
    try:
        xbl_claims = xbl_auth["DisplayClaims"]["xui"][0]
    except (KeyError, IndexError, TypeError):
        pass

    # ---- 3b. sisu /authorize for the PlayFab RP MC pins ----
    pf_sisu = _sisu("https://b980a380.minecraft.playfabapi.com/") or {}
    pf_auth = pf_sisu.get("AuthorizationToken", {}) if pf_sisu else {}
    sisu_rp = "https://b980a380.minecraft.playfabapi.com/" if pf_auth.get("Token") else None
    sisu_token = pf_auth.get("Token")
    sisu_expiry = pf_auth.get("NotAfter", "")
    sisu_uhs = None
    try:
        sisu_uhs = pf_auth["DisplayClaims"]["xui"][0].get("uhs")
    except (KeyError, IndexError, TypeError):
        pass

    # ---- 3c. sisu /authorize for the multiplayer RP, used when joining a
    # third-party server — without a pre-minted token the live SISU call RSTs
    # and the join fails (pings still work).
    mp_sisu = _sisu("https://multiplayer.minecraft.net/") or {}
    mp_auth = mp_sisu.get("AuthorizationToken", {}) if mp_sisu else {}
    mp_rp = "https://multiplayer.minecraft.net/" if mp_auth.get("Token") else None
    mp_token = mp_auth.get("Token")
    mp_expiry = mp_auth.get("NotAfter", "")
    mp_uhs = None
    try:
        mp_uhs = mp_auth["DisplayClaims"]["xui"][0].get("uhs")
    except (KeyError, IndexError, TypeError):
        pass

    # ---- 3d. sisu /authorize for the licensing RP, used by the in-game
    # Marketplace — its catalog/entitlement edges (collections/purchase.
    # mp.microsoft.com, inventory/licensing.xboxlive.com) only accept an XSTS
    # token minted for http://licensing.xboxlive.com. Pre-mint it here so the
    # store catalog loads instead of hanging on a live SISU call (which RSTs
    # under Wine GnuTLS).
    lic_sisu = _sisu("http://licensing.xboxlive.com") or {}
    lic_auth = lic_sisu.get("AuthorizationToken", {}) if lic_sisu else {}
    lic_rp = "http://licensing.xboxlive.com" if lic_auth.get("Token") else None
    lic_token = lic_auth.get("Token")
    lic_expiry = lic_auth.get("NotAfter", "")
    lic_uhs = None
    try:
        lic_uhs = lic_auth["DisplayClaims"]["xui"][0].get("uhs")
    except (KeyError, IndexError, TypeError):
        pass

    # Export the EC P-256 key as BCRYPT_ECCPRIVATE_BLOB so xgameruntime.dll
    # can BCryptImportKeyPair() it byte-for-byte. Layout (104 bytes):
    #   dwMagic (LE u32 = BCRYPT_ECDSA_PRIVATE_P256_MAGIC 0x32534345)
    #   cbKey   (LE u32 = 32)
    #   X       (32 big-endian)
    #   Y       (32 big-endian)
    #   d       (32 big-endian, the private scalar)
    priv_d = priv.private_numbers().private_value
    ecc_blob = (
        (0x32534345).to_bytes(4, "little") + (32).to_bytes(4, "little")
        + pub_numbers.x.to_bytes(32, "big")
        + pub_numbers.y.to_bytes(32, "big")
        + priv_d.to_bytes(32, "big")
    )
    out = {
        "device_id": device_id,
        "ecc_private_blob_b64": base64.b64encode(ecc_blob).decode(),
        "device_token": device_token,
        "device_token_expiry": j.get("NotAfter", ""),
        "user_token": user_token,
        "user_token_expiry": user_token_expiry,
        # SISU for http://xboxlive.com — used as the bootstrap XSTS token MC
        # extracts xuid/gamertag/agegroup from in LoadDefaultUser.
        "xbl_token": xbl_token,
        "xbl_token_expiry": xbl_expiry,
        "xbl_xuid": xbl_claims.get("xid"),
        "xbl_gamertag": xbl_claims.get("gtg"),
        "xbl_age_group": xbl_claims.get("agg"),
        "xbl_uhs": xbl_claims.get("uhs"),
        # SISU for the PlayFab RP — cached and used by
        # XUserGetTokenAndSignatureProvider so it never re-hits sisu.xboxlive.
        "sisu_rp": sisu_rp,
        "sisu_token": sisu_token,
        "sisu_uhs": sisu_uhs,
        "sisu_expiry": sisu_expiry,
        # SISU for the multiplayer RP — same idea, used when joining a
        # third-party/external server (https://multiplayer.minecraft.net/).
        "mp_rp": mp_rp,
        "mp_token": mp_token,
        "mp_uhs": mp_uhs,
        "mp_expiry": mp_expiry,
        # SISU for the licensing RP — used by the in-game Marketplace
        # (http://licensing.xboxlive.com).
        "lic_rp": lic_rp,
        "lic_token": lic_token,
        "lic_uhs": lic_uhs,
        "lic_expiry": lic_expiry,
        "obtained": int(_time.time()),
    }
    # Atomic write: two launches (or a launch racing a stale one) both ran
    # xbl_preauth and a plain write_text let their output interleave, leaving a
    # corrupted xbl_xuid in device.json — which the game then loads and faults
    # on. Write to a temp file in the same dir and rename, so a reader only ever
    # sees a complete file and the last writer wins cleanly.
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=str(out_path.parent), prefix=".device-",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(out, indent=2))
        os.replace(tmp, out_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    bits = ["device"]
    if user_token: bits.append("user")
    if xbl_token: bits.append(f"XBL(xuid={xbl_claims.get('xid')},gtg={xbl_claims.get('gtg')})")
    if sisu_token: bits.append(f"SISU-pf(uhs={sisu_uhs})")
    if mp_token: bits.append(f"SISU-mp(uhs={mp_uhs})")
    if lic_token: bits.append(f"SISU-lic(uhs={lic_uhs})")
    ok(f"Xbox Live pre-auth: {', '.join(bits)}")
    return True


def wine_reg_set_refresh_token(token):
    """Seed the MSA refresh token where WineGDK's XUser reads it
    (HKLM\\Software\\Wine\\WineGDK 'RefreshToken'), written through the same
    proton/umu runtime (and prefix) used to launch the game."""
    cmd, env = proton_umu_cmd("reg")
    cmd += ["add", "HKLM\\" + WINEGDK_REG, "/v", "RefreshToken",
            "/t", "REG_SZ", "/d", token, "/f"]
    LOGS.mkdir(parents=True, exist_ok=True)
    log = open(LOGS / "native-login.log", "w")
    try:
        rc = subprocess.run(cmd, env=env, stdout=log,
                            stderr=subprocess.STDOUT, timeout=120).returncode
    except Exception as e:
        warn(f"Could not write WineGDK RefreshToken: {e}")
        return False
    if rc == 0:
        ok("In-game login token written to the Wine prefix")
        return True
    warn(f"reg add returned {rc} — see logs/native-login.log")
    return False


def wine_apply_winegdk_prereqs():
    """Registry prereqs: ConsoleMode=8 (console enum → the XSAPI code path;
    1 = Win32 PC would block the Servers tab as a 'dev build'), TLS 1.2
    forced, and the WindowsAppRuntime UI-mute env vars in HKCU\\Environment
    (pressure-vessel filters MICROSOFT_* out of the host env)."""
    LOGS.mkdir(parents=True, exist_ok=True)
    log = open(LOGS / "native-login.log", "a")
    def _regadd(*args):
        cmd, env = proton_umu_cmd("reg")
        cmd += ["add", *args, "/f"]
        try:
            subprocess.run(cmd, env=env, stdout=log,
                           stderr=subprocess.STDOUT, timeout=120)
        except Exception as e:
            warn(f"reg add {args[0]} failed: {e}")
    def _regdel(key, value):
        cmd, env = proton_umu_cmd("reg")
        cmd += ["delete", key, "/v", value, "/f"]
        try:
            subprocess.run(cmd, env=env, stdout=log,
                           stderr=subprocess.STDOUT, timeout=120)
        except Exception as e:
            warn(f"reg delete {key} failed: {e}")
    _regadd(r"HKLM\Software\Microsoft\Windows NT\CurrentVersion\OEM",
            "/v", "ConsoleMode", "/t", "REG_DWORD", "/d", "8")
    # Force the in-game "signed in with Microsoft" facet (unlocks the Servers
    # tab). The engine reads this and applies the patch only when 1 (default);
    # users whose game crashes on launch (the XSAPI account object never
    # populates under Wine — issue #17/#18) set it 0 to fall back to the
    # pre-patch behaviour (Servers greyed, but the game runs).
    from .util import load_settings
    _regadd(r"HKLM\Software\Wine\WineGDK", "/v", "ForceMsaFacet",
            "/t", "REG_DWORD", "/d",
            "1" if load_settings().get("force_msa_facet", True) else "0")
    # Azure rejects Wine GnuTLS' TLS 1.3 handshake (7-byte fatal Alert →
    # 0x80090304); forcing TLS 1.2 via DefaultSecureProtocols lets the
    # SISU/XSTS and PlayFab POSTs through.
    _regadd(r"HKLM\Software\Microsoft\Windows\CurrentVersion\Internet Settings\WinHttp",
            "/v", "DefaultSecureProtocols", "/t", "REG_DWORD", "/d", "2560")
    _regadd(r"HKLM\Software\Microsoft\SchannelTLS\Protocols\TLS 1.3\Client",
            "/v", "DisabledByDefault", "/t", "REG_DWORD", "/d", "1")
    for name, val in (
        ("MICROSOFT_WINDOWSAPPRUNTIME_BOOTSTRAP_INITIALIZE_SHOWUI", "0"),
        ("MICROSOFT_WINDOWSAPPRUNTIME_BOOTSTRAP_INITIALIZE_FAILFAST", "0"),
        ("MICROSOFT_WINDOWSAPPRUNTIME_DEPLOYMENT_INITIALIZE_ONERRORSHOWUI",
         "0"),
    ):
        _regadd(r"HKCU\Environment", "/v", name, "/t", "REG_SZ", "/d", val)
    # Issue #26: in windowed mode the mouse cursor escapes the game window when
    # the player looks around. Minecraft confines the cursor with ClipCursor
    # during mouse-look, but under Wine that grab is unreliable for an
    # individually-managed top-level window (the compositor/focus handshake can
    # drop it), so the OS pointer drifts out — only visible when the game isn't
    # fullscreen. Running inside a Wine virtual desktop gives Wine a single
    # owning X window it fully controls, which makes ClipCursor confine to the
    # game window reliably. Opt-in (setting `confine_cursor` / env
    # BOL_CONFINE_CURSOR=1) because it changes windowing for the whole prefix.
    # A persisted marker keeps the common (off) path from paying two extra
    # `wine reg` calls every launch, while still reverting cleanly on toggle-off.
    from .util import _screen_wh, save_settings
    confine = (os.environ.get("BOL_CONFINE_CURSOR", "").lower()
               in ("1", "yes", "on", "true")
               or load_settings().get("confine_cursor", False))
    applied = load_settings().get("_confine_applied", False)
    if confine:
        # Re-ensure every launch (not just on the enable edge) so the keys
        # survive a prefix reset, which recreates the prefix and would wipe
        # them. Only opt-in users reach this path, so the extra reg calls are
        # never paid by the default-off majority.
        wh = _screen_wh() or ("1920", "1080")
        _regadd(r"HKCU\Software\Wine\Explorer", "/v", "Desktop",
                "/t", "REG_SZ", "/d", "Default")
        _regadd(r"HKCU\Software\Wine\Explorer\Desktops", "/v", "Default",
                "/t", "REG_SZ", "/d", f"{wh[0]}x{wh[1]}")
        if not applied:
            s2 = load_settings(); s2["_confine_applied"] = True; save_settings(s2)
        ok(f"Cursor confinement ON (virtual desktop {wh[0]}x{wh[1]}).")
    elif applied:
        _regdel(r"HKCU\Software\Wine\Explorer", "Desktop")
        _regdel(r"HKCU\Software\Wine\Explorer\Desktops", "Default")
        s2 = load_settings(); s2["_confine_applied"] = False; save_settings(s2)
        ok("Cursor confinement OFF (virtual desktop removed).")
    ok("WineGDK prereqs applied (ConsoleMode=8, TLS 1.2 forced, UI muted)")
