# Universal vkd3d-proton payload for engines r11, r12 and native5 through native12

BedrockOnLinux engine revisions `wow64-archs-r11`, `wow64-archs-r12` and
`wow64-archs-native5` through `wow64-archs-native12` use a reviewed vkd3d-proton 3.0.1
build containing both Vulkan device-generated-command implementations.
vkd3d-proton itself selects `VK_EXT_device_generated_commands` when the
selected device fully supports it and falls back to
`VK_NV_device_generated_commands` on older NVIDIA drivers.

This directory is the auditable provenance and rebuild bundle for that binary
payload. It does not contain game files and none of its scripts publish or
release anything. It is not, by itself, a complete corresponding-source
archive: the recipe checks out the exact recursive source tree described below.

## Exact source transformation

- Upstream: <https://github.com/HansKristian-Work/vkd3d-proton>
- Tag: `v3.0.1`
- Base commit: `3b10bd7a7ec6a7347e616cf8bea59333afec2255`
- Restored commit: `76c11d2e2b90b0a46dc894508e67e2aaacc2c04d`
- Operation: `git revert --no-commit 76c11d2e2b90b0a46dc894508e67e2aaacc2c04d`
- Resulting binary patch SHA-256:
  `91878d389dc0e315f770fa6c7fffea8f78f410a04796c38e2a6410ff0b9b4a33`

The complete revert is vendored as `restore-nv-dgc.patch`. The build script
performs the Git revert itself and requires its generated patch to be identical
to that file. `submodules.lock` similarly fixes every recursive submodule.

## Reviewed but not yet built: occluded frame latency

