// util.cpp - files, text, time, shell and process helpers of the launcher.
#include "common.h"

#include <shellapi.h>
#include <shlobj.h>
#include <shobjidl.h>
#include <tlhelp32.h>

#include <cstdarg>
#include <cstring>
#include <cwchar>

std::wstring Join(const std::wstring& a, const std::wstring& b) {
    if (a.empty()) return b;
    if (a.back() == L'\\' || a.back() == L'/') return a + b;
    return a + L"\\" + b;
}

bool FileExists(const std::wstring& p) {
    DWORD a = GetFileAttributesW(p.c_str());
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

bool DirExists(const std::wstring& p) {
    DWORD a = GetFileAttributesW(p.c_str());
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}

bool GetFileTime64(const std::wstring& p, FILETIME& ft) {
    WIN32_FILE_ATTRIBUTE_DATA d;
    if (!GetFileAttributesExW(p.c_str(), GetFileExInfoStandard, &d)) return false;
    ft = d.ftLastWriteTime;
    return true;
}

std::wstring FormatFileTime(const FILETIME& ft) {
    FILETIME local;
    SYSTEMTIME st;
    if (!FileTimeToLocalFileTime(&ft, &local) || !FileTimeToSystemTime(&local, &st)) return L"?";
    return Format(L"%04u-%02u-%02u %02u:%02u", st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute);
}

std::wstring Stamp() {
    SYSTEMTIME st;
    GetLocalTime(&st);
    return Format(L"%04u-%02u-%02u_%02u%02u%02u", st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond);
}

std::wstring FormatElapsed(ULONGLONG ms) {
    ULONGLONG s = ms / 1000;
    if (s >= 3600) return Format(L"%llu:%02llu:%02llu", s / 3600, (s / 60) % 60, s % 60);
    return Format(L"%02llu:%02llu", s / 60, s % 60);
}

std::wstring Utf8ToWide(const std::string& s) {
    if (s.empty()) return L"";
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), nullptr, 0);
    std::wstring w(size_t(n), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), &w[0], n);
    return w;
}

std::string WideToUtf8(const std::wstring& s) {
    if (s.empty()) return "";
    int n = WideCharToMultiByte(CP_UTF8, 0, s.data(), int(s.size()), nullptr, 0, nullptr, nullptr);
    std::string a(size_t(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, s.data(), int(s.size()), &a[0], n, nullptr, nullptr);
    return a;
}

std::wstring ReadTextFile(const std::wstring& p, size_t maxBytes) {
    HANDLE h = CreateFileW(p.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr);
    if (h == INVALID_HANDLE_VALUE) return L"";
    std::string data;
    char buf[65536];
    DWORD got = 0;
    while (data.size() < maxBytes && ReadFile(h, buf, sizeof buf, &got, nullptr) && got) data.append(buf, got);
    CloseHandle(h);
    if (data.size() >= 3 && (unsigned char)data[0] == 0xEF && (unsigned char)data[1] == 0xBB && (unsigned char)data[2] == 0xBF)
        data.erase(0, 3);
    std::wstring w = Utf8ToWide(data), out;   // EDIT controls want CRLF
    out.reserve(w.size() + w.size() / 16);
    for (size_t i = 0; i < w.size(); i++) {
        if (w[i] == L'\n' && (i == 0 || w[i - 1] != L'\r')) out += L'\r';
        out += w[i];
    }
    return out;
}

bool EnsureDir(const std::wstring& p) {
    if (p.empty() || DirExists(p)) return true;
    size_t cut = p.find_last_of(L"\\/");
    if (cut != std::wstring::npos && cut > 2 && !EnsureDir(p.substr(0, cut))) return false;
    return CreateDirectoryW(p.c_str(), nullptr) || GetLastError() == ERROR_ALREADY_EXISTS;
}

bool CopyTree(const std::wstring& from, const std::wstring& to, int* files) {
    if (!CreateDirectoryW(to.c_str(), nullptr)) return false;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(from, L"*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return true;   // empty
    bool ok = true;
    do {
        std::wstring n = fd.cFileName;
        if (n == L"." || n == L"..") continue;
        std::wstring a = Join(from, n), b = Join(to, n);
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            ok = CopyTree(a, b, files);
        } else {
            ok = CopyFileW(a.c_str(), b.c_str(), TRUE) != 0;
            if (ok && files) (*files)++;
        }
    } while (ok && FindNextFileW(h, &fd));
    FindClose(h);
    return ok;
}

bool DeleteTree(const std::wstring& p) {
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(p, L"*").c_str(), &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            std::wstring n = fd.cFileName;
            if (n == L"." || n == L"..") continue;
            std::wstring a = Join(p, n);
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                DeleteTree(a);
            } else {
                if (fd.dwFileAttributes & FILE_ATTRIBUTE_READONLY) SetFileAttributesW(a.c_str(), FILE_ATTRIBUTE_NORMAL);
                DeleteFileW(a.c_str());
            }
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    return RemoveDirectoryW(p.c_str()) != 0;
}

