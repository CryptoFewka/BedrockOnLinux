"""Regression tests for actionable, credential-free Xbox auth failures."""
# SPDX-License-Identifier: MIT

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bol import auth


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._payload


class PreauthFailureDiagnosticTests(unittest.TestCase):
    access_secret = "msa-access-token-must-not-leak"
    response_secret = "xbox-response-token-must-not-leak"

    def run_failure(self, urlopen_effect):
        warnings = []
        urlopen = mock.Mock()
        if isinstance(urlopen_effect, BaseException):
            urlopen.side_effect = urlopen_effect
        elif isinstance(urlopen_effect, (list, tuple)):
            urlopen.side_effect = urlopen_effect
        else:
            urlopen.return_value = urlopen_effect
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(auth, "DATA", root), \
                    mock.patch("urllib.request.urlopen", urlopen), \
                    mock.patch.object(auth, "warn",
                                      side_effect=lambda text:
                                      warnings.append(str(text))), \
                    mock.patch.object(auth, "info"), \
                    mock.patch.object(auth, "ok"):
                self.assertFalse(auth.xbl_preauth(self.access_secret))
        diagnostic = auth.xbl_preauth_diagnostic()
        self.assertIsNotNone(diagnostic)
        visible = json.dumps({
            "diagnostic": diagnostic,
            "message": auth.xbl_preauth_error_message(),
            "warnings": warnings,
        })
        self.assertNotIn(self.access_secret, visible)
        self.assertNotIn(self.response_secret, visible)
        return diagnostic

    def test_network_exception_is_sanitized_and_actionable(self):
        diagnostic = self.run_failure(
            OSError("timeout while sending " + self.response_secret))

        self.assertEqual(diagnostic["stage"], "device-auth")
        self.assertEqual(diagnostic["category"], "network")
        self.assertNotIn("http_status", diagnostic)
        self.assertIn("DNS", auth.xbl_preauth_error_message())

    def test_missing_xbox_profile_is_an_account_failure(self):
        expiry = "2999-01-01T00:00:00Z"
        failure = {
            "XErr": 2148916233,
            "Token": self.response_secret,
            "Message": "private " + self.access_secret,
        }
        diagnostic = self.run_failure([
            _Response(200, {"Token": "device", "NotAfter": expiry}),
            *[_Response(401, failure) for _ in range(6)],
        ])

        self.assertEqual(diagnostic, {
            "stage": "user-auth",
            "category": "account",
            "message": auth._XBL_PREAUTH_MESSAGES["account"],
            "http_status": 401,
            "error_code": 2148916233,
        })
        self.assertIn("Xbox profile", auth.xbl_preauth_error_message())

    def test_child_account_error_gets_age_family_guidance(self):
        expiry = "2999-01-01T00:00:00Z"
        failure = {
            "XErr": "2148916238",
            "Token": self.response_secret,
        }
        diagnostic = self.run_failure([
            _Response(200, {"Token": "device", "NotAfter": expiry}),
            _Response(200, {"Token": "user", "NotAfter": expiry}),
            *[_Response(403, failure) for _ in range(6)],
        ])

        self.assertEqual(diagnostic["stage"], "xsts-achievements")
        self.assertEqual(diagnostic["category"], "age")
        self.assertEqual(diagnostic["http_status"], 403)
        self.assertEqual(diagnostic["error_code"], 2148916238)
        self.assertIn("family", auth.xbl_preauth_error_message())

    def test_accessor_returns_a_copy(self):
        self.run_failure(OSError("offline"))
        diagnostic = auth.xbl_preauth_diagnostic()
        diagnostic["message"] = self.response_secret

        self.assertNotEqual(
            auth.xbl_preauth_diagnostic()["message"], self.response_secret)


if __name__ == "__main__":
    unittest.main()
