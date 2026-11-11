// status.cpp - what the launcher reads from the port folder: dumps, keys, tools, builds, data and saves.
// The rules are the scripts' ones (recomp_tsukihime.ps1: FindInputs, MoveRootKeys; install_requirements.ps1:
// toolchain.json); nothing here reads prod.keys or the content of a dump beyond its container header.
#include "common.h"

#include <cstring>

const Edition kEditions[2] = {
    {L"japan", L"tsukihime", L"tsuki"},
    {L"global", L"tsukihime_global", L"tsuki_global"},
};

static bool HeaderIs(const std::wstring& path, ULONGLONG at, const char* magic) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER pos;
    pos.QuadPart = LONGLONG(at);
    char buf[4] = {};
    DWORD got = 0;
    bool ok = SetFilePointerEx(h, pos, nullptr, FILE_BEGIN) && ReadFile(h, buf, 4, &got, nullptr) && got == 4 &&
              memcmp(buf, magic, 4) == 0;
    CloseHandle(h);
    return ok;
}

std::vector<DumpFile> FindDumps() {
    std::vector<DumpFile> out;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(g.root, L"*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return out;
    do {
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        std::wstring n = fd.cFileName;
        if (n.size() < 5) continue;
        std::wstring ext = n.substr(n.size() - 4);
        bool nsp = _wcsicmp(ext.c_str(), L".nsp") == 0, xci = _wcsicmp(ext.c_str(), L".xci") == 0;
        if (!nsp && !xci) continue;
        std::wstring full = Join(g.root, n);
        if (nsp ? !HeaderIs(full, 0, "PFS0") : !HeaderIs(full, 0x100, "HEAD")) continue;   // same check as ContainerType
        DumpFile d;
        d.name = n;
        d.size = (ULONGLONG(fd.nFileSizeHigh) << 32) | fd.nFileSizeLow;
        d.nsp = nsp;
        out.push_back(d);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return out;
}

KeysAt FindKeys() {
    if (FileExists(Join(g.root, L"data\\prod.keys"))) return KeysAt::Data;
    if (FileExists(Join(g.root, L"prod.keys"))) return KeysAt::Root;   // recomp moves it to data\ (never copies or reads it)
    return KeysAt::None;
}

bool ToolsReady(std::wstring* detail) {
    bool venv = FileExists(Join(g.root, L"framework\\private\\venv\\Scripts\\python.exe"));
    bool xwin = FileExists(Join(g.root, L"_prepare\\toolchain.json"));
    bool local = FileExists(Join(g.root, L"tools\\dev.local.ps1")) || FileExists(Join(g.root, L"framework\\dev.local.ps1"));
    if (detail) *detail = xwin ? L"xwin" : local ? L"dev.local.ps1" : L"";
    return venv && (xwin || local);
}

std::wstring ExePath(int ed) {
    std::wstring game = kEditions[ed].game;
    return Join(g.root, L"framework\\build\\" + game + L"\\" + game + L".exe");
}

std::wstring DataDir(int ed) { return Join(g.root, std::wstring(L"data\\") + kEditions[ed].data); }

bool GameDataReady(int ed) {
    return FileExists(Join(DataDir(ed), L"romfs_source.bin")) || DirExists(Join(DataDir(ed), L"extract\\romfs\\shader"));
}

std::wstring SaveDir(int ed) { return Join(DataDir(ed), L"user\\save"); }

std::wstring BackupDir(int ed) { return Join(g.root, std::wstring(L"save_backups\\") + kEditions[ed].key); }

std::wstring PowerShellExe() {
    wchar_t sys[MAX_PATH];
    UINT n = GetSystemDirectoryW(sys, MAX_PATH);
    std::wstring s = (n && n < MAX_PATH) ? std::wstring(sys, n) : std::wstring(L"C:\\Windows\\System32");
    return Join(s, L"WindowsPowerShell\\v1.0\\powershell.exe");
}
