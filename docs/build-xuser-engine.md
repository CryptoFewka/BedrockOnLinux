# Building a GDK-Proton with WineGDK XUser (for native login)

Native in-game login (no ProxyPass) needs a GDK-Proton whose bundled WineGDK
implements `XUser`. **No public build has it yet** (verified 2026-05-18:
`Weather-OS/GDK-Proton` releases + forks, `olivi-r` repos). The stock build
makes Minecraft show *"Authentication failed — 0x80004001"*
(`0x80004001 = E_NOTIMPL`, the "XUser absent" signature).

This is upstream C/Wine work, out of scope for this Python launcher. The
launcher's job is done (it mints the MSA refresh token and seeds it where
WineGDK's XUser reads it — see [`native-login.md`](native-login.md)) and it
can now point at a build you supply. This doc maps what to assemble.

## Honest status of the pieces

| Piece | Where | State |
|---|---|---|
| `XUser` interfaces + token via registry | WineGDK PR #33 (`olivi-r:master`) | WIP draft. Token works; **`signature = NULL`** (`signatureSize=0`) → signed Xbox endpoints (servers, marketplace) still fail. 39 stubs remain. |
| `XLauncher` | WineGDK PR #37 (`olivi-r:xlauncher`) | Split from #33, semi-functional. |
| Working Xbox Live (signatures, sign-in) | `minecraft-linux/mcpelauncher-gdk` branch `xbox-live-dev` + replacement DLLs in `minecraft-linux/mcpelauncher-gdk-dependencies` (`libhttpclient.GDK.dll`, `XCurl.dll`) | @ChristopherHX got marketplace + public servers working on it (2026-05-02), but **paused** it ("several AI-generated inaccuracies"); PR #42 was **closed/rejected** by upstream over methodology. |

So a fork today is **research-grade**, not merge-and-build: PR #33's missing
request signature is the critical gap, and the only known fill (HX's branch)
is explicitly flagged inaccurate and unmaintained.

Licensing: WineGDK's `xgameruntime` code is declared **CC0 / public domain**
by the maintainer (the rest of Wine stays LGPL). You may freely fork, derive
and redistribute the `xgameruntime` parts.

## Already assembled: `Wyze3306/WineGDK` branch `xuser-login`

Done in this project, on `Wyze3306/WineGDK` **`master`** (cloned at
`~/Bureau/WineGDK`):

- `olivi-r/master` (XUser, PR #33 — the Microsoft sign-in)
- XLauncher (PR #37) integrated on top
- **Request signing implemented** (was the gap below): a per-user ECDSA
  P-256 proof key (bcrypt), its public key sent as `ProofKey` in
  user/authenticate, a `Signature` header on every Xbox token request,
  and a real per-request signature returned by
  `XUserGetTokenAndSignature`. Best-effort: the token is still returned
  if crypto fails.

Caveat: the crypto helpers compile clean with mingw (`-Wall`) but the
full fork is **not built/tested here**. The signing scheme is the
standard proof-bound user-token flow; if Xbox rejects it a device/SISU
token may be needed (iterate from the build/run loop).

### Build & use it

```bash
scripts/build-winegdk-proton.sh        # builds ~/Bureau/WineGDK + overlays
                                       # onto a copy of the stock GDK-Proton
bedrock-on-linux config --proton-dir ~/.local/share/bedrock-on-linux/proton/GDK-Proton-xuser
bedrock-on-linux config --native-login on && bedrock-on-linux login && bedrock-on-linux play
```

The script automates the mechanical steps but the Wine build + GDK-Proton
overlay is **not verified by this repo** — test in-game. A full Wine build
needs `flex bison gcc x86_64-w64-mingw32-gcc` + Wine build-deps.

## Assembly plan (if redoing from scratch)

1. Fork `Weather-OS/WineGDK`.
2. Merge **PR #33** (`olivi-r:master` — XUser) and **PR #37**
   (`olivi-r:xlauncher` — XLauncher).
3. Close the signature gap in
   `dlls/xgameruntime/GDKComponent/System/XUser.c`
   (`XUserGetTokenAndSignatureProvider` currently sets `signature=NULL`):
   implement the Xbox Live request signing (ECDSA P-256 over the documented
   buffer; see gophertunnel `minecraft/auth` `sign()` for the exact byte
   layout). Reference / salvage from `minecraft-linux/mcpelauncher-gdk`
   `xbox-live-dev` (treat its AI-generated parts as untrusted — re-derive).
4. Some Xbox Live connectivity also needs OSS replacement game DLLs from
   `minecraft-linux/mcpelauncher-gdk-dependencies`.

## Build

WineGDK builds like Wine:

```bash
git clone --depth=1 <your-winegdk-fork> && cd WineGDK
./configure            # add --enable-win64 for the 64-bit build Minecraft needs
make -j"$(nproc)"
```

Then package it into a GDK-Proton layout (the `Weather-OS/GDK-Proton`
"Protonified" wrapper: a directory containing a `proton` script and
`files/lib/wine/...`). Easiest is to take an existing GDK-Proton release and
swap its `files/lib/wine` with your freshly built WineGDK tree, keeping the
rest of the Proton wrapper.

## Point the launcher at your build

```bash
# a local directory containing ./proton and ./files/lib/wine/...
bedrock-on-linux config --proton-dir /path/to/your/GDK-Proton
# or an archive URL
bedrock-on-linux config --proton-url https://…/GDK-Proton-xuser.tar.gz
# revert to stock
bedrock-on-linux config --proton-auto
```

(GUI: card ② → *"… custom build (XUser)"* / *"auto"*.)

With a custom build the combase/ntdll binary patches run **non-strict**
(`patch_proton(strict=False)`): a mismatch warns instead of aborting, since a
fork may already handle it or use a different Wine. Then enable native login
(see [`native-login.md`](native-login.md)): `config --native-login on`,
`login`, `play`.

## References

- WineGDK PR #33 / #37 / issue #10 / PR #42:
  `https://github.com/Weather-OS/WineGDK`
- `https://github.com/minecraft-linux/mcpelauncher-gdk` (`xbox-live-dev`)
- `https://github.com/minecraft-linux/mcpelauncher-gdk-dependencies`
- Signing reference: gophertunnel `minecraft/auth`
  (`https://github.com/Sandertv/gophertunnel/tree/master/minecraft/auth`)