`fix-occluded-frame-latency.patch` is a reviewed fix for the game freezing
for good when its window is minimized or left behind on another virtual
desktop ([#50](https://github.com/Wyze3306/BedrockOnLinux/issues/50)). It is
**not part of the payload described above**: the build recipe, the pinned
output hashes and every shipped engine revision still correspond to the
unpatched source, and adopting it is a deliberate act described below.

### What it fixes

`dxgi_vk_swap_chain_Present()` returns `DXGI_STATUS_OCCLUDED` before it
enqueues anything, so the present callback that releases the frame latency
waitable object never runs. Minecraft creates a waitable swapchain — its
graphics log says `Enabling frame latency handles` — and waits on that object
before every frame, so the first occluded present ends its render loop.

Under Wine that is not a paused frame but a permanent hang, because the
condition that produced the occlusion can no longer clear:

1. The window manager iconifies the window, so Wine sets `WS_MINIMIZE` and
   the Win32 client rect becomes empty.
2. `adjust_surface_capabilities()` in `dlls/win32u/vulkan.c` derives the
   Vulkan surface extents from that client rect, so `maxImageExtent` is 0 and
   vkd3d-proton considers the swapchain occluded.
3. The render loop blocks on the waitable object, so the window stops pumping
   messages, and Wine never processes the un-iconify. The client rect stays
   empty and step 2 stays true for ever.

Releasing the waitable object on the occluded path — exactly what the
present-wait worker does for a retired present — keeps the loop running, so
the window keeps pumping messages and picks the window state back up.

The legacy WineD3D renderer is unaffected because it does not go through this
swapchain, which is why turning it on has been the workaround in that issue.
Upstream `master` still has the same early return, so this is worth sending
upstream rather than carrying for ever.

### Reproducing and validating

Reproduced and diagnosed on X11 (NVIDIA, Cinnamon/Muffin), where minimizing
the game window or switching virtual desktop freezes it every time:

```bash
# with the game running, iconify its window, restore it, then observe
python3 -c 'import ctypes;x=ctypes.cdll.LoadLibrary("libX11.so.6");
x.XOpenDisplay.restype=ctypes.c_void_p;d=ctypes.c_void_p(x.XOpenDisplay(None));
x.XIconifyWindow(d,ctypes.c_ulong(WINDOW_ID),x.XDefaultScreen(d));x.XFlush(d)'
wmctrl -i -a WINDOW_ID
```

Before the fix the window comes back black, the process settles at 0% CPU and
never repaints, and the desktop offers to force quit it. A Win32 probe run
against the same prefix reports the window still minimized after the restore
(`IsIconic` 1, `showCmd` `SW_SHOWMINIMIZED`, client rect `0x0`, window rect
`-32000,-32000 160x31`), a cross-process `ShowWindow(SW_RESTORE)` blocks
because the window is not pumping messages, and `WINEDEBUG=+x11drv` shows
`window_update_client_state minimizing win` with no restore ever following it.
After the fix the same sequence must return to a rendering window.

Expect the game to keep rendering while it is minimized, as it already does
under the legacy renderer; if that costs too much on battery, throttle the
occluded path with `dxgi_vk_swap_chain_platform_sleep_for_ns()` rather than by
withholding the release again.

### Adopting it

The patch applies to the pinned source after the NV-DGC revert
(`git apply` against `libs/vkd3d/swapchain.c`, verified). Building it means
changing the payload these hashes describe, so all of the following belong in
the same reviewed change:

1. Apply it in `scripts/build-vkd3d-universal.sh` after the revert
   verification, hash-checked against `provenance.env` the way
   `restore-nv-dgc.patch` already is.
2. Record its SHA-256 in `provenance.env`
   (`aac04d2bf777345272dec90f48b715d3eaebac7b2890f2b1ece4aa8daffc3465` for the
   file as vendored here) and add it to `PROVENANCE_FILES` and
   `verify_reviewed_provenance()` in `scripts/package-engine.sh`, whose pinned
   hash for `provenance.env` changes with it.
3. Regenerate `OUTPUT-SHA256SUMS` from the new build, never by hand.
4. Bump `WINEGDK_BUILD_REV` and the pinned hashes in `bol/vkd3d.py`, since the
   payload no longer matches the revision currently pinned there.

## Rebuild

The reviewed toolchain was Debian trixie with these exact components:

- GCC/MinGW `14.2.0-19+27+b1` (`14-win32` targets), for x86-64 and i686;
- MinGW binutils `2.44-3+12+b1`;
- `mingw-w64-tools` `12.0.0-5`;
- Meson `1.7.0-1`;
- Ninja Python package `1.13.0`;
- `glslang-tools` `15.1.0+1.4.309.0-1`;
- `spirv-tools` `2025.1~rc1-1`;
- Python `3.13.5` and Git `2.47.3`.

Put those tools on `PATH` (and Meson's Debian modules on `PYTHONPATH` when
using locally extracted `.deb` files), then choose a new empty work root:

```bash
scripts/build-vkd3d-universal.sh /tmp/bol-vkd3d-r11
```

The result is written to:

```text
/tmp/bol-vkd3d-r11/vkd3d-build-output/vkd3d-proton-3.0.1-nv-dgc
```

The historical directory names and per-architecture `SOURCE_DATE_EPOCH`
values are deliberate binary inputs: stripped vkd3d-proton DLLs retain
relative `__FILE__` strings, and GNU's PE linker records a timestamp. The
script refuses toolchain drift and finally verifies all four DLLs against
`OUTPUT-SHA256SUMS`. Never update those hashes merely to make an unexplained
rebuild pass.

After independent review, the verified directory can be passed to the engine
packager:

```bash
scripts/package-engine.sh /path/to/GDK-Proton-xuser \
  /tmp/bol-vkd3d-r11/vkd3d-build-output/vkd3d-proton-3.0.1-nv-dgc \
  75637b674e1f191e65753663c4c0c32bea05ba6e \
  /path/to/GDK-Proton10-32.tar.gz \
  /path/to/winegdk-native5-work/prefix
```

## Licence and distribution

vkd3d-proton source files are distributed under the GNU Lesser General Public
License 2.1 or later; the upstream LGPL 2.1 text is included as
`COPYING.LGPL-2.1`. Its recursively checked-out dependencies retain the
licences found in their pinned source trees. BedrockOnLinux's build recipe is
MIT-licensed with the main project.

When distributing the modified DLLs, retain this provenance, the revert patch
and all licence notices, and make the complete pinned recursive source checkout
available under the applicable licences so recipients can rebuild and modify
the corresponding source.

`scripts/package-engine.sh` verifies the fixed SHA-256 of every file in this
directory that forms the distribution bundle, verifies the four built DLLs
against `OUTPUT-SHA256SUMS`, and embeds the bundle in the engine at:

```text
files/share/bedrock-on-linux/licenses-and-provenance/vkd3d-proton-universal/
```

The embedded `SHA256SUMS` covers the licence, provenance lock, recursive
submodule lock, output hash lock and restoration patch. The packager extracts
those records back out of the completed compressed candidate and rechecks them
before it publishes the local archive path.