void CountTree(const std::wstring& p, int& files, ULONGLONG& bytes, FILETIME& newest) {
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(p, L"*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        std::wstring n = fd.cFileName;
        if (n == L"." || n == L"..") continue;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            CountTree(Join(p, n), files, bytes, newest);
        } else {
            files++;
            bytes += (ULONGLONG(fd.nFileSizeHigh) << 32) | fd.nFileSizeLow;
            if (CompareFileTime(&fd.ftLastWriteTime, &newest) > 0) newest = fd.ftLastWriteTime;
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
}

std::wstring PickPath(HWND owner, const wchar_t* title, bool folder, const wchar_t* filterName, const wchar_t* filterSpec) {
    // The GUIDs are written here so the exe needs no uuid.lib.
    static const GUID kClsid = {0xDC1C5A9C, 0xE88A, 0x4DDE, {0xA5, 0xA1, 0x60, 0xF8, 0x2A, 0x20, 0xAE, 0xF7}};   // CLSID_FileOpenDialog
    static const GUID kIid = {0xD57C7288, 0xD4AD, 0x4768, {0xBE, 0x02, 0x9D, 0x96, 0x95, 0x32, 0xD9, 0x60}};     // IID_IFileOpenDialog
    std::wstring out;
    HRESULT init = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    IFileOpenDialog* dlg = nullptr;
    if (SUCCEEDED(CoCreateInstance(kClsid, nullptr, CLSCTX_INPROC_SERVER, kIid, reinterpret_cast<void**>(&dlg))) && dlg) {
        DWORD opts = 0;
        dlg->GetOptions(&opts);
        opts |= FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST;
        opts |= folder ? FOS_PICKFOLDERS : FOS_FILEMUSTEXIST;
        dlg->SetOptions(opts);
        dlg->SetTitle(title);
        if (!folder && filterSpec) {
            COMDLG_FILTERSPEC spec[2] = {{filterName ? filterName : filterSpec, filterSpec}, {L"*.*", L"*.*"}};
            dlg->SetFileTypes(2, spec);
        }
        if (SUCCEEDED(dlg->Show(owner))) {
            IShellItem* item = nullptr;
            if (SUCCEEDED(dlg->GetResult(&item)) && item) {
                PWSTR path = nullptr;
                if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path)) && path) {
                    out = path;
                    CoTaskMemFree(path);
                }
                item->Release();
            }
        }
        dlg->Release();
    }
    if (SUCCEEDED(init)) CoUninitialize();
    while (folder && out.size() > 3 && out.back() == L'\\') out.pop_back();
    return out;
}

bool ToRecycleBin(HWND owner, const std::wstring& path) {
    std::wstring from = path;
    from.push_back(L'\0');   // the list ends with two NULs
    SHFILEOPSTRUCTW op{};
    op.hwnd = owner;
    op.wFunc = FO_DELETE;
    op.pFrom = from.c_str();
    // To the Recycle Bin; Windows asks before deleting for good (too big for the bin, or a drive without one).
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_WANTNUKEWARNING | FOF_NOERRORUI;
    SHFileOperationW(&op);
    return GetFileAttributesW(path.c_str()) == INVALID_FILE_ATTRIBUTES;
}

