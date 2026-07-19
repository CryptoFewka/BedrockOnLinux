# Building BedrockOnLinux

The engine and application are built by a **public, reproducible, attested**
CI pipeline. Every component is built from pinned public inputs, so anyone can
rebuild it and get identical bytes, and every published artifact carries a
[SLSA build-provenance](https://slsa.dev) attestation.

```
OpenSSL-XCurl (from source) ┐
vkd3d-proton ────────────────┼─▶ managed engine ─▶ app {.deb, .pyz, AppImage, Flatpak}
WineGDK + public GDK base ───┘
```

## Trust model: verify, don't trust

The managed engine is a large binary. Rather than asking users to trust it, the
pipeline makes it **verifiable two ways**:

1. **Reproducibility.** Each build is byte-deterministic from pinned inputs, and
   its workflow asserts the result matches the SHA-256 committed in the repo
   (`bol/config.py`, `third_party/**`). A drift fails the build; it is never
   silently accepted.
2. **Attestation.** Each published release asset has a signed provenance record
   in a public transparency log:

   ```
   gh attestation verify <file> --repo <owner>/BedrockOnLinux
   ```

Because it is reproducible, you can also verify by rebuilding and comparing the
SHA-256 yourself.

## Components

| Component | Workflow | Source | Reproducibility |
|---|---|---|---|
| OpenSSL-XCurl set | `build-xcurl.yml` | msys2 MinGW curl closure (pinned in `third_party/xcurl-msys2.lock`) + `src/xcurl-cashim.c` + `src/cryptbase-stub.c` | `OPENSSL_XCURL_ARCHIVE_SHA256` |
| vkd3d-proton | `build-vkd3d.yml` | `HansKristian-Work/vkd3d-proton`, pinned Debian 13 (Trixie) mingw toolchain | `third_party/vkd3d-proton-universal/OUTPUT-SHA256SUMS` |
| WineGDK | `build-winegdk.yml` | `Weather-OS/WineGDK` (pinned commit), Debian 11 (Bullseye) container | packed-prefix hash |
| Managed engine | `build-engine.yml` | public base + WineGDK + vkd3d | `WINEGDK_ARCHIVE_SHA256` |
| App (4 formats) | `build-app.yml` | the launcher + attested engine/xcurl | per-artifact attestation |

Notes:

- **OpenSSL-XCurl** contains no opaque binary: the runtime DLLs are stock msys2
  packages (SHA-pinned), and the CA-shim + cryptbase RNG stub are compiled from
  `src/`. MinGW's per-link image-base + PE timestamps are pinned/zeroed
  (`--image-base`, `scripts/pe-zero-timestamps.py`) for byte reproducibility.
- **WineGDK** is compiled in a `debian:bullseye` container
  (`scripts/build-winegdk-container.sh`) rather than the original
  `scripts/build-winegdk-bullseye.sh`, whose debootstrap + unprivileged
  user-namespace path cannot run on hosted CI runners (`unshare --setgid` fails
  there even as root). The container build enforces the same glibc-2.31 ABI
  ceiling and writes the same provenance. `build-winegdk-bullseye.sh` is kept
  for local unprivileged builds.
  Determinism rests on three pinned inputs: `SOURCE_DATE_EPOCH`, the glibc-2.31
  toolchain, and a **fixed install prefix** (`/prefix` in the container). Wine's
  `configure --prefix` is baked into the binaries, so the workflow always builds
  to the same path; changing it changes the bytes (this is the Wine analogue of
  pinning a timestamp). Two builds to the same fixed prefix are byte-identical.
- **Engine** is assembled from the public, SHA-pinned `Weather-OS/GDK-Proton`
  `release10-32` base. `scripts/package-engine.sh` overlays the WineGDK prefix,
  installs the universal vkd3d DLLs, reconciles the `files/bin` wow64 launch
  aliases, and validates the result against `bol/vkd3d.py`'s manifest pins.
- **App** builds all four formats via `scripts/build-release.sh`. The AppImage's
  Tcl/Tk is compiled in a `debian:bullseye` container to meet the glibc-2.31
  baseline, then cached for the host build.

## Bootstrap order

On a fresh repo, run once (each `publish: true`):

1. **Build WineGDK**, **Build vkd3d-proton**, **Build OpenSSL-XCurl set** — creates the three attested Tier-1 releases.
2. **Build managed engine** — reuses the Tier-1 releases (verifies each attestation + SHA), asserts the engine reproduces `WINEGDK_ARCHIVE_SHA256`, publishes.
3. **Build application artifacts** — consumes the engine + xcurl, publishes all four formats.

`build-engine.yml` also accepts `rebuild_from_source: true` to recompile WineGDK
+ vkd3d in the same run.

## Updating the pins (re-baselining)

The pins are the reproducibility gate. When a pinned input legitimately changes
(a new `WINEGDK_SOURCE_COMMIT`, an msys2 package rotation, a toolchain bump), a
build will fail its SHA assertion. That is the signal to **rebuild and re-pin**,
not to loosen the check:

- vkd3d output → `third_party/vkd3d-proton-universal/OUTPUT-SHA256SUMS` (+ its
  SHA in `provenance.env` and `package-engine.sh`).
- xcurl set → `OPENSSL_XCURL_REV` + `OPENSSL_XCURL_ARCHIVE_SHA256` (regenerate
  `third_party/xcurl-msys2.lock` if msys2 rotated a package off the mirror).
- engine → `WINEGDK_ARCHIVE_SHA256` and the `bol/vkd3d.py` manifest pins.

## CI environment

- **Hosted runners only** (no self-hosted) for public verifiability.
- All GitHub Actions are pinned by commit SHA.
- The only non-source inputs are the public GDK-Proton base and the msys2
  packages, both SHA-pinned.
