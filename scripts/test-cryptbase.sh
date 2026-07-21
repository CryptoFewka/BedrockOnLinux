#!/usr/bin/env bash
# Wine runtime test for the from-source cryptbase stub (src/cryptbase-stub.c).
#
# Compiles the stub, loads it as the native cryptbase in a throwaway Wine prefix,
# and exercises SystemFunction036 (RtlGenRandom) end to end:
#   - it must return TRUE and fill the buffer with non-trivial random bytes,
#   - across sizes up to 4 KiB (a size that would blow a recursive stack), and
#   - two independent draws must differ,
#   - with no "stack overflow" from Wine (the advapi32->cryptbase RtlGenRandom
#     recursion this stub is written to avoid).
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
int main(void)
{
    HMODULE h = LoadLibraryA("cryptbase.dll");
    if (!h) { printf("FAIL: cannot load cryptbase.dll\n"); return 3; }
    RG rg = (RG)GetProcAddress(h, "SystemFunction036");
    if (!rg) { printf("FAIL: no SystemFunction036 export\n"); return 2; }

    /* Fill a range of sizes, including one large enough that a recursive RNG
     * would overflow the stack before returning. */
    for (unsigned long n = 1; n <= 4096; n <<= 2) {
        unsigned char buf[4096];
        memset(buf, 0, n);
        if (!rg(buf, n)) { printf("FAIL: returned FALSE for len=%lu\n", n); return 1; }
        unsigned long nz = 0;
        for (unsigned long i = 0; i < n; i++) nz += (buf[i] != 0);
        if (n >= 16 && nz == 0) { printf("FAIL: all-zero output for len=%lu\n", n); return 1; }
    }

    /* Two independent draws must differ (not a constant/counter). */
    unsigned char a[64], b[64];
    if (!rg(a, sizeof a) || !rg(b, sizeof b)) { printf("FAIL: draw returned FALSE\n"); return 1; }
    if (memcmp(a, b, sizeof a) == 0) { printf("FAIL: two draws identical\n"); return 1; }

    printf("PASS: SystemFunction036 returns varied random bytes\n");
    return 0;
}
C
"$CC" -O2 -o "$WORK/selftest.exe" "$WORK/selftest.c"

echo "== Running under Wine with the native cryptbase"
export WINEPREFIX="$WORK/pfx" WINEARCH=win64 WINEDLLOVERRIDES="cryptbase=n,b" \
       WINEDEBUG="err+virtual,fixme-none" DISPLAY=
wineboot -i >/dev/null 2>&1
wineserver -w
cp "$WORK/cryptbase.dll" "$WINEPREFIX/drive_c/windows/system32/cryptbase.dll"

out="$(wine "$WORK/selftest.exe" 2> "$WORK/err" || true)"
echo "$out"
if grep -q 'stack overflow' "$WORK/err"; then
  echo "!! Wine reported a stack overflow: SystemFunction036 is recursing" >&2
  sed -n '1,20p' "$WORK/err" >&2
  exit 1
fi
case "$out" in
  *"PASS: SystemFunction036"*) echo "== cryptbase RNG test passed" ;;
  *) echo "!! cryptbase RNG test failed" >&2; sed -n '1,20p' "$WORK/err" >&2; exit 1 ;;
esac
