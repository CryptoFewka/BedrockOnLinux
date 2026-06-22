/* bol injector — load a client .dll into the running Minecraft via the classic
 * CreateRemoteThread + LoadLibraryW technique, so BedrockOnLinux can inject
 * directly without any third-party injector .exe. Built for Wine (x86_64) and
 * run inside the game's own Wine prefix so it shares the wineserver and can see
 * Minecraft.Windows.exe. Returns 0 on success.
 *
 * Build: x86_64-w64-mingw32-gcc -O2 -municode -s injector.c -o ../bol/injector.exe
 * Usage: injector.exe <dll-path> [process.exe]   (default Minecraft.Windows.exe)
 */
#include <windows.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <wchar.h>

static DWORD find_pid(const wchar_t *name)
{
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return 0;
    PROCESSENTRY32W pe;
    pe.dwSize = sizeof(pe);
    DWORD pid = 0;
    if (Process32FirstW(snap, &pe))
        do {
            if (!_wcsicmp(pe.szExeFile, name)) { pid = pe.th32ProcessID; break; }
        } while (Process32NextW(snap, &pe));
    CloseHandle(snap);
    return pid;
}

int wmain(int argc, wchar_t **argv)
{
    if (argc < 2) { fwprintf(stderr, L"usage: injector <dll> [process.exe]\n"); return 2; }
    const wchar_t *dll  = argv[1];
    const wchar_t *proc = argc > 2 ? argv[2] : L"Minecraft.Windows.exe";

    DWORD pid = find_pid(proc);
    if (!pid) { fwprintf(stderr, L"ERR process not found: %ls\n", proc); return 3; }

    HANDLE h = OpenProcess(PROCESS_CREATE_THREAD | PROCESS_VM_OPERATION |
                           PROCESS_VM_WRITE | PROCESS_VM_READ |
                           PROCESS_QUERY_INFORMATION, FALSE, pid);
    if (!h) { fwprintf(stderr, L"ERR OpenProcess: %lu\n", GetLastError()); return 4; }

    SIZE_T n = (wcslen(dll) + 1) * sizeof(wchar_t);
    void *rem = VirtualAllocEx(h, NULL, n, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!rem || !WriteProcessMemory(h, rem, dll, n, NULL)) {
        fwprintf(stderr, L"ERR write process memory: %lu\n", GetLastError());
        CloseHandle(h); return 5;
    }

    /* kernel32 is mapped at the same address in every Wine process, so the
     * target can call our LoadLibraryW pointer directly. */
    HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
    FARPROC loadlib = GetProcAddress(k32, "LoadLibraryW");
    HANDLE th = CreateRemoteThread(h, NULL, 0, (LPTHREAD_START_ROUTINE)loadlib,
                                   rem, 0, NULL);
    if (!th) {
        fwprintf(stderr, L"ERR CreateRemoteThread: %lu\n", GetLastError());
        VirtualFreeEx(h, rem, 0, MEM_RELEASE); CloseHandle(h); return 6;
    }

    WaitForSingleObject(th, 15000);
    DWORD mod = 0;                       /* low 32 bits of the loaded HMODULE */
    GetExitCodeThread(th, &mod);
    VirtualFreeEx(h, rem, 0, MEM_RELEASE);
    CloseHandle(th);
    CloseHandle(h);

    if (!mod) {
        fwprintf(stderr, L"ERR LoadLibrary returned 0 (bad DLL / 32-bit / missing "
                         L"deps): %ls\n", dll);
        return 7;
    }
    fwprintf(stderr, L"OK injected %ls into %ls (pid %lu)\n", dll, proc, pid);
    return 0;
}
