"""Cross-profile regressions for the persistent GPU safety interlock."""
# SPDX-License-Identifier: MIT

import json
import os
import subprocess
import sys
from pathlib import Path

from bol.profiles import create_profile


ROOT = Path(__file__).resolve().parents[1]


def _run_in_profile(profile, source):
    env = dict(os.environ)
    env.update({
        "BOL_HOME": str(profile),
        "PYTHONPATH": str(ROOT),
    })
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def test_profile_b_observes_profile_a_interrupted_gpu_launch(tmp_path):
    base = tmp_path / "shared data"
    profile_a = create_profile("Player A", base)
    profile_b = create_profile("Player B", base)

    armed = _run_in_profile(profile_a, """
import json
from bol import gpu_safety
gpu_safety._boot_id = lambda: "boot-a"
gpu_safety.arm_gpu_launch()
print(json.dumps({
    "marker": str(gpu_safety.GPU_LAUNCH_MARKER),
    "ack": str(gpu_safety.GPU_SAFETY_ACK),
}))
""")
    expected_marker = base / ".gpu-launch-in-progress.json"
    expected_ack = base / ".gpu-safety-ack.json"
    assert json.loads(armed.stdout) == {
        "marker": str(expected_marker),
        "ack": str(expected_ack),
    }
    assert expected_marker.is_file()

    observed = _run_in_profile(profile_b, """
import json
from bol import gpu_safety
from bol.log import BolError
gpu_safety._boot_id = lambda: "boot-b"
problem = gpu_safety.interrupted_launch_problem()
try:
    gpu_safety.arm_gpu_launch()
except BolError:
    arm_blocked = True
else:
    arm_blocked = False
print(json.dumps({
    "problem": problem,
    "arm_blocked": arm_blocked,
    "marker": str(gpu_safety.GPU_LAUNCH_MARKER),
}))
""")
    result = json.loads(observed.stdout)
    assert "did not return cleanly" in result["problem"]
    assert result["arm_blocked"] is True
    assert result["marker"] == str(expected_marker)


def test_profile_b_acknowledgement_is_visible_from_profile_a(tmp_path):
    base = tmp_path / "relocated shared data"
    profile_a = create_profile("Player A", base)
    profile_b = create_profile("Player B", base)

    _run_in_profile(profile_a, """
from bol import gpu_safety
gpu_safety._boot_id = lambda: "boot-before-reboot"
gpu_safety.arm_gpu_launch()
""")
    acknowledged = _run_in_profile(profile_b, """
import json
from types import SimpleNamespace
from bol import gpu_safety
gpu_safety._boot_id = lambda: "boot-now"
clean_journal = lambda *_args, **_kwargs: SimpleNamespace(
    stdout="", stderr="", returncode=0)
status = gpu_safety.acknowledge_gpu_safety_incident(
    journal_runner=clean_journal)
print(json.dumps({
    "can_acknowledge": status.can_acknowledge,
    "marker_present": status.marker_present,
    "ack": str(gpu_safety.GPU_SAFETY_ACK),
}))
""")
    result = json.loads(acknowledged.stdout)
    expected_ack = base / ".gpu-safety-ack.json"
    assert result == {
        "can_acknowledge": True,
        "marker_present": True,
        "ack": str(expected_ack),
    }

    visible = _run_in_profile(profile_a, """
import json
from bol import gpu_safety
print(gpu_safety.GPU_SAFETY_ACK.read_text(encoding="utf-8"))
""")
    payload = json.loads(visible.stdout)
    assert payload["boot_id"] == "boot-now"
    assert payload["marker"] is True
    assert not (base / ".gpu-launch-in-progress.json").exists()
