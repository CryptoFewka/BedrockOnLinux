# Changelog

## Unreleased

### Fixed

- Stop the crash that hit the main menu as soon as an account was signed in.
  Offline play was fine, but signing in brought the game to the menu, let it
  start loading the account and then killed it with a page fault
  ([#171](https://github.com/Wyze3306/BedrockOnLinux/issues/171)). The engine's
  Xbox store component answered two of the queries the signed-in menu makes —
  the game licence and the list of associated products — as if a Microsoft
  Store were really behind it, handing back a hard-coded licence and an empty
  product catalogue. The game took those for real answers, marked its store
  data loaded and then read through catalogue entries that were never filled
  in. Both queries now report that no store service is available, which is a
  state the game already knows how to handle: it retries a few times and
  carries on with the store disabled, and the menu stays up. Signing out and
  back in, resetting the Wine prefix or reinstalling Minecraft were never real
  cures — the crash depended on timing, which is why it came and went.

- Open the launcher again. 2.1.4 made starting the launcher in a Gamescope
  session start Minecraft directly instead of showing its window, to get past
  Steam Deck Game Mode showing one window at a time. That took the launcher
  away from the people who wanted it: in Game Mode nothing appeared at all,
  and the session probe matched on the reporter's desktop session too, where
  the game started the moment the launcher was opened
  ([#127](https://github.com/Wyze3306/BedrockOnLinux/issues/127),
  [#130](https://github.com/Wyze3306/BedrockOnLinux/issues/130)). Starting the
  launcher now opens the launcher, in every session. The launcher-free launch
  stays available where it is asked for explicitly: `bedrock-on-linux play`,
  the **Play without the launcher** app-menu action, and the *Minecraft
  Bedrock* shortcut that `shortcut` writes. `BOL_FORCE_GUI` no longer has
  anything to override and can be dropped from Steam launch options.

- Reach the game from the launcher in Game Mode. Gamescope presents a single
  application window, so the launcher's own kept Minecraft's off screen — the
  game was audible but never appeared
  ([#130](https://github.com/Wyze3306/BedrockOnLinux/issues/130)). **▶ PLAY**
  now unmaps the launcher window as soon as the game process exists and maps
  it back when the game closes, so the launcher is usable there rather than
  bypassed. A failure
  brings the window back first, so its error dialog has a window to appear in.
  Desktop sessions show both windows and are unchanged.

## 2.1.4 — 2026-08-11

### Fixed

- Reach the game from Steam Deck Game Mode. Adding the launcher to Steam and
  starting it there left an interface whose buttons did nothing — no hover, no
  click — and when a launch did happen the game stayed audible but invisible,
  both only with the Flatpak
  ([#127](https://github.com/Wyze3306/BedrockOnLinux/issues/127),
  [#130](https://github.com/Wyze3306/BedrockOnLinux/issues/130)). Two causes,
  both specific to that session:
  - Gamescope's Xwayland legitimately reports zero RandR GPU providers, which
    the launcher already tolerates — but only once it recognises the session,
    and it recognised it from environment variables that a Flatpak sandbox
    does not forward. Game Mode therefore looked like an ordinary X11 session
    running on a software renderer, and the launcher refused to start with
    *could not verify any X11 hardware provider*. That is why the AppImage was
    unaffected and why connecting an external monitor, which makes a provider
    appear, was the only known way out. The session is now identified by
    Gamescope's own `GAMESCOPE_*` properties on the X root window, which no
    sandbox hides.
  - Game Mode shows one application window at a time, so the launcher's own
    window stands between Steam and the game. Starting the launcher there now
    starts the game directly instead, the same launcher-free path a *Minecraft
    Bedrock* shortcut uses. A first run still opens the window, since
    installing a version and signing in cannot be done without it, and
    `BOL_FORCE_GUI=1` in the shortcut's launch options opens it on demand.

- Keep playing after minimizing the game or leaving it on another virtual
  desktop. The window came back black and never repainted, the desktop
  eventually offered to force quit it, and it happened every single time;
  turning on the Legacy compatibility renderer was the only way out, at the
  cost of the anti-aliasing. An occluded `Present` returns before it queues
  anything, so the callback that releases the frame latency object Minecraft
  waits on never runs — and under Wine that is permanent rather than a paused
  frame, because the blocked render loop stops pumping messages, so the window
  manager can no longer deliver the restore that would clear the occlusion.
  The managed engine, now `wow64-archs-native14`, rebuilds vkd3d-proton with
  that object released on the occluded path, so the render loop survives being
  hidden and picks the window back up. Only `d3d12core.dll` changes; the
  Legacy compatibility renderer is no longer needed for this. The game now
  keeps rendering while minimized, which is what it already did under that
  renderer ([#50](https://github.com/Wyze3306/BedrockOnLinux/issues/50)).

### Build

- Make the reviewed vkd3d-proton payload reproducible again. `git diff
  --check` counted the blank line ending the restored upstream shader source
  as an error and aborted the build, and the generated revert patch is
  compared byte for byte against the vendored one while its `index` lines
  abbreviate blob hashes with `core.abbrev=auto`, whose length grows with the
  repository — upstream had since crossed that threshold, so the check failed
  on a correct revert. Repackaging a published engine to change only its vkd3d
  payload also now finds the WineGDK build records where such an engine keeps
  them, beside its embedded provenance.

### Internal

- Share the helpers six modules had each reimplemented: hashing a file,
  removing a path without following a directory symlink, testing existence
  including a dangling symlink, and reading an opt-in environment string. Two
  of the copies were byte-identical. Only one behaviour changed, and only for
  the better: removing a game directory now survives a concurrent delete, as
  the other two copies of that helper always did.

## 2.1.3 — 2026-08-09

### Added

- Start Minecraft without the launcher window. **Tools ▸ Create direct launch
  shortcut** and `bedrock-on-linux shortcut` write a *Minecraft Bedrock*
  desktop entry that runs the game directly, and print the equivalent command;
  `--profile NAME` scopes one to an isolated profile. Steam adds that entry as
  a non-Steam game, so it plays from the library and from Steam Deck Game Mode
  without the GUI in between. The launcher's own app-menu entry also gained a
  **Play without the launcher** action, which is how the Flatpak — where
  host-visible shortcuts cannot be written from the sandbox — offers the same
  thing, alongside `flatpak run io.github.wyze3306.BedrockOnLinux play`
  ([#158](https://github.com/Wyze3306/BedrockOnLinux/issues/158)).
- Say why a launcher-free launch stopped. A desktop or Steam shortcut discards
  the launcher's output, so a refused launch — no version installed, a busy
  prefix, a blocked graphics session — looked exactly like the shortcut doing
  nothing. Those failures are now raised as a desktop notification. Creating a
  shortcut also names the first-run steps it cannot perform itself, since it
  has nowhere to display the Microsoft device code or a version picker.

### Performance

- Build the engine with Wine's in-process synchronization (ntsync) backend,
  which had been silently compiled out. Wine 11 has no esync/fsync any more,
  so `/dev/ntsync` is its only fast synchronization path — but the engine is
  built in Debian 11 to hold the `GLIBC_2.31` ABI ceiling, and Bullseye's
  `linux-libc-dev` is kernel 5.10 and ships no `linux/ntsync.h`. `configure`
  therefore left `HAVE_LINUX_NTSYNC_H` undefined and reduced the whole backend
  to stubs, so every Win32 wait, `SetEvent`, mutex and semaphore became a
  wineserver round-trip. Because the wineserver serialises those requests,
  Minecraft's worker threads queued behind one another and the game behaved as
  if it were single-threaded: chunk generation stalled, the GPU was left idle
  while one core saturated, and server TPS collapsed. The reviewed upstream
  UAPI header is now vendored in `third_party/linux-uapi/` and installed into
  the build container, and both build scripts fail closed if the resulting
  `wineserver` does not carry the ntsync code path. Checking for the header is
  not enough: kernels 6.10 to 6.13 shipped a two-ioctl preview that satisfies
  `HAVE_LINUX_NTSYNC_H` while Wine's `NTSYNC_IOC_EVENT_READ` gate still
  compiles the backend out, which is why building the engine yourself on, say,
  Debian 13 produced a stubbed engine too
  ([#63](https://github.com/Wyze3306/BedrockOnLinux/issues/63),
  [#139](https://github.com/Wyze3306/BedrockOnLinux/issues/139),
  [#143](https://github.com/Wyze3306/BedrockOnLinux/issues/143),
  [#148](https://github.com/Wyze3306/BedrockOnLinux/issues/148),
  [#150](https://github.com/Wyze3306/BedrockOnLinux/issues/150)).
- Say so before the game starts when that fast path is still unusable, instead
  of leaving the stutter unexplained. PLAY and Doctor now report whether the
  installed engine carries the backend and whether the running kernel exposes
  `/dev/ntsync` (mainline Linux 6.14 and later, module `ntsync`), with the
  matching remedy. Silence the notice with `BOL_SKIP_NTSYNC_CHECK=1`.
- Describe the Legacy compatibility renderer accurately. Settings, the
  diagnostic hint and the README all implied it merely "bypasses DXVK", but
  `PROTON_USE_WINED3D=1` swaps the entire Direct3D stack — D3D9 through
  **D3D12** — to WineD3D, dropping DXVK *and* vkd3d-proton. Minecraft renders
  exclusively through D3D12, so the option replaces the renderer the game
  actually uses, which is why following that advice produced artifacts and
  "weird blocks" rather than a speed-up
  ([#63](https://github.com/Wyze3306/BedrockOnLinux/issues/63)).
- Ignore an inherited `PROTON_NO_NTSYNC`, so a stale global export can no
  longer serialise every worker thread behind the wineserver. The Advanced
  custom-environment field remains the supported way to turn the fast path off.

### Fixed

- Play without a working Xbox Live session. PLAY refused to start the game at
  all when no Microsoft account was linked (`No Microsoft account linked —
  click 'Sign in' first.`) or when the Xbox Live chain could not be completed,
  so a machine with no Internet connection could not reach even a single-player
  world: the offline token refresh failed, the pre-auth was reported as
  `Xbox Live rejected this Microsoft account`, and signing out replaced that
  error with the other one. Minecraft now starts in offline mode instead —
  single-player worlds and LAN play work, while Realms, servers, the
  Marketplace and Xbox friends stay unavailable until sign-in succeeds, which
  the launcher says before the game window appears. An unreachable Microsoft
  token endpoint is also reported as a connection failure rather than as a
  rejected account, and credentials the launcher could not validate are no
  longer handed to the engine, so it settles into offline mode instead of
  chasing a sign-in that cannot complete
  ([#160](https://github.com/Wyze3306/BedrockOnLinux/issues/160)).

### Packaging

- Ship an `.rpm`, so the Fedora-based gaming distributions install the launcher
  like any other application instead of falling back to the AppImage
  ([#156](https://github.com/Wyze3306/BedrockOnLinux/issues/156)). It carries
  the same hash-pinned GUI stack as the `.deb`, declares its dependencies with
  Fedora's package names, is built reproducibly from `SOURCE_DATE_EPOCH`, and
  goes through the same payload verification and build attestation as every
  other artifact. `scripts/build-release.sh` builds it beside the others, and
  `sudo dnf install ./bedrock-on-linux-*.x86_64.rpm` installs it.
- Publish nightly builds again. Every nightly since 2.1.2 failed: the candidate
  build always asked for the Flatpak's *released* manifest, so the bundle came
  from the pinned tag while the payload audit compared it against the branch
  being built. The first commit merged after a release therefore broke the
  build for good, and with the Flatpak a required format, no nightly release
  was cut at all. A nightly now builds its Flatpak from the checkout like every
  other artifact, keeping the AppStream metadata, and a stale pin during a real
  release build says so instead of only listing the files that differ.
- Document which component needs the Flatpak
  `--talk-name=org.freedesktop.Flatpak` permission and how to verify it. No
  launcher code calls the portal, so the permission read as unused; the caller
  is the bundled Steam Linux Runtime, whose `pressure-vessel` switches to a
  Flatpak sub-sandbox when it sees `/.flatpak-info` and requests it through
  `org.freedesktop.Flatpak.Development.Spawn`. Revoking the name leaves the
  GUI, downloads and sign-in working and breaks only the game launch, which is
  what made it look removable
  ([#157](https://github.com/Wyze3306/BedrockOnLinux/issues/157)).

## 2.1.2 — 2026-08-03

### Friends, Realms and Xbox Live

- Stop exporting `GNUTLS_SYSTEM_PRIORITY_FILE`, which restores Friends worlds
  over the internet. The variable was meant to force TLS 1.2 for Azure-fronted
  hosts, but inside the Flatpak it did the opposite: its mere presence made
  Wine's `secur32` abandon its own version-capped priority and negotiate
  TLS 1.3, which Wine 11.1 schannel does not support, so every in-game WinHTTP
  TLS connection died just after the handshake. The XSAPI realtime-activity
  WebSocket could then never connect, the session write went out without a
  connection id, and Minecraft reported the misleading `world is full` — or
  published a hosted world as LAN-only. Wine's own schannel priority already
  caps at TLS 1.2, so removing the workaround achieves what it intended
  ([#48](https://github.com/Wyze3306/BedrockOnLinux/issues/48),
  [#145](https://github.com/Wyze3306/BedrockOnLinux/issues/145),
  [#125](https://github.com/Wyze3306/BedrockOnLinux/issues/125),
  [#133](https://github.com/Wyze3306/BedrockOnLinux/issues/133)).
- Name an unsynchronized system clock when Xbox Live rejects the account. A
  drifted clock puts the signed XSTS request outside its validity window and is
  refused exactly like an unusable profile, so the generic advice sent people
  to xbox.com to fix an account that was fine
  ([#119](https://github.com/Wyze3306/BedrockOnLinux/issues/119)).
- Remove WineGDK's durable refresh token from the Wine prefix on sign-out, so
  no reusable credential survives in the prefix registry
  ([#120](https://github.com/Wyze3306/BedrockOnLinux/pull/120)).
- Restore the Microsoft sign-out button, which failed with a misleading
  "another setup, repair, or game session is already in progress" on every
  attempt: a re-introduced lock wrapper deadlocked against the one that now
  lives inside the sign-out itself.

### Fixed

- Recover automatically from an interrupted first-run data migration instead
  of refusing to start forever with `stale migration staging path exists`,
  which kept the Flatpak from opening at all on affected systems
  ([#124](https://github.com/Wyze3306/BedrockOnLinux/issues/124)).
- Retry the Wine prefix once with a reinstalled RNG component when `wineboot`
  aborts on the unresolved `advapi32.SystemFunction036` forward, instead of
  waiting out the 300-second timeout and reporting a broken prefix
  ([#138](https://github.com/Wyze3306/BedrockOnLinux/issues/138)).
- Print an acknowledgement command the running installation can actually run
  (`flatpak run …`, the AppImage or zipapp path) rather than a program name
  that only exists for the distribution packages
  ([#136](https://github.com/Wyze3306/BedrockOnLinux/issues/136)).
- Stop offering the GPU acknowledgement for an interrupted launch from the
  running boot, which is always refused until the machine is rebooted.
- Report a fatal in-game page fault as the game process crashing instead of
  answering `No known cause`
  ([#132](https://github.com/Wyze3306/BedrockOnLinux/issues/132)).
- Scroll the version picker with a mouse wheel or touchpad, including while
  the pointer is over a version row
  ([#112](https://github.com/Wyze3306/BedrockOnLinux/issues/112)).
- Place the version picker correctly at 150% and 200% UI scaling
  ([#117](https://github.com/Wyze3306/BedrockOnLinux/pull/117)).
- Stop re-downloading the changelog every time the Game/Launcher tabs are
  switched ([#131](https://github.com/Wyze3306/BedrockOnLinux/pull/131)).
- Seed the `cryptbase` RNG component before the managed `wineboot` rather than
  after it, so the first prefix creation cannot race the missing forward.
- Refresh managed-engine release metadata instead of reusing a stale cached
  response, which could pin setup to an engine asset that no longer matches
  ([#110](https://github.com/Wyze3306/BedrockOnLinux/pull/110)).
- Survive a malformed or truncated PE header when raising the game's stack
  reserve, instead of failing the launch with an unhandled `struct` error
  ([#140](https://github.com/Wyze3306/BedrockOnLinux/pull/140)).
- Build the QR code image on the main thread, removing a CustomTkinter
  threading hazard during device-code sign-in
  ([#140](https://github.com/Wyze3306/BedrockOnLinux/pull/140)).

### Security

- Reject archive members whose path escapes the destination directory when
  importing `.mcpack`/`.mcaddon`/`.mcworld`/`.mctemplate` content, closing a
  Zip Slip path-traversal write outside the content directory
  ([#140](https://github.com/Wyze3306/BedrockOnLinux/pull/140)).

### Packaging

- Build the Flatpak with `--release` so bundles keep their AppStream metadata
  ([#141](https://github.com/Wyze3306/BedrockOnLinux/pull/141)).
- Pin the Flatpak manifest to the released tag
  ([#142](https://github.com/Wyze3306/BedrockOnLinux/pull/142)).

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
