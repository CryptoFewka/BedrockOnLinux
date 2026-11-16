"""bol.dgc — detect GPUs that cannot expose Vulkan DGC for the menu."""
# SPDX-License-Identifier: MIT

import os
import re
from pathlib import Path

# Intel discrete GPU PCI device IDs whose ANV Vulkan driver exposes Device
# Generated Commands (DGC) only when bound to the xe kernel driver, not the
# legacy i915 driver. This engine's vkd3d needs DGC for Minecraft's
# ExecuteIndirect menu path, so such a GPU on i915 faults in the menu.
# Maintain deliberately as new discrete GPUs ship (mirrors the deliberate
# engine hash pins in bol/vkd3d.py):
#   56a/56b/56c  DG2 / Arc Alchemist (Xe-HPG)
#   e2           Arc Battlemage (Xe2)
_INTEL_DGPU_DGC_PREFIXES = ("56a", "56b", "56c", "e2")

_INTEL_VENDOR = "0x8086"
_LEGACY_DRIVER = "i915"


def intel_dgpus_on_legacy_driver(drm_root=None):
    """Intel discrete GPUs that need DGC but are bound to legacy i915.

    ANV exposes Vulkan Device Generated Commands (DGC) only when the GPU is
    bound to the xe kernel driver; on i915 a qualifying Intel discrete GPU
    cannot expose DGC and Minecraft's menu faults. Detected via sysfs only —
    no graphics device is opened (same convention as launch._is_steam_deck).
    Returns the affected DRM card names (e.g. ['card2']); empty when none.
    """
    root = Path(drm_root) if drm_root is not None else Path("/sys/class/drm")
    affected = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return affected
    for card in entries:
        if not re.fullmatch(r"card\d+", card.name):
            continue
        device = card / "device"
        try:
            vendor = (device / "vendor").read_text(errors="ignore").strip()
            dev_id = (device / "device").read_text(errors="ignore").strip()
            driver = Path(os.readlink(device / "driver")).name
        except OSError:
            continue
        if vendor.lower() != _INTEL_VENDOR:
            continue
        if driver != _LEGACY_DRIVER:
            continue
        dev = dev_id.lower()
        if dev.startswith("0x"):
            dev = dev[2:]
        if dev.startswith(_INTEL_DGPU_DGC_PREFIXES):
            affected.append(card.name)
    return affected


def dgc_warning_message(cards):
    """Actionable guidance for Intel dGPUs that lack DGC under i915."""
    return (
        "Intel GPU %s is bound to the legacy 'i915' driver, which cannot "
        "expose the Vulkan Device Generated Commands (DGC) that Minecraft's "
        "menu needs. This only matters if Minecraft renders on that GPU: if "
        "it does and the menu crashes, binding the GPU to the 'xe' kernel "
        "driver (xe.force_probe=<device-id>, keep i915 off it, then reboot) "
        "should fix it. Check first that your display is not driven by that "
        "GPU — rebinding the GPU your screen hangs off can leave you without "
        "a display. Silence this notice with BOL_SKIP_DGC_CHECK=1."
        % ", ".join(cards))
