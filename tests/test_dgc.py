"""Tests for DGC-readiness detection (bol/dgc.py) and its launch wiring."""
# SPDX-License-Identifier: MIT

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bol import dgc, launch


def _make_card(root, name, vendor, dev_id, driver):
    device = root / name / "device"
    device.mkdir(parents=True)
    (device / "vendor").write_text(vendor + "\n", encoding="utf-8")
    (device / "device").write_text(dev_id + "\n", encoding="utf-8")
    if driver is not None:
        os.symlink("/sys/bus/pci/drivers/" + driver, device / "driver")


class IntelDgpuLegacyDriverTests(unittest.TestCase):
    def test_arc_on_i915_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_card(root, "card2", "0x8086", "0x56a0", "i915")
            self.assertEqual(
                dgc.intel_dgpus_on_legacy_driver(root), ["card2"])

    def test_battlemage_on_i915_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_card(root, "card2", "0x8086", "0xe204", "i915")
            self.assertEqual(
                dgc.intel_dgpus_on_legacy_driver(root), ["card2"])

    def test_integrated_intel_on_i915_is_not_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_card(root, "card1", "0x8086", "0xa780", "i915")
            self.assertEqual(dgc.intel_dgpus_on_legacy_driver(root), [])

    def test_arc_on_xe_is_not_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_card(root, "card2", "0x8086", "0x56a0", "xe")
            self.assertEqual(dgc.intel_dgpus_on_legacy_driver(root), [])

    def test_non_intel_is_not_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_card(root, "card0", "0x10de", "0x56a0", "i915")
            self.assertEqual(dgc.intel_dgpus_on_legacy_driver(root), [])

    def test_connectors_skipped_and_mixed_cards(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_card(root, "card1", "0x8086", "0xa780", "i915")
            _make_card(root, "card2", "0x8086", "0x56a0", "i915")
            (root / "card2-DP-3").mkdir()
            (root / "card2-HDMI-A-6").mkdir()
            self.assertEqual(
                dgc.intel_dgpus_on_legacy_driver(root), ["card2"])

    def test_card_without_driver_link_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_card(root, "card0", "0x8086", "0x56a0", None)
            self.assertEqual(dgc.intel_dgpus_on_legacy_driver(root), [])

    def test_missing_root_returns_empty(self):
        self.assertEqual(
            dgc.intel_dgpus_on_legacy_driver("/nonexistent/bol-drm"), [])


class DgcWarningMessageTests(unittest.TestCase):
    def test_message_names_card_and_xe_fix(self):
        msg = dgc.dgc_warning_message(["card2"])
        self.assertIn("card2", msg)
        self.assertIn("xe", msg)
        self.assertIn("DGC", msg)
        self.assertIn("BOL_SKIP_DGC_CHECK=1", msg)

    def test_message_is_conditional_and_warns_about_the_display(self):
        # The launcher cannot tell which GPU renders (it sets no DRI_PRIME /
        # VK_ICD), and an affected card may be a secondary GPU the game never
        # touches — so the advice must stay conditional rather than predict a
        # crash. Rebinding the GPU that drives the panel can leave the user
        # without a display, so that caveat has to travel with the advice.
        msg = dgc.dgc_warning_message(["card2"])
        self.assertIn("only matters if Minecraft renders on that GPU", msg)
        self.assertIn("without a display", msg)
        self.assertNotIn("the menu will crash", msg)


class LaunchDgcAdvisoryTests(unittest.TestCase):
    def test_warns_when_arc_on_i915(self):
        with mock.patch.object(launch, "custom_proton", return_value=False), \
                mock.patch.object(launch, "intel_dgpus_on_legacy_driver",
                                  return_value=["card2"]), \
                mock.patch.object(launch, "warn") as warn:
            launch._warn_if_dgc_unavailable(environ={})
        warn.assert_called_once()
        self.assertIn("card2", warn.call_args[0][0])

    def test_silent_when_no_affected_cards(self):
        with mock.patch.object(launch, "custom_proton", return_value=False), \
                mock.patch.object(launch, "intel_dgpus_on_legacy_driver",
                                  return_value=[]), \
                mock.patch.object(launch, "warn") as warn:
            launch._warn_if_dgc_unavailable(environ={})
        warn.assert_not_called()

    def test_silent_when_override_set(self):
        with mock.patch.object(launch, "custom_proton", return_value=False), \
                mock.patch.object(launch, "intel_dgpus_on_legacy_driver",
                                  return_value=["card2"]), \
                mock.patch.object(launch, "warn") as warn:
            launch._warn_if_dgc_unavailable(
                environ={"BOL_SKIP_DGC_CHECK": "1"})
        warn.assert_not_called()

    def test_silent_for_custom_proton(self):
        with mock.patch.object(launch, "custom_proton", return_value=True), \
                mock.patch.object(launch, "intel_dgpus_on_legacy_driver",
                                  return_value=["card2"]), \
                mock.patch.object(launch, "warn") as warn:
            launch._warn_if_dgc_unavailable(environ={})
        warn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
