"""Regression tests for the managed WineGDK online-access setting."""
# SPDX-License-Identifier: MIT

import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bol import auth




class OnlinePreauthPayloadTests(unittest.TestCase):
    @staticmethod
    def payload(expiry="2999-01-01T00:00:00Z"):
        return {
            "device_token": "device",
            "device_token_expiry": expiry,
            "user_token": "user",
            "user_token_expiry": expiry,
            "xbl_token": "xbl",
            "xbl_token_expiry": expiry,
            "xbl_xuid": "1234",
            "sisu_token": "playfab",
            "sisu_rp": "https://example.playfabapi.com/",
            "sisu_uhs": "42",
            "sisu_expiry": expiry,
            "mp_token": "multiplayer",
            "mp_rp": "https://multiplayer.minecraft.net/",
            "mp_uhs": "42",
            "mp_expiry": expiry,
            "realms_token": "realms",
            "realms_rp": "https://pocket.realms.minecraft.net/",
            "realms_uhs": "42",
            "realms_expiry": expiry,
            "lic_token": "license",
            "lic_rp": "https://licensing.minecraft.net/",
            "lic_uhs": "42",
            "lic_expiry": expiry,
        }

    def test_complete_future_payload_is_ready(self):
        self.assertEqual(auth._online_preauth_problems(self.payload()), [])

    def test_legacy_payload_without_xbl_privileges_remains_ready(self):
        payload = self.payload()
        self.assertNotIn("xbl_privileges", payload)
        self.assertEqual(auth._online_preauth_problems(payload), [])

    def test_xbl_privileges_are_canonical_and_deduplicated(self):
        self.assertEqual(
            auth._normalize_xbl_privileges(
                "254 185 000188 254 invalid -1 4294967296"),
            "185 188 254",
        )
        self.assertEqual(
            auth._normalize_xbl_privileges([254, "185", "0185", True]),
            "185 254",
        )

    def test_invalid_xbl_privilege_claim_is_omitted(self):
        self.assertIsNone(auth._normalize_xbl_privileges(None))
        self.assertIsNone(auth._normalize_xbl_privileges("invalid -1"))

    def test_xbl_privilege_claim_distinguishes_absent_from_empty(self):
        self.assertEqual(auth._xbl_privilege_claim({}), (False, None))
        self.assertEqual(auth._xbl_privilege_claim({"prv": ""}), (True, ""))
        self.assertEqual(
            auth._xbl_privilege_claim({"prv": "254 185 254"}),
            (True, "185 254"),
        )
        # A present but malformed claim must not become the permissive legacy
        # fallback merely because no valid IDs could be extracted.
        self.assertEqual(
            auth._xbl_privilege_claim({"prv": "invalid -1"}),
            (True, ""),
        )

    def test_modern_gamertag_claims_are_kept_as_distinct_components(self):
        self.assertEqual(
            auth._xbl_gamertag_claims({
                "mgt": "ModernPlayer",
                "mgs": "1234",
                "umg": "ModernPlayer#1234",
                "gtg": "ClassicPlayer1234",
            }),
            {
                "xbl_modern_gamertag": "ModernPlayer",
                "xbl_modern_gamertag_suffix": "1234",
                "xbl_unique_modern_gamertag": "ModernPlayer#1234",
            },
        )
        self.assertEqual(
            auth._xbl_gamertag_claims({"mgt": None, "mgs": 1234}), {})

    def test_xbox_seven_digit_expiry_is_python39_compatible(self):
        # Xbox NotAfter uses 100 ns precision. Python 3.9's fromisoformat()
        # accepts at most six fractional digits, so the launcher must normalize
        # this real service format before parsing it.
        service_expiry = "2999-01-01T00:00:00.1234567Z"
        self.assertEqual(
            auth._normalize_xbox_expiry(service_expiry),
            "2999-01-01T00:00:00.123456+00:00",
        )
        payload = self.payload(service_expiry)
        self.assertEqual(auth._online_preauth_problems(payload), [])

    def test_missing_multiplayer_token_is_rejected(self):
        payload = self.payload()
        payload["mp_token"] = None
        self.assertIn("missing mp_token",
                      auth._online_preauth_problems(payload))

    def test_missing_realms_token_is_rejected(self):
        payload = self.payload()
        payload["realms_token"] = None
        self.assertIn("missing realms_token",
                      auth._online_preauth_problems(payload))

    def test_missing_realms_routing_fields_are_rejected(self):
        for field in ("realms_rp", "realms_uhs"):
            with self.subTest(field=field):
                payload = self.payload()
                payload[field] = None
                self.assertIn(f"missing {field}",
                              auth._online_preauth_problems(payload))

    def test_expired_realms_token_is_rejected(self):
        payload = self.payload()
        payload["realms_expiry"] = "2000-01-01T00:00:00Z"
        self.assertIn("expired realms_token",
                      auth._online_preauth_problems(payload))

    def test_expired_token_is_rejected(self):
        problems = auth._online_preauth_problems(
            self.payload("2000-01-01T00:00:00Z"))
        self.assertIn("expired mp_token", problems)

    def test_partial_payload_never_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "device.json"
            path.write_text("keep-me")
            payload = self.payload()
            payload["xbl_token"] = None
            self.assertFalse(auth._store_online_preauth(path, payload))
            self.assertEqual(path.read_text(), "keep-me")

    def test_complete_payload_is_written_atomically_and_private(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "device.json"
            self.assertTrue(auth._store_online_preauth(path, self.payload()))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            stored = auth._load_online_preauth(path)
            self.assertEqual(stored["mp_token"], "multiplayer")
            self.assertEqual(stored["realms_token"], "realms")
            expected = str(int(auth._parse_xbox_expiry(
                "2999-01-01T00:00:00Z").timestamp()))
            for epoch_field in auth._WINEGDK_EXPIRY_EPOCH_FIELDS.values():
                self.assertEqual(stored[epoch_field], expected)

    def test_invalid_required_iso_expiry_is_still_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "device.json"
            payload = self.payload()
            payload["mp_expiry"] = "not-an-iso-timestamp"
            self.assertFalse(auth._store_online_preauth(path, payload))
            self.assertFalse(path.exists())

    def test_corrupt_or_unreadable_account_epoch_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "winegdk-preauth"
            cache.mkdir()
            path = cache / "device.json"
            payload = self.payload()
            self.assertTrue(auth._store_online_preauth(path, payload))
            before = path.read_bytes()

            (cache / ".account-epoch").write_text("not-a-valid-generation\n")
            self.assertIsNone(auth._account_cache_epoch(cache))
            self.assertFalse(auth._store_online_preauth(path, payload))
            with mock.patch.object(auth, "DATA", root), \
                    mock.patch.object(auth, "warn"):
                self.assertFalse(auth.xbl_preauth(""))
            self.assertEqual(path.read_bytes(), before)

            # Permission/read failures use the same fail-closed state and may
            # neither match an old payload nor authorize a new store.
            with mock.patch.object(auth, "_account_cache_epoch",
                                   return_value=None):
                self.assertFalse(auth._cached_account_matches(payload, None))
                self.assertFalse(auth._store_online_preauth(path, payload))
            self.assertEqual(path.read_bytes(), before)

    def test_missing_access_token_reuses_complete_unexpired_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "winegdk-preauth"
            cache.mkdir()
            path = cache / "device.json"
            path.write_text(json.dumps(self.payload()))
            self.assertNotIn("mp_expiry_epoch", json.loads(path.read_text()))
            with mock.patch.object(auth, "DATA", root), \
                    mock.patch.object(auth, "warn"), \
                    mock.patch.object(auth, "info"):
                self.assertTrue(auth.xbl_preauth(""))
            stored = json.loads(path.read_text())
            for epoch_field in auth._WINEGDK_EXPIRY_EPOCH_FIELDS.values():
                self.assertRegex(stored[epoch_field], r"^[0-9]+$")

    def test_missing_access_token_does_not_accept_partial_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "winegdk-preauth"
            cache.mkdir()
            partial = self.payload()
            partial["mp_token"] = None
            path = cache / "device.json"
            path.write_text(json.dumps(partial))
            before = path.read_bytes()
            with mock.patch.object(auth, "DATA", root), \
                    mock.patch.object(auth, "warn"), \
                    mock.patch.object(auth, "info"):
                self.assertFalse(auth.xbl_preauth(""))
            self.assertEqual(path.read_bytes(), before)

    def test_launch_epoch_mismatch_refuses_even_valid_cached_tokens(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "winegdk-preauth"
            cache.mkdir()
            current_epoch = "c" * 32
            (cache / ".account-epoch").write_text(current_epoch + "\n")
            payload = self.payload()
            payload["_account_epoch"] = current_epoch
            auth._store_online_preauth(cache / "device.json", payload,
                                       expected_epoch=current_epoch)

            with mock.patch.object(auth, "DATA", root), \
                    mock.patch.object(auth, "warn"):
                self.assertFalse(auth.xbl_preauth("", "d" * 32))

    def test_logout_purges_account_tokens_but_keeps_device_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            msa = root / "msa"
            cache = root / "winegdk-preauth"
            msa.mkdir()
            cache.mkdir()
            (msa / "token.json").write_text(
                json.dumps({"refresh_token": "old-account"}))
            old_payload = self.payload()
            auth._store_online_preauth(cache / "device.json", old_payload)
            key = b"device-private-key"
            device_id = "{stable-device-id}"
            (cache / "device-key.pem").write_bytes(key)
            (cache / "device-id.txt").write_text(device_id)
            (cache / ".device-crash.tmp").write_text("old account tokens")

            with mock.patch.object(auth, "DATA", root), \
                    mock.patch.object(auth, "MSA_DIR", msa):
                self.assertTrue(auth.msa_logout())

            self.assertFalse((msa / "token.json").exists())
            self.assertFalse((cache / "device.json").exists())
            self.assertFalse((cache / ".device-crash.tmp").exists())
            self.assertEqual((cache / "device-key.pem").read_bytes(), key)
            self.assertEqual((cache / "device-id.txt").read_text(), device_id)
            self.assertRegex((cache / ".account-epoch").read_text().strip(),
                             r"^[0-9a-f]{32}$")

            # Even if an old cache is restored after a crash, its legacy epoch
            # cannot be consumed by the next account.
            (cache / "device.json").write_text(json.dumps(old_payload))
            with mock.patch.object(auth, "DATA", root), \
                    mock.patch.object(auth, "warn"), \
                    mock.patch.object(auth, "info"):
                self.assertFalse(auth.xbl_preauth(""))

    def test_fallback_rechecks_cache_expiry_after_failed_post(self):
        from datetime import datetime, timezone

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "winegdk-preauth"
            cache.mkdir()
            expiry_text = "2030-01-01T00:00:00.1234567Z"
            expiry = datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()
            auth._store_online_preauth(
                cache / "device.json", self.payload(expiry_text))

            # First validation: two minutes remain. The device POST then fails
            # after the cache has crossed its 60-second safety margin. A stale
            # boolean from function entry would incorrectly return True here.
            with mock.patch.object(auth, "DATA", root), \
                    mock.patch.object(
                        auth.time, "time",
                        side_effect=[expiry - 120, expiry - 30, expiry]), \
                    mock.patch("urllib.request.urlopen",
                               side_effect=OSError("simulated timeout")), \
                    mock.patch.object(auth, "warn"), \
                    mock.patch.object(auth, "info"):
                self.assertFalse(auth.xbl_preauth("fresh-access-token"))

    def test_successful_refresh_mints_exact_realms_audience(self):
        expiry = "2999-01-01T00:00:00.1234567Z"
        epoch = "e" * 32
        requests = []

        class Response:
            def __init__(self, payload):
                self.status = 200
                self.payload = json.dumps(payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.payload

        def authorization(label, uhs="42", claims=None):
            xui = {"uhs": uhs}
            if claims:
                xui.update(claims)
            return {
                "AuthorizationToken": {
                    "Token": label,
                    "NotAfter": expiry,
                    "DisplayClaims": {"xui": [xui]},
                },
            }

        responses = iter([
            {"Token": "device", "NotAfter": expiry},
            {"Token": "user", "NotAfter": expiry},
            authorization("xbl", claims={
                "xid": "1234", "gtg": "Player", "agg": "Adult",
            }),
            authorization("playfab"),
            authorization("multiplayer"),
            authorization("realms"),
            authorization("license"),
        ])

        def urlopen(request, timeout):
            self.assertEqual(timeout, 15)
            requests.append(json.loads(request.data))
            return Response(next(responses))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "winegdk-preauth"
            cache.mkdir()
            (cache / ".account-epoch").write_text(epoch + "\n")
            with mock.patch.object(auth, "DATA", root), \
                    mock.patch("urllib.request.urlopen", side_effect=urlopen), \
                    mock.patch.object(auth, "warn"), \
                    mock.patch.object(auth, "info"), \
                    mock.patch.object(auth, "ok"):
                self.assertTrue(auth.xbl_preauth("fresh-access-token", epoch))

            self.assertEqual(
                [request["RelyingParty"] for request in requests],
                [
                    "http://auth.xboxlive.com",
                    "http://auth.xboxlive.com",
                    "http://xboxlive.com",
                    "https://b980a380.minecraft.playfabapi.com/",
                    "https://multiplayer.minecraft.net/",
                    "https://pocket.realms.minecraft.net/",
                    "http://licensing.xboxlive.com",
                ],
            )
            stored = json.loads((cache / "device.json").read_text())
            self.assertEqual(stored["realms_rp"],
                             "https://pocket.realms.minecraft.net/")
            self.assertEqual(stored["realms_token"], "realms")
            self.assertRegex(stored["realms_expiry_epoch"], r"^[0-9]+$")


class NativeAuthCancellationTests(unittest.TestCase):
    @staticmethod
    def _device_response():
        return {
            "device_code": "device-code",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://example.invalid/link",
            "interval": 1,
            "expires_in": 900,
        }

    def test_logout_during_token_post_cannot_resurrect_account(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "winegdk-preauth"
            msa = root / "msa"
            cache.mkdir()
            msa.mkdir()
            epoch = "a" * 32
            (cache / ".account-epoch").write_text(epoch + "\n")
            native = auth.NativeAuth()
            online = mock.Mock()

            def post(url, _data):
                if url == auth.MSA_CONNECT:
                    return self._device_response()
                # Sign out completes while the token POST is in flight.
                auth.msa_logout()
                return {"refresh_token": "must-not-survive"}

            with mock.patch.object(auth, "DATA", root), \
                    mock.patch.object(auth, "MSA_DIR", msa), \
                    mock.patch.object(auth, "http_post_form",
                                      side_effect=post), \
                    mock.patch.object(auth.time, "sleep"), \
                    mock.patch.object(auth, "warn"), \
                    mock.patch.object(auth, "info"):
                native._flow(None, online, epoch)

            self.assertFalse((msa / "token.json").exists())
            self.assertFalse(native.online)
            online.assert_not_called()
            self.assertNotEqual(
                (cache / ".account-epoch").read_text().strip(), epoch)

    def test_stop_during_token_post_discards_response(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "winegdk-preauth"
            msa = root / "msa"
            cache.mkdir()
            msa.mkdir()
            epoch = "b" * 32
            (cache / ".account-epoch").write_text(epoch + "\n")
            native = auth.NativeAuth()

            def post(url, _data):
                if url == auth.MSA_CONNECT:
                    return self._device_response()
                native.stop()
                return {"refresh_token": "cancelled"}

            with mock.patch.object(auth, "DATA", root), \
                    mock.patch.object(auth, "MSA_DIR", msa), \
                    mock.patch.object(auth, "http_post_form",
                                      side_effect=post), \
                    mock.patch.object(auth.time, "sleep"), \
                    mock.patch.object(auth, "info"):
                native._flow(None, None, epoch)

            self.assertFalse((msa / "token.json").exists())
            self.assertFalse(native.online)


if __name__ == "__main__":
    unittest.main()
