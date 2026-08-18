"""bol.cli — argument parsing and command dispatch (main)."""
# SPDX-License-Identifier: MIT

import argparse
import os
import sys

from .auth import NativeAuth
from .config import APP, PRETTY, VERSION
from .content import cmd_import
from .doctor import doctor
from .games import list_mc_versions
from .gamesetup import do_setup
from .gui import gui
from .launch import (
    direct_launch_readiness,
    launch,
)
from .log import BolError, IS_TTY, desktop_notify, die, err, info, ok, warn
from .network import diagnose_network
from .prefix import reset_prefix
from .profiles import (
    create_profile,
    list_profiles,
    play_launch_command,
    profile_launch_command,
    require_profile_shortcuts_supported,
    require_shortcuts_supported,
    write_play_shortcut,
    write_profile_shortcut,
)
from .update import check_for_update, self_update, update_kind


def _run_network_diagnostics(host_ip=None):
    """Run and display the read-only connectivity checks."""
    healthy, checks = diagnose_network(host_ip)
    for check in checks:
        state = "OK" if check.ok is True else (
            "ÉCHEC" if check.ok is False else "INFO"
        )
        print(
            f"  {check.kind:14} {check.target}: {state} — {check.detail}"
        )
    return healthy


def _report_launch_failure(message):
    """Show why PLAY stopped when a shortcut left no terminal to print to."""
    if IS_TTY:
        return
    desktop_notify(f"Minecraft did not start.\n{message}")


