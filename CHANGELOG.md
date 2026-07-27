# Changelog

## 2.1.1 — 2026-07-26

### Fixed

- Complete the in-game single-file picker on Minecraft's calling apartment,
  preventing the cross-apartment `RoFailFastWithErrorContext` crash triggered
  after choosing a custom skin.
- Keep the native picker owned by the game window so it remains visible in
  fullscreen and Gamescope sessions.
- Keep the WineX11 rendering surface at the client origin, removing the thin
  top and left borders that could appear when Minecraft entered fullscreen.
- Make the General, Advanced and Tools settings tabs scroll with an X11
  touchpad or mouse wheel even while the pointer is over a child control.
- Create and list isolated profiles at the shared installation root when the
  launcher is opened from a managed profile, while preserving direct
  `BOL_HOME` data-root overrides.
- Keep Flatpak's private XDG data directory writable while exposing only the
  pre-XDG data root read-only for automatic migration, fixing the
  `.shared-assets.lock` startup failure reported on Bazzite and CachyOS.
- Load Minecraft's Windows Achievements catalog with a dedicated user-only
  XSTS token while preserving the packaged Windows title, SCID and platform.
- Repair an invalid managed-prefix `user32.dll` atomically from the verified
  engine before Wine starts, and diagnose its `c0000020` load failure instead
  of incorrectly reporting a missing `ntdll` patch.
- Remove the inherited Wine 10 i386 Unix runtime and force the managed engine's
  pure-WoW64 path, avoiding host i386 dependencies on minimal distributions.
- Keep graphics caches across Minecraft version changes and prevent Advanced
  diagnostics from tracing hot GDK polling channels that can starve the game.
- Ship the fixes in the reproducible, attested `wow64-archs-native12` managed
  engine.

## 2.1.0 — 2026-07-25

### Startup, engine, and compatibility

- Repair or reject partial Wine prefixes instead of reporting an incomplete
  installation as successful, including timed-out `wineboot`, incomplete
  registry hives, missing WineGDK activation, and residual Wine processes.
- Provide the missing native `cryptbase.SystemFunction036` implementation,
  removing the RNG recursion that prevented Wine services and the game window
  from starting.
- Upgrade the managed engine to `wow64-archs-native6` and UMU to 1.4.3.
- Disable incompatible global Proton options such as
  `PROTON_ENABLE_WAYLAND=1` automatically while preserving overrides explicitly
  configured in Advanced Settings.
- Recover stale XWayland `DISPLAY` values from session sockets owned by the
  current user, including on Hyprland, and improve image restoration after
  switching virtual desktops.
- Use the primary RandR monitor rather than the combined virtual-desktop size
  and tolerate non-UTF-8 system output.
- Recognize nested Gamescope sessions, remove Wine decorations on Steam Deck,
  and improve fullscreen behavior in Steam Game Mode.
- Configure DualSense and DualSense Edge through Steam Input when a virtual
  controller is present, and SDL otherwise.
- Add an explicit opt-in WineD3D compatibility renderer for systems limited to
  Vulkan 1.2.
- Repair WinRT activation and open the native file chooser for world imports
  and skin-file selection. Applying a custom skin remained a known issue in
  2.1.0 and is fixed in 2.1.1.
- Add direct `.mcskin` package import while Minecraft is stopped.
- Detect Minecraft archives replaced under the same tag, verify their identity
  and digest, and activate or restore them transactionally.
- Harden 64-bit DLL injection with a timeout, process validation,
  immediate-crash detection, and accurate failure messages.

### Xbox accounts, profiles, and multiplayer

- Add isolated local Xbox profiles. Each profile keeps its own account, prefix,
  settings, and worlds while large game, engine, and runtime downloads are
  shared under a lock.
- Serialize preparation, repair, launch, and shared-resource access to prevent
  inconsistent concurrent installations or sessions.
- Make Xbox pre-authentication errors more precise and actionable without
  retaining or printing response secrets.
- Add `doctor --network` and `doctor --host <IP>` for read-only checks of DNS,
  TLS 1.2+, clock/RTC, routes, VPN/container interfaces, and
  Xbox/PlayFab/Minecraft endpoints.
- Recognize `InitialConnection-13`, `InitialConnection-25`, client/host build
  mismatches, and the misleading “world full” message, with guidance that does
  not require users to configure environment variables manually.

### Interface, storage, and recovery

- Redesign the interface with tabbed settings, version search/filtering,
  persistent version selection, Copy/Clear log actions, and consistent
  PLAY/STOP states.
- Integrate application and Minecraft changelogs into the GUI and add the
  `changelog` CLI command, with disk caching, Markdown rendering, links,
  filtering, and controlled refresh.
- Rework Microsoft sign-in with a locally generated QR code, synchronized
  theme, gamertag retrieval, and two-step sign-out.
- Replace generic Tk dialogs with themed, centered, resizable, scrollable
  dialogs suitable for high scaling and multi-monitor desktops.
- Add 100/150/200% UI scaling, fix application restart outside a zipapp, and
  preserve theme choices.
- Add persistent Gamescope arguments and custom environment-variable fields
  with safe parsing of quoted values.
- Add GUI-driven data relocation with free-space checks, locking, internal-link
  repair, rollback, and safe restart.
- Honor `XDG_DATA_HOME`. Flatpak now writes to its private storage and migrates
  the previous directory transactionally without merging two populated trees;
  the old tree remains available as a recovery backup.
- Expose acknowledgement of an earlier GPU incident in the GUI only when
  eligible, revalidate it under lock, and remove orphaned markers after a clean
  exit.

### Security, integrity, and publication

- Stop forwarding an ambient `GITHUB_TOKEN` to non-GitHub hosts.
- Harden downloads and extraction against path traversal, unsafe links,
  partial archives, and unverified replacements.
- Add a public, reproducible, attested pipeline for OpenSSL-XCurl,
  vkd3d-proton, WineGDK, the managed engine, and all four application formats.
- Pin source trees, containers, APT snapshots, Python dependencies,
  intermediate digests, and final artifacts; mismatches now fail closed.
- Separate read-only build jobs from attestation/publication jobs, add a
  rolling nightly release, and include a bill of materials in releases.
- Add CI checks for tests, Python compilation, ShellCheck, actionlint, Ruff,
  Zizmor, CodeQL, published-pin validation, and a native Wine RNG test.
- Separate `SHA256SUMS` for application artifacts from `inputs.sha256` for
  separately published engine and XCurl inputs.

### Validation

- Pass 441 automated tests and 29 subtests.
- Pass Ruff, high-confidence Vulture, actionlint, Zizmor, and AppStream
  validation.
- Rebuild the WineGDK prefix and native6 engine reproducibly.
- Load `wine-11.1` successfully inside the pinned Steam sniper runtime.
- Build `.deb`, `.pyz`, AppImage, and Flatpak artifacts with the expected
  version and metadata.

Known limitations:

- #48/#61: the launcher diagnoses known “world full” causes but cannot alter a
  remote Windows host.
- #15: Xodus is not included in this release.
- #55: Xbox achievement submission has not been validated end to end.
- #63: launcher-induced repeated shader compilation and debug-I/O stutter are
  fixed, but initial RX 9060 XT chunk generation still needs reporter
  validation.
- #90: Borion and other version-specific third-party DLLs are not guaranteed
  and may crash Minecraft.
- WineD3D is a degraded compatibility mode; performance and rendering are not
  guaranteed.
- Published builds target x86-64 glibc Linux. The graphical launcher requires
  X11/XWayland; ARM and musl-only systems are not supported.
