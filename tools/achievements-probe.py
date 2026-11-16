#!/usr/bin/env python3
"""achievements-probe — what Xbox Live says about Minecraft's achievements.

Purpose (issues #55 and #152): answer "why don't achievements pop?" from the
outside, without launching the game, using the tokens the launcher has already
minted for the signed-in account.

It runs three checks against achievements.xboxlive.com:

  1. the catalog, with the dedicated user-only Achievements token the engine
     hands to that host — expected: the full Minecraft for Windows list, with
     what the account has already earned;
  2. the same catalog, with the profile token every other Xbox Live call uses —
     expected: empty, because that token is bound to a different Minecraft
     title. This is the difference issue #55 fixed;
  3. one unlock attempt — expected: HTTP 400, *"None of the submitted
     achievements may be updated in this fashion"*. Minecraft's achievements
     are awarded by Xbox from in-game events, never written by the client, so
     no token at all makes an unlock go through this way.

The unlock attempt targets an achievement id that does not exist, so it cannot
award, revoke or alter anything: it only shows how the service answers. No
token, gamertag or XUID is printed, and nothing is written to disk.

Usage:
  tools/achievements-probe.py [--data DIR] [--title-id ID] [--scid SCID]

DIR defaults to the launcher's data directory ($XDG_DATA_HOME or
~/.local/share, or ~/.var/app/io.github.wyze3306.BedrockOnLinux/data under the
Flatpak). The title id and service config id default to the values in the
packaged game's MicrosoftGame.Config.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Minecraft for Windows, from MicrosoftGame.Config: <TitleId>35760C07</TitleId>
DEFAULT_TITLE_ID = 0x35760C07
DEFAULT_SCID = "4fc10100-5f7a-4470-899b-280835760c07"
HOST = "https://achievements.xboxlive.com"
FLATPAK_DATA = ".var/app/io.github.wyze3306.BedrockOnLinux/data"


def data_dir():
    """Where the launcher keeps winegdk-preauth/device.json."""
    flatpak = Path.home() / FLATPAK_DATA / "bedrock-on-linux"
    if (flatpak / "winegdk-preauth" / "device.json").is_file():
        return flatpak
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(xdg) / "bedrock-on-linux"


def request(token, uhs, method, url, body=None):
    """One authenticated Xbox Live call; returns (status, text)."""
    headers = {
        "Authorization": "XBL3.0 x=%s;%s" % (uhs, token),
        "x-xbl-contract-version": "2",
        "Accept": "application/json",
        "Accept-Language": "en-US",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    call = urllib.request.Request(url, data=data, headers=headers,
                                  method=method)
    try:
        with urllib.request.urlopen(call, timeout=30) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def catalog(label, token, uhs, xuid, title_id):
    if not token or not uhs:
        print("  %-28s not in the pre-auth cache" % label)
        return
    status, text = request(
        token, uhs, "GET",
        "%s/users/xuid(%s)/achievements?titleId=%d&maxItems=1000"
        % (HOST, xuid, title_id))
    if status != 200:
        print("  %-28s HTTP %d %s" % (label, status, text[:120]))
        return
    try:
        achievements = json.loads(text)["achievements"]
    except (KeyError, TypeError, ValueError):
        print("  %-28s HTTP 200, unreadable response" % label)
        return
    earned = [a for a in achievements if a.get("progressState") == "Achieved"]
    latest = max((a.get("progression", {}).get("timeUnlocked") or ""
                  for a in earned), default="")
    print("  %-28s HTTP 200, %d listed, %d earned%s"
          % (label, len(achievements), len(earned),
             ", newest " + latest[:10] if latest else ""))


def unlock_attempt(label, token, uhs, xuid, title_id, scid):
    if not token or not uhs:
        print("  %-28s not in the pre-auth cache" % label)
        return
    status, text = request(
        token, uhs, "POST",
        "%s/users/xuid(%s)/achievements/%s/update" % (HOST, xuid, scid),
        {
            "action": "progressUpdate",
            "serviceConfigId": scid,
            "titleId": title_id,
            "userId": str(xuid),
            # An id no Minecraft achievement uses: the service answers before
            # anything could be awarded.
            "achievements": [{"id": "999999", "percentComplete": 100}],
        })
    try:
        described = json.loads(text).get("description") or text
    except ValueError:
        described = text
    print("  %-28s HTTP %d %s" % (label, status, described[:110]))


def main():
    parser = argparse.ArgumentParser(
        description="Report what Xbox Live answers about Minecraft's "
                    "achievements for the signed-in account.")
    parser.add_argument("--data", type=Path, default=None,
                        help="launcher data directory")
    parser.add_argument("--title-id", type=lambda v: int(v, 0),
                        default=DEFAULT_TITLE_ID)
    parser.add_argument("--scid", default=DEFAULT_SCID)
    args = parser.parse_args()

    root = args.data or data_dir()
    cache = root / "winegdk-preauth" / "device.json"
    if not cache.is_file():
        sys.exit("no pre-auth cache in %s — sign in with the launcher first."
                 % root)
    try:
        payload = json.loads(cache.read_text())
    except ValueError as exc:
        sys.exit("unreadable pre-auth cache: %s" % exc)
    xuid = payload.get("xbl_xuid")
    if not xuid:
        sys.exit("the pre-auth cache holds no XUID — sign in again.")

    print("Minecraft for Windows, title %d, %s"
          % (args.title_id, cache))
    print("Catalog:")
    catalog("dedicated Achievements", payload.get("achievements_token"),
            payload.get("achievements_uhs"), xuid, args.title_id)
    catalog("profile (every other call)", payload.get("xbl_token"),
            payload.get("xbl_uhs"), xuid, args.title_id)
    print("Unlock from the client:")
    unlock_attempt("dedicated Achievements", payload.get("achievements_token"),
                   payload.get("achievements_uhs"), xuid, args.title_id,
                   args.scid)
    print("\nA full catalog on the first line and an empty one on the second "
          "is the expected\nsplit: only the dedicated token sees this title. "
          "An unlock refused with\n\"may be updated in this fashion\" is Xbox "
          "saying these achievements are awarded\nfrom in-game events, not by "
          "the game — see the Achievements section of the README.")


if __name__ == "__main__":
    main()
