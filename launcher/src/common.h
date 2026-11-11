// common.h - shared declarations of tsukihime_launcher.exe (Win32, no dependencies besides Windows itself).
//
// The launcher lives in the root of the port (next to tools\, framework\, the dump and prod.keys). It only reads the
// port's files and runs its scripts (tools\install_requirements.ps1, tools\recomp_tsukihime.ps1) and the compiled
// game; it contains no code or data of the game.
#pragma once

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <cstdint>
#include <string>
#include <vector>

// ---------------------------------------------------------------------------------------------- editions
struct Edition {
    const wchar_t* key;   // -Edition of the scripts: japan / global
    const wchar_t* game;  // exe and build folder: tsukihime / tsukihime_global
    const wchar_t* data;  // data root under data\: tsuki / tsuki_global
};
extern const Edition kEditions[2];

// ---------------------------------------------------------------------------------------------- util.cpp
std::wstring Join(const std::wstring& a, const std::wstring& b);
bool FileExists(const std::wstring& p);
bool DirExists(const std::wstring& p);
bool GetFileTime64(const std::wstring& p, FILETIME& ft);
std::wstring FormatFileTime(const FILETIME& ft);           // local time, yyyy-mm-dd hh:mm
std::wstring Stamp();                                       // yyyy-mm-dd_hhmmss (local)
std::wstring FormatElapsed(ULONGLONG ms);                   // mm:ss or h:mm:ss
std::wstring Utf8ToWide(const std::string& s);
std::string WideToUtf8(const std::wstring& s);
std::wstring ReadTextFile(const std::wstring& p, size_t maxBytes = 1 << 20);   // UTF-8 (BOM ok) -> CRLF text
bool EnsureDir(const std::wstring& p);                      // creates every missing level
bool CopyTree(const std::wstring& from, const std::wstring& to, int* files);   // to must not exist yet
bool DeleteTree(const std::wstring& p);
void CountTree(const std::wstring& p, int& files, ULONGLONG& bytes, FILETIME& newest);
void OpenInShell(HWND owner, const std::wstring& path);    // folder or file with its default program
void SelectInExplorer(const std::wstring& path);
bool IsExeRunning(const std::wstring& exePath);             // any process whose image is that file
bool IsWine();
std::wstring Format(const wchar_t* fmt, ...);
std::wstring GetDlgText(HWND dlg, int id);
bool CopyToClipboard(HWND owner, const std::wstring& text);
// Standard Open dialog (IFileOpenDialog): a folder, or a file (filterSpec like L"*.nsp", nullptr = any). "" = cancelled.
std::wstring PickPath(HWND owner, const wchar_t* title, bool folder, const wchar_t* filterName = nullptr, const wchar_t* filterSpec = nullptr);
bool ToRecycleBin(HWND owner, const std::wstring& path);   // file or folder; false if it is still there

// launcher.ini (root of the port; never versioned: the .gitignore keeps the root out)
std::wstring IniGet(const wchar_t* key, const wchar_t* def);
void IniSet(const wchar_t* key, const std::wstring& value);

// ---------------------------------------------------------------------------------------------- status.cpp
struct DumpFile {
    std::wstring name;   // file name in the root
    ULONGLONG size = 0;
    bool nsp = false;
};
std::vector<DumpFile> FindDumps();                          // .xci (HEAD at 0x100) / .nsp (PFS0) in the root
enum class KeysAt { None, Data, Root };
KeysAt FindKeys();
bool ToolsReady(std::wstring* detail);                      // venv + toolchain.json (xwin) or dev.local.ps1
std::wstring ExePath(int ed);                               // framework\build\<game>\<game>.exe
std::wstring DataDir(int ed);                               // data\<data>
bool GameDataReady(int ed);                                 // romfs_source.bin (read from the dump) or extract\romfs
std::wstring SaveDir(int ed);                               // data\<data>\user\save
std::wstring BackupDir(int ed);                             // save_backups\<edition>
std::wstring PowerShellExe();

// ---------------------------------------------------------------------------------------------- fetch.cpp
bool ValidRoot(const std::wstring& r);                      // tools\*.ps1 + framework\ are in r
bool DownloadPort();
bool Sha256File(const std::wstring& path, std::wstring& hex);   // lowercase hex                                        // asks, then downloads + verifies + unpacks the port into g.root; true when done

// ---------------------------------------------------------------------------------------------- runner.cpp
// Runs one command line hidden, with stdout+stderr in a pipe and the whole process tree in a job (Cancel kills it).
// Each line goes to the owner window as WM_APP_LINE (lParam = new std::wstring, the receiver deletes it); at the end
// WM_APP_DONE (wParam = exit code, lParam = 1 if cancelled).
constexpr UINT WM_APP_LINE = WM_APP + 1;
constexpr UINT WM_APP_DONE = WM_APP + 2;
constexpr UINT WM_APP_GAME_EXIT = WM_APP + 3;   // wParam = exit code of the game

bool RunnerStart(HWND owner, const std::wstring& cmdline, const std::wstring& cwd, const std::wstring& logFile);
void RunnerCancel();
bool RunnerBusy();

// ---------------------------------------------------------------------------------------------- globals (main.cpp)
struct AppState {
    HINSTANCE inst = nullptr;
    std::wstring root;        // folder of the port (no trailing backslash)
    std::wstring iniPath;     // <root>\launcher.ini
    bool wine = false;        // running under Wine / Proton
    HWND main = nullptr;
    HWND tab = nullptr;
    HWND pages[4] = {};
    int edition = 0;          // selected in the Play tab
    HANDLE game = nullptr;    // the game started from here (still running)
};
extern AppState g;

std::wstring Quoted(const std::wstring& arg);   // command-line quoting (CommandLineToArgvW rules)
