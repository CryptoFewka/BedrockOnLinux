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
