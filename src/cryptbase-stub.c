/* cryptbase.dll stub — the RtlGenRandom family (SystemFunction036/040/041)
 * that GDK's OpenSSL TLS RNG resolves through advapi32. GDK-Proton forwards
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
 * So draw entropy from the RDRAND hardware instruction (with a CPUID guard and a
 * self-seeded fallback), importing only kernel32/msvcrt -- never advapi32/bcrypt.
 *
 * Build: x86_64-w64-mingw32-gcc -O2 -mrdrnd -shared -o cryptbase.dll cryptbase-stub.c
 */
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0600   /* GetTickCount64 (Vista+) */
#endif
#include <windows.h>
#include <immintrin.h>
#include <intrin.h>
#include <stdint.h>
#include <string.h>

/* CPUID.01H:ECX.RDRAND[bit 30]. Executing rdrand on a CPU without it faults
 * (#UD), so gate on the feature bit before ever issuing the instruction. */
static int have_rdrand(void)
{
    int regs[4];
    __cpuid(regs, 1);
    return (regs[2] >> 30) & 1;
}

static int rdrand64(unsigned long long *out)
{
    for (int i = 0; i < 32; i++)
        if (_rdrand64_step(out))
            return 1;
    return 0;
}

/* Self-contained fallback for the rare CPU without RDRAND: a splitmix64 stream
 * seeded from process/time/address state. Only kernel32 imports, no advapi32
 * loop. Best-effort, not a hardware RNG -- RDRAND is the real path. */
static unsigned long long seed_state(void)
{
    LARGE_INTEGER pc;
    QueryPerformanceCounter(&pc);
    unsigned long long s = (unsigned long long)pc.QuadPart;
    s ^= (unsigned long long)GetTickCount64() * 0x9E3779B97F4A7C15ULL;
    s ^= (unsigned long long)GetCurrentProcessId() << 32;
    s ^= (unsigned long long)GetCurrentThreadId();
    s ^= (unsigned long long)(uintptr_t)&pc;
    return s ? s : 0x1234567890ABCDEFULL;
}

static unsigned long long splitmix64(unsigned long long *st)
{
    unsigned long long z = (*st += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

/* SystemFunction036 == RtlGenRandom: fill the buffer with random bytes.
 * Returns TRUE on success. */
__declspec(dllexport) BOOLEAN WINAPI
SystemFunction036(PVOID buffer, ULONG length)
{
    if (!buffer && length)
        return FALSE;
    unsigned char *p = (unsigned char *)buffer;
    int hw = have_rdrand();
    unsigned long long st = hw ? 0 : seed_state();
    ULONG i = 0;
    while (i < length) {
        unsigned long long r;
        if (hw) {
            if (!rdrand64(&r)) {        /* transient exhaustion: drop to fallback */
                hw = 0;
                st = seed_state();
                r = splitmix64(&st);
            }
        } else {
            r = splitmix64(&st);
        }
        ULONG n = (length - i < sizeof(r)) ? (length - i) : (ULONG)sizeof(r);
        memcpy(p + i, &r, n);
        i += n;
    }
    return TRUE;
}

/* SystemFunction040 == RtlEncryptMemory, SystemFunction041 == RtlDecryptMemory.
 * The GDK/OpenSSL TLS path only needs the RNG above; provide success-returning
 * pass-throughs so any incidental caller keeps working. */
__declspec(dllexport) LONG WINAPI
SystemFunction040(PVOID memory, ULONG length, ULONG flags)
{
    (void)memory; (void)length; (void)flags;
    return 0; /* STATUS_SUCCESS */
}

__declspec(dllexport) LONG WINAPI
SystemFunction041(PVOID memory, ULONG length, ULONG flags)
{
    (void)memory; (void)length; (void)flags;
    return 0; /* STATUS_SUCCESS */
}
