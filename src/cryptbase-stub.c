/* cryptbase.dll stub: RtlGenRandom (SystemFunction036), the one export GDK's
 * OpenSSL TLS RNG resolves through advapi32. GDK-Proton forwards
 * advapi32.SystemFunction036 (RtlGenRandom) to cryptbase, and OpenSSL's Windows
 * RAND aborts at its first TLS RNG draw without it. Built for Wine (x86_64) and
 * installed into the prefix system32 in place of the opaque prebuilt stub, so
 * the OpenSSL-XCurl set has no non-source binary.
 *
 * IMPORTANT: the RNG must be self-contained. Sourcing it from bcrypt's
 * BCryptGenRandom recurses to a stack overflow under Wine: advapi32.
 * SystemFunction036 forwards to THIS cryptbase.SystemFunction036, our impl calls
 * BCryptGenRandom, and Wine's bcrypt seeds its DRBG via RtlGenRandom, which is
 * advapi32.SystemFunction036 again -> back into this stub -> unbounded recursion.
 * So draw entropy without touching advapi32/bcrypt: the host CSPRNG via Wine's
 * unix path (/dev/urandom) as the primary, RDRAND as the fallback. cryptbase
 * imports only kernel32/msvcrt.
 *
 * Build: x86_64-w64-mingw32-gcc -O2 -mrdrnd -shared -o cryptbase.dll cryptbase-stub.c
 */
#include <windows.h>
#include <immintrin.h>
#include <intrin.h>
#include <string.h>

/* Primary: read the host CSPRNG through Wine's unix path. This DLL only ever
 * runs under Wine, where "\\?\unix\dev\urandom" maps to the host /dev/urandom --
 * cryptographically strong on every CPU, exactly what Wine's builtin cryptbase
 * uses. Imports only kernel32, so no advapi32/bcrypt loop. */
static int read_dev_urandom(unsigned char *p, ULONG len)
{
    HANDLE h = CreateFileW(L"\\\\?\\unix\\dev\\urandom", GENERIC_READ,
                           FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                           OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE)
        return 0;
    ULONG total = 0;
    int ok = 1;
    while (total < len) {
        DWORD got = 0;
        if (!ReadFile(h, p + total, len - total, &got, NULL) || got == 0) {
            ok = 0;
            break;
        }
        total += got;
    }
    CloseHandle(h);
    return ok;
}

/* Fallback: RDRAND. CPUID.01H:ECX.RDRAND[bit 30] -- the instruction faults (#UD)
 * on a CPU without it, so gate on the feature bit first. Self-contained; no loop. */
static int have_rdrand(void)
{
    int regs[4];
    __cpuid(regs, 1);
    return (regs[2] >> 30) & 1;
}

static int fill_rdrand(unsigned char *p, ULONG len)
{
    if (!have_rdrand())
        return 0;
    ULONG i = 0;
    while (i < len) {
        unsigned long long r;
        int ok = 0;
        for (int t = 0; t < 32 && !ok; t++)
            ok = _rdrand64_step(&r);
        if (!ok)
            return 0;
        ULONG n = (len - i < sizeof(r)) ? (len - i) : (ULONG)sizeof(r);
        memcpy(p + i, &r, n);
        i += n;
    }
    return 1;
}

/* SystemFunction036 == RtlGenRandom: fill the buffer with cryptographic random
 * bytes. Fail closed (FALSE) rather than return weak/predictable bytes if
 * neither strong source is available. Returns TRUE on success. */
__declspec(dllexport) BOOLEAN WINAPI
SystemFunction036(PVOID buffer, ULONG length)
{
    if (!buffer && length)
        return FALSE;
    unsigned char *p = (unsigned char *)buffer;
    if (read_dev_urandom(p, length))
        return TRUE;
    if (fill_rdrand(p, length))
        return TRUE;
    return FALSE;
}

/* SystemFunction040/041 (RtlEncryptMemory/RtlDecryptMemory) are intentionally
 * not exported: the GDK/OpenSSL TLS path only needs the RNG above, and a no-op
 * that returns success without encrypting/decrypting is worse than an honest
 * "not found". If a caller ever needs them, add a real implementation. */
