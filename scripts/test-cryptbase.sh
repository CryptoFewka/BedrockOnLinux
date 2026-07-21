#!/usr/bin/env bash
# Wine runtime test for the from-source cryptbase stub (src/cryptbase-stub.c).
#
# It initializes a throwaway prefix with the builtin cryptbase first, then runs
# the RNG check with WINEDLLOVERRIDES=cryptbase=n (native ONLY, no builtin
# fallback): if the native stub fails to load, LoadLibrary returns NULL and the
# test fails instead of silently passing on Wine's builtin. SystemFunction036
# (RtlGenRandom) must:
#   - load from the native cryptbase.dll and return non-trivial random bytes,
#   - across sizes up to 4 KiB (a size that would blow a recursive stack),
#   - with two independent draws differing,
#   - and resolve + work through advapi32.dll too (the module OpenSSL's RAND
#     queries; under the GDK-Proton engine advapi32.SystemFunction036 forwards
#     to this cryptbase, under stock Wine advapi32 serves it directly),
#   - with no "stack overflow" from Wine and a zero process exit status.
#
# Requires: x86_64-w64-mingw32-gcc, wine (headless). Exits non-zero on failure.
set -Eeuo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CC="${CC:-x86_64-w64-mingw32-gcc}"
for t in "$CC" wine; do
  command -v "$t" >/dev/null 2>&1 || { echo "!! missing required tool: $t" >&2; exit 2; }
done

WORK="$(mktemp -d)"
trap 'wineserver -k 2>/dev/null || true; rm -rf "$WORK"' EXIT

echo "== Building cryptbase.dll from source"
"$CC" -O2 -mrdrnd -shared -o "$WORK/cryptbase.dll" "$SRC/src/cryptbase-stub.c"

cat > "$WORK/selftest.c" <<'C'
#include <windows.h>
#include <stdio.h>
#include <string.h>
typedef BOOLEAN (WINAPI *RG)(PVOID, ULONG);

/* Fill a range of sizes, including one large enough that a recursive RNG would
 * overflow the stack before returning, and require two draws to differ. */
static int exercise(RG rg, const char *who)
{
    for (unsigned long n = 1; n <= 4096; n <<= 2) {
        unsigned char buf[4096];
        memset(buf, 0, n);
        if (!rg(buf, n)) { printf("FAIL(%s): returned FALSE for len=%lu\n", who, n); return 1; }
        unsigned long nz = 0;
        for (unsigned long i = 0; i < n; i++) nz += (buf[i] != 0);
        if (n >= 16 && nz == 0) { printf("FAIL(%s): all-zero output for len=%lu\n", who, n); return 1; }
    }
    unsigned char a[64], b[64];
    if (!rg(a, sizeof a) || !rg(b, sizeof b)) { printf("FAIL(%s): draw returned FALSE\n", who); return 1; }
    if (memcmp(a, b, sizeof a) == 0) { printf("FAIL(%s): two draws identical\n", who); return 1; }
    return 0;
}

int main(void)
{
    /* cryptbase=n means native-only: a NULL here is our stub failing to load,
     * not a fallback to Wine's builtin, so fail loudly. */
    HMODULE cb = LoadLibraryA("cryptbase.dll");
    if (!cb) { printf("FAIL: native cryptbase.dll did not load\n"); return 2; }
    RG cbrg = (RG)GetProcAddress(cb, "SystemFunction036");
    if (!cbrg) { printf("FAIL: no SystemFunction036 export in cryptbase\n"); return 3; }
    if (exercise(cbrg, "cryptbase")) return 1;

    /* The module OpenSSL's RAND resolves RtlGenRandom from. */
    HMODULE ad = LoadLibraryA("advapi32.dll");
    RG adrg = (RG)GetProcAddress(ad, "SystemFunction036");
    if (!adrg) { printf("FAIL: no SystemFunction036 via advapi32\n"); return 4; }
    if (exercise(adrg, "advapi32")) return 1;

    printf("PASS: SystemFunction036 returns varied random bytes\n");
    return 0;
}
C
"$CC" -O2 -o "$WORK/selftest.exe" "$WORK/selftest.c"

echo "== Initializing the prefix with the builtin cryptbase"
export WINEPREFIX="$WORK/pfx" WINEARCH=win64 \
       WINEDEBUG="err+virtual,fixme-none" DISPLAY=
wineboot -i >/dev/null 2>&1
wineserver -w
cp "$WORK/cryptbase.dll" "$WINEPREFIX/drive_c/windows/system32/cryptbase.dll"

echo "== Running the RNG check with cryptbase=n (native only)"
set +e
WINEDLLOVERRIDES="cryptbase=n" wine "$WORK/selftest.exe" \
  > "$WORK/out" 2> "$WORK/err"
status=$?
set -e
cat "$WORK/out"

if grep -q 'stack overflow' "$WORK/err"; then
  echo "!! Wine reported a stack overflow: SystemFunction036 is recursing" >&2
  sed -n '1,20p' "$WORK/err" >&2
  exit 1
fi
if [ "$status" -ne 0 ]; then
  echo "!! selftest.exe exited with status $status" >&2
  sed -n '1,20p' "$WORK/err" >&2
  exit 1
fi
echo "== cryptbase RNG test passed"
