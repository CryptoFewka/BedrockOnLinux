"""External client DLL injection regressions."""
# SPDX-License-Identifier: MIT

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from bol import inject
from bol.log import BolError


ROOT = Path(__file__).resolve().parents[1]


def test_native_injector_does_not_free_a_live_loadlibrary_buffer():
    source = (ROOT / "src" / "injector.c").read_text(encoding="utf-8")
    assert "DWORD wait = WaitForSingleObject(th, 15000);" in source
    assert "if (wait != WAIT_OBJECT_0)" in source
    timeout_branch = source.split(
        "if (wait != WAIT_OBJECT_0)", 1
    )[1].split("DWORD mod", 1)[0]
    assert "VirtualFreeEx" not in timeout_branch
    assert "return 8;" in timeout_branch
    write_failure = source.split(
        "if (!WriteProcessMemory", 1
    )[1].split("HANDLE th", 1)[0]
    assert "VirtualFreeEx" in write_failure
    bundled = (ROOT / "bol" / "injector.exe").read_bytes()
    assert "ERR allocate process memory".encode("utf-16le") in bundled


@pytest.mark.parametrize(
    "cmdline",
    [
        b"/engine/files/bin/wine64-preloader\0winedbg\0--auto\0",
        (
            b"/engine/files/bin/wine\0"
            b"C:\\windows\\system32\\winedbg.exe\0--auto\0"
        ),
    ],
)
def test_winedbg_scanner_matches_exact_argv_entry(cmdline):
    assert inject._cmdline_has_winedbg(cmdline)


@pytest.mark.parametrize(
    "cmdline",
    [
        b"/mnt/winedbg-archives/Minecraft.Windows.exe\0",
        b"/games/winedbg.exe.backup\0",
        b"/engine/files/bin/wine\0--log=winedbg\0",
    ],
)
def test_winedbg_scanner_ignores_unrelated_substrings(cmdline):
    assert not inject._cmdline_has_winedbg(cmdline)


def test_cached_injector_same_size_but_old_content_is_replaced_atomically(
        tmp_path):
    cached = tmp_path / "injector.exe"
    cached.write_bytes(b"OLD!")
    with mock.patch.object(inject, "CACHE", tmp_path), \
            mock.patch.object(
                inject.pkgutil, "get_data", return_value=b"NEW!"
            ), \
            mock.patch.object(inject.os, "replace",
                              wraps=inject.os.replace) as replace:
        assert inject._extract_injector() == cached

    assert cached.read_bytes() == b"NEW!"
    replace.assert_called_once()
    assert not list(tmp_path.glob(".injector-*.tmp"))


def test_injector_rejects_missing_or_non_dll_file(tmp_path):
    with pytest.raises(BolError, match="DLL not found"):
        inject.run_injector(tmp_path / "missing.dll")
    text = tmp_path / "client.txt"
    text.write_text("not a dll")
    with pytest.raises(BolError, match=r"Not a \.dll"):
        inject.run_injector(text)


def test_injector_requires_running_minecraft(tmp_path):
    dll = tmp_path / "client.dll"
    dll.write_bytes(b"MZ")
    with mock.patch.object(inject, "_mc_running", return_value=False):
        with pytest.raises(BolError, match="Start Minecraft first"):
            inject.run_injector(dll)


def test_injector_uses_game_prefix_and_wine_z_path(tmp_path):
    dll = tmp_path / "clients" / "Example Client.dll"
    dll.parent.mkdir()
    dll.write_bytes(b"MZ")
    engine = tmp_path / "engine"
    wine = engine / "files" / "bin" / "wine"
    wine.parent.mkdir(parents=True)
    wine.write_bytes(b"wine")
    injector_exe = tmp_path / "injector.exe"
    injector_exe.write_bytes(b"MZ")
    prefix = tmp_path / "prefix"
    logs = tmp_path / "logs"
    completed = subprocess.CompletedProcess([], 0)

    with mock.patch.object(inject, "_mc_running", return_value=True), \
            mock.patch.object(inject, "_wine_debugger_running",
                              return_value=False), \
            mock.patch.object(inject, "_post_injection_failure",
                              return_value=None), \
            mock.patch.object(inject, "proton_path", return_value=engine), \
            mock.patch.object(inject, "_extract_injector",
                              return_value=injector_exe), \
            mock.patch.object(inject, "active_prefix", return_value=prefix), \
            mock.patch.object(inject, "LOGS", logs), \
            mock.patch.object(inject.subprocess, "run",
                              return_value=completed) as run:
        assert inject.run_injector(dll) == "Example Client.dll"

    args, kwargs = run.call_args
    assert args[0] == [
        str(wine),
        str(injector_exe),
        "Z:" + str(dll.resolve()).replace("/", "\\"),
        "Minecraft.Windows.exe",
    ]
    assert kwargs["env"]["WINEPREFIX"] == str(prefix)
    assert kwargs["timeout"] == 60
    assert (logs / "injector.log").is_file()


def test_injector_reports_client_failure_with_64_bit_hint(tmp_path):
    dll = tmp_path / "bad.dll"
    dll.write_bytes(b"MZ")
    engine = tmp_path / "engine"
    wine = engine / "files" / "bin" / "wine"
    wine.parent.mkdir(parents=True)
    wine.write_bytes(b"wine")
    with mock.patch.object(inject, "_mc_running", return_value=True), \
            mock.patch.object(inject, "_wine_debugger_running",
                              return_value=False), \
            mock.patch.object(inject, "proton_path", return_value=engine), \
            mock.patch.object(inject, "_extract_injector",
                              return_value=tmp_path / "injector.exe"), \
            mock.patch.object(inject, "LOGS", tmp_path / "logs"), \
            mock.patch.object(
                inject.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 7)):
        with pytest.raises(BolError, match="64-bit"):
            inject.run_injector(dll)


