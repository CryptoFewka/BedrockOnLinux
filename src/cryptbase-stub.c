/* cryptbase.dll stub — the RtlGenRandom family (SystemFunction036/040/041)
 * that GDK's OpenSSL TLS RNG resolves through advapi32. GDK-Proton forwards
 * advapi32.SystemFunction036 (RtlGenRandom) to cryptbase, and OpenSSL's Windows
 * RAND aborts at its first TLS RNG draw without it. Built for Wine (x86_64) and
 * installed into the prefix system32 in place of the opaque prebuilt stub, so
 * the OpenSSL-XCurl set has no non-source binary.
 *
 * Build: x86_64-w64-mingw32-gcc -O2 -shared -o cryptbase.dll cryptbase-stub.c -lbcrypt
 */
#include <windows.h>
#include <bcrypt.h>

/* SystemFunction036 == RtlGenRandom: fill the buffer with cryptographic random
 * bytes, sourced from the system-preferred RNG. Returns TRUE on success. */
__declspec(dllexport) BOOLEAN WINAPI
SystemFunction036(PVOID buffer, ULONG length)
{
    if (!buffer && length)
        return FALSE;
    return BCryptGenRandom(NULL, (PUCHAR)buffer, length,
                           BCRYPT_USE_SYSTEM_PREFERRED_RNG) == 0; /* STATUS_SUCCESS */
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
