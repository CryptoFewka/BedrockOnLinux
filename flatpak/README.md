# Flatpak / Flathub

App ID: **`io.github.wyze3306.BedrockOnLinux`**

| File | Role |
|---|---|
| `io.github.wyze3306.BedrockOnLinux.yml` | Flathub-ready manifest (pinned sources, real sha256) |
| `io.github.wyze3306.BedrockOnLinux.desktop` / `.metainfo.xml` | installed from the app source by the manifest |
| `flathub.json` | restricts Flathub builds to x86_64 (the game is 64-bit only) |
| `../scripts/build-flatpak.sh` | local build (working tree); `--release` builds the pure manifest |

## Local build & test

```bash
scripts/build-flatpak.sh        # → dist/BedrockOnLinux-<ver>-x86_64.flatpak
flatpak install --user dist/BedrockOnLinux-*-x86_64.flatpak
flatpak run io.github.wyze3306.BedrockOnLinux
```

Test order: (1) build passes — the `python3` module aborts if tkinter didn't
link; (2) the Tk window opens; (3) engine/game download lands in
`~/.var/app/io.github.wyze3306.BedrockOnLinux/data/bedrock-on-linux`; (4) the
UMU runtime stays below that same private directory; (5) the game launches — the
pressure-vessel-inside-Flatpak step is the one most likely to need iteration.
To debug: `flatpak run --devel --command=sh io.github.wyze3306.BedrockOnLinux`
then `bedrock-on-linux play`.

The application only installs the reviewed prebuilt engine. Keep its matching
release asset available; reproducible engine builds remain a maintainer task.

## Publishing on Flathub

Per <https://docs.flathub.org/docs/for-app-authors/submission>:

1. **Tag the release** this manifest pins, then fill in the commit:

   ```bash
   git tag -a vX.Y.Z -m 'BedrockOnLinux vX.Y.Z' && git push origin vX.Y.Z
   git rev-parse 'vX.Y.Z^{commit}'   # → add as `commit:` under the git source
   ```

2. **Verify locally** what Flathub CI will run:

   ```bash
   flatpak install flathub org.flatpak.Builder
   flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest \
     flatpak/io.github.wyze3306.BedrockOnLinux.yml
   flatpak run --command=flatpak-builder-lint org.flatpak.Builder appstream \
     flatpak/io.github.wyze3306.BedrockOnLinux.metainfo.xml
   scripts/build-flatpak.sh --release
   ```

3. **Open the submission PR**:

   ```bash
   # fork github.com/flathub/flathub first (keep "copy the master branch only" UNCHECKED)
   git clone --branch=new-pr git@github.com:<you>/flathub.git flathub-pr
   cd flathub-pr && git checkout -b add-bedrockonlinux new-pr
   cp ../BedrockOnLinux/flatpak/io.github.wyze3306.BedrockOnLinux.yml .
   cp ../BedrockOnLinux/flatpak/flathub.json .
   git add . && git commit -m 'Add io.github.wyze3306.BedrockOnLinux'
   git push -u origin add-bedrockonlinux
   # open the PR against flathub/flathub branch `new-pr`,
   # then comment:  bot, build io.github.wyze3306.BedrockOnLinux
   ```

4. After merge, Flathub creates `flathub/io.github.wyze3306.BedrockOnLinux`
   and invites you as collaborator. **Verify the app** (Settings on
   <https://flathub.org> → your app → Verification → via the GitHub account
   `wyze3306`, which matches the `io.github.wyze3306.*` ID).

### Permission rationale (paste into the PR description)

- `--device=all`: Vulkan GPU access + game controllers (hidraw).
- `--talk-name=org.freedesktop.Flatpak`, `--allow=devel`, `--allow=multiarch`:
  the game runs through umu-launcher → pressure-vessel (Steam Linux Runtime),
  which spawns its sub-sandbox via the Flatpak portal and runs 32-bit Wine
  code; same permission set as Lutris, Bottles and Heroic.
- `--share=network`: downloads + Microsoft/Xbox sign-in + multiplayer.
- `--filesystem=~/.local/share/bedrock-on-linux:ro`: one-upgrade transition
  access to the exact old data root. The explicit home-relative path leaves the
  private XDG destination writable; the `xdg-data` alias must not be used here
  because Flatpak maps it onto that destination. The app atomically copies the
  old tree before any command can populate the destination and cannot continue
  writing to the host folder. Remove this permission after the migration
  window.

The game, engine, prefix, account, UMU runtime and empty Proton Steam-compat
directory all live under Flatpak's private XDG data directory. The narrowly
scoped legacy permission above is read-only and transitional, not a storage
location. The old tree remains as a recovery copy; conflicting populated trees
are never merged automatically.

The app ships no Minecraft content; users supply their own game files
(launcher precedent on Flathub: `io.mrarm.mcpelauncher`).