def test_native_process_failure_is_not_misdiagnosed_as_bad_dll(tmp_path):
    dll = tmp_path / "client.dll"
    dll.write_bytes(b"MZ")
    engine = tmp_path / "engine"
    wine = engine / "files" / "bin" / "wine"
    wine.parent.mkdir(parents=True)
    wine.write_bytes(b"wine")
    with mock.patch.object(inject, "_mc_running", return_value=True), \
            mock.patch.object(inject, "_wine_debugger_running",
                              return_value=False), \
            mock.patch.object(inject, "proton_path", return_value=engine), \
            mock.patch.object(inject, "_extract_injector",
                              return_value=tmp_path / "injector.exe"), \
            mock.patch.object(inject, "LOGS", tmp_path / "logs"), \
            mock.patch.object(
                inject.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 3)):
        with pytest.raises(BolError, match="infrastructure failed") as raised:
            inject.run_injector(dll)

    assert "64-bit" not in str(raised.value)


def test_native_loadlibrary_timeout_has_specific_safe_diagnosis(tmp_path):
    dll = tmp_path / "slow-client.dll"
    dll.write_bytes(b"MZ")
    engine = tmp_path / "engine"
    wine = engine / "files" / "bin" / "wine"
    wine.parent.mkdir(parents=True)
    wine.write_bytes(b"wine")
    logs = tmp_path / "logs"
    with mock.patch.object(inject, "_mc_running", return_value=True), \
            mock.patch.object(inject, "_wine_debugger_running",
                              return_value=False), \
            mock.patch.object(inject, "proton_path", return_value=engine), \
            mock.patch.object(inject, "_extract_injector",
                              return_value=tmp_path / "injector.exe"), \
            mock.patch.object(inject, "LOGS", logs), \
            mock.patch.object(
                inject.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 8)):
        with pytest.raises(BolError, match="timed out.*retained safely"):
            inject.run_injector(dll)


def test_injector_timeout_is_actionable(tmp_path):
    dll = tmp_path / "slow.dll"
    dll.write_bytes(b"MZ")
    engine = tmp_path / "engine"
    wine = engine / "files" / "bin" / "wine"
    wine.parent.mkdir(parents=True)
    wine.write_bytes(b"wine")
    with mock.patch.object(inject, "_mc_running", return_value=True), \
            mock.patch.object(inject, "_wine_debugger_running",
                              return_value=False), \
            mock.patch.object(inject, "proton_path", return_value=engine), \
            mock.patch.object(inject, "_extract_injector",
                              return_value=tmp_path / "injector.exe"), \
            mock.patch.object(inject, "LOGS", tmp_path / "logs"), \
            mock.patch.object(
                inject.subprocess, "run",
                side_effect=subprocess.TimeoutExpired(["wine"], 60)):
        with pytest.raises(BolError, match="timed out"):
            inject.run_injector(dll)


def test_injector_refuses_game_already_in_winedbg(tmp_path):
    dll = tmp_path / "client.dll"
    dll.write_bytes(b"MZ")
    with mock.patch.object(inject, "_mc_running", return_value=True), \
            mock.patch.object(inject, "_wine_debugger_running",
                              return_value=True):
        with pytest.raises(BolError, match="already stopped.*crash debugger"):
            inject.run_injector(dll)


def test_post_injection_monitor_detects_winedbg_without_waiting():
    with mock.patch.object(inject, "_wine_debugger_running",
                           return_value=True), \
            mock.patch.object(inject, "_mc_running", return_value=True):
        assert "crash debugger" in inject._post_injection_failure(timeout=0)


def test_post_injection_monitor_detects_game_exit_without_waiting():
    with mock.patch.object(inject, "_wine_debugger_running",
                           return_value=False), \
            mock.patch.object(inject, "_mc_running", return_value=False):
        assert "Minecraft exited" in inject._post_injection_failure(timeout=0)


def test_post_injection_monitor_accepts_stable_game_without_waiting():
    with mock.patch.object(inject, "_wine_debugger_running",
                           return_value=False), \
            mock.patch.object(inject, "_mc_running", return_value=True):
        assert inject._post_injection_failure(timeout=0) is None


def test_injector_reports_asynchronous_client_crash(tmp_path):
    dll = tmp_path / "Borion.dll"
    dll.write_bytes(b"MZ")
    engine = tmp_path / "engine"
    wine = engine / "files" / "bin" / "wine"
    wine.parent.mkdir(parents=True)
    wine.write_bytes(b"wine")
    logs = tmp_path / "logs"
    with mock.patch.object(inject, "_mc_running", return_value=True), \
            mock.patch.object(inject, "_wine_debugger_running",
                              return_value=False), \
            mock.patch.object(inject, "_post_injection_failure",
                              return_value="Minecraft entered Wine's crash "
                                           "debugger immediately after the "
                                           "DLL was loaded (winedbg)"), \
            mock.patch.object(inject, "proton_path", return_value=engine), \
            mock.patch.object(inject, "_extract_injector",
                              return_value=tmp_path / "injector.exe"), \
            mock.patch.object(inject, "LOGS", logs), \
            mock.patch.object(
                inject.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0)):
        with pytest.raises(BolError, match="exact Minecraft version"):
            inject.run_injector(dll)
    assert "ERR post-injection" in (logs / "injector.log").read_text()