def main():
    p = argparse.ArgumentParser(prog=APP, description=f"{PRETTY} {VERSION}")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("gui", help="open the launcher (default)")
    sub.add_parser("play", help="launch Minecraft")
    sp = sub.add_parser("setup", help="download & prepare Minecraft")
    sp.add_argument("--mc", metavar="VERSION",
                    help="Minecraft version tag (e.g. 1.26.21.1)")
    sp.add_argument("--beta", action="store_true", help="allow beta versions")
    sp.add_argument("--force", action="store_true", help="re-download / rebuild")
    lv = sub.add_parser("versions", help="list available Minecraft versions")
    lv.add_argument("--beta", action="store_true")
    sub.add_parser("login", help="sign in to a Microsoft account")
    ip = sub.add_parser("import",
                        help=("import .mcpack/.mcaddon/.mcworld/.mctemplate/"
                              ".mcskin"))
    ip.add_argument("files", nargs="+", metavar="FILE",
                    help="content file(s) to import")
    sub.add_parser("repair", help="reset the Wine prefix")
    dp = sub.add_parser("doctor", help="check host requirements")
    dp.add_argument(
        "--acknowledge-gpu-crash",
        action="store_true",
        help=("after repairing the graphics driver and rebooting, clear the "
              "interrupted-launch safety block"),
    )
    dp.add_argument(
        "--network",
        action="store_true",
        help="run read-only Xbox/Minecraft connectivity checks",
    )
    dp.add_argument(
        "--host",
        metavar="IP",
        help="also inspect the route to a LAN host IP (implies --network)",
    )
    pp = sub.add_parser(
        "profiles",
        help="create or list isolated Xbox account profiles",
    )
    profiles_sub = pp.add_subparsers(dest="profiles_cmd")
    profiles_create = profiles_sub.add_parser(
        "create",
        help="create an isolated profile and desktop shortcut",
    )
    profiles_create.add_argument("name", metavar="NAME")
    profiles_sub.add_parser("list", help="list isolated profiles")
    sc = sub.add_parser(
        "shortcut",
        help="create a desktop/Steam shortcut that launches Minecraft "
             "directly, without the launcher window",
    )
    sc.add_argument(
        "--profile",
        metavar="NAME",
        help="launch an isolated profile instead of the default installation",
    )
    sub.add_parser("update", help="check for and install launcher updates")

    sub.add_parser("changelog", help="display the launcher's release changelog history")

    a = p.parse_args()
    try:
        # Migration must precede every write to the new XDG root.
        from .util import _ensure_xdg_storage
        _ensure_xdg_storage()
        if a.cmd == "setup":
            mc = None
            if a.mc:
                mc = next((v for v in list_mc_versions(True)
                           if v["tag"] == a.mc), None)
                if not mc:
                    die(f"Minecraft version '{a.mc}' not found.")
            do_setup(mc_ver=mc, force=a.force)
            ok(f"Done. Run:  {APP} play")
        elif a.cmd == "play":
            launch()
        elif a.cmd == "shortcut":
            require_shortcuts_supported()
            profile = create_profile(a.profile) if a.profile else None
            entry = write_play_shortcut(
                profile_name=a.profile, profile_dir=profile)
            ok(f"Desktop shortcut: {entry}")
            print("Steam command: " + play_launch_command(profile))
            info("It starts Minecraft with no launcher window. Add it to "
                 "Steam with 'Add a Non-Steam Game' to play it from the "
                 "library, including Steam Deck Game Mode.")
            # A profile's own installation state lives under its BOL_HOME.
            for pending in ([] if profile else direct_launch_readiness()):
                warn(pending)
        elif a.cmd == "versions":
            for v in list_mc_versions(a.beta):
                print(f"  {v['tag']:<14}{'beta' if v['beta'] else 'stable':>7}"
                      f"   {v['size']>>20} MiB")
        elif a.cmd == "login":
            na = NativeAuth()
            if na.signed_in():
                ok("A Microsoft account is already linked.")
            else:
                na._flow(None, None)
                if not na.signed_in():
                    sys.exit(1)
        elif a.cmd == "doctor":
            system_healthy = doctor(a.acknowledge_gpu_crash)
            network_healthy = True
            if a.network or a.host:
                network_healthy = _run_network_diagnostics(a.host)
            sys.exit(0 if system_healthy and network_healthy else 1)
        elif a.cmd == "profiles":
            if a.profiles_cmd == "create":
                require_profile_shortcuts_supported()
                profile = create_profile(a.name)
                shortcut = write_profile_shortcut(
                    a.name,
                    profile_dir=profile,
                )
                ok(f"Profile: {profile}")
                ok(f"Desktop shortcut: {shortcut}")
                print("Steam command: " + profile_launch_command(profile))
            elif a.profiles_cmd == "list":
                profiles = list_profiles()
                if not profiles:
                    info("No isolated profiles.")
                for profile in profiles:
                    print(
                        f"  {profile['name']} ({profile['slug']}): "
                        f"{profile['path']}"
                    )
            else:
                pp.print_help()
        elif a.cmd == "update":
            rel = check_for_update()
            if not rel:
                ok(f"{PRETTY} {VERSION} is up to date.")
            else:
                info(f"Update available: v{rel['version']} "
                     f"(you have {VERSION}).")
                go = (update_kind() in ("git", "system") or not IS_TTY
                      or input(f"Install v{rel['version']} now? [y/N] ")
                      .strip().lower() == "y")
                if go:
                    state, msg = self_update(rel)
                    (ok if state == "ok" else warn)(msg)
                else:
                    info("Update skipped.")
        elif a.cmd == "import":
            cmd_import(a.files)
        elif a.cmd == "repair":
            reset_prefix()
        elif a.cmd == "changelog":
            try:
                from .util import gh_latest
                from .config import SELF_REPO
                rel = gh_latest(SELF_REPO)
                if not rel:
                    print("No release found.")
                    return
                tag = rel.get("tag_name", "Unknown")
                date = (rel.get("published_at") or "").split("T")[0]
                body = rel.get("body") or ""
                print(f"Release {tag} ({date})")
                print("-" * (len(tag) + len(date) + 11))
                print(body.strip())
            except Exception as e:
                print(f"Error fetching changelog: {e}")
        elif a.cmd == "gui":
            gui()
        else:
            if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
                gui()
            else:
                p.print_help()
    except BolError as exc:
        if not getattr(exc, "reported", False):
            err(str(exc))
        if a.cmd == "play":
            _report_launch_failure(str(exc))
        sys.exit(1)