void OpenInShell(HWND owner, const std::wstring& path) {
    ShellExecuteW(owner, L"open", path.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
}

void SelectInExplorer(const std::wstring& path) {
    std::wstring args = L"/select," + Quoted(path);
    ShellExecuteW(nullptr, L"open", L"explorer.exe", args.c_str(), nullptr, SW_SHOWNORMAL);
}

static std::wstring ImagePathOf(DWORD pid) {
    std::wstring out;
    HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!h) return out;
    wchar_t buf[MAX_PATH * 2];
    DWORD n = MAX_PATH * 2;
    if (QueryFullProcessImageNameW(h, 0, buf, &n)) out.assign(buf, n);
    CloseHandle(h);
    return out;
}

bool IsExeRunning(const std::wstring& exePath) {
    const wchar_t* base = wcsrchr(exePath.c_str(), L'\\');
    base = base ? base + 1 : exePath.c_str();
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return false;
    PROCESSENTRY32W pe;
    pe.dwSize = sizeof pe;
    bool found = false;
    for (BOOL ok = Process32FirstW(snap, &pe); ok && !found; ok = Process32NextW(snap, &pe)) {
        if (_wcsicmp(pe.szExeFile, base) != 0) continue;
        std::wstring img = ImagePathOf(pe.th32ProcessID);
        found = img.empty() || _wcsicmp(img.c_str(), exePath.c_str()) == 0;   // no access to its path: assume it is ours
    }
    CloseHandle(snap);
    return found;
}

bool IsWine() {
    HMODULE nt = GetModuleHandleW(L"ntdll.dll");
    return nt && GetProcAddress(nt, "wine_get_version") != nullptr;
}

std::wstring Format(const wchar_t* fmt, ...) {
    wchar_t buf[2048];
    va_list ap;
    va_start(ap, fmt);
    int n = _vsnwprintf(buf, 2047, fmt, ap);
    va_end(ap);
    buf[2047] = 0;
    if (n < 0) n = int(wcslen(buf));
    return std::wstring(buf, size_t(n));
}

std::wstring GetDlgText(HWND dlg, int id) {
    HWND c = GetDlgItem(dlg, id);
    int n = GetWindowTextLengthW(c);
    std::wstring s(size_t(n) + 1, L'\0');
    GetWindowTextW(c, &s[0], n + 1);
    s.resize(size_t(n));
    return s;
}

bool CopyToClipboard(HWND owner, const std::wstring& text) {
    if (!OpenClipboard(owner)) return false;
    EmptyClipboard();
    size_t bytes = (text.size() + 1) * sizeof(wchar_t);
    HGLOBAL mem = GlobalAlloc(GMEM_MOVEABLE, bytes);
    bool ok = false;
    if (mem) {
        void* p = GlobalLock(mem);
        memcpy(p, text.c_str(), bytes);
        GlobalUnlock(mem);
        ok = SetClipboardData(CF_UNICODETEXT, mem) != nullptr;
        if (!ok) GlobalFree(mem);
    }
    CloseClipboard();
    return ok;
}

std::wstring IniGet(const wchar_t* key, const wchar_t* def) {
    wchar_t buf[1024];
    GetPrivateProfileStringW(L"launcher", key, def, buf, 1024, g.iniPath.c_str());
    return buf;
}

void IniSet(const wchar_t* key, const std::wstring& value) {
    WritePrivateProfileStringW(L"launcher", key, value.c_str(), g.iniPath.c_str());
}

// CommandLineToArgvW rules: backslashes are literal unless they precede a quote; a trailing run is doubled.
std::wstring Quoted(const std::wstring& arg) {
    if (!arg.empty() && arg.find_first_of(L" \t\"") == std::wstring::npos) return arg;
    std::wstring out = L"\"";
    size_t slashes = 0;
    for (wchar_t c : arg) {
        if (c == L'\\') { slashes++; continue; }
        if (c == L'"') { out.append(slashes * 2 + 1, L'\\'); out += L'"'; slashes = 0; continue; }
        out.append(slashes, L'\\');
        slashes = 0;
        out += c;
    }
    out.append(slashes * 2, L'\\');
    out += L'"';
    return out;
}
