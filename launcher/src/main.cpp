// main.cpp - tsukihime_launcher.exe: the window, the Play / save data tab, the license, and the tabs of parts 2 and 3.
//
// The launcher goes in the root of the port (next to tools\, framework\, your .xci/.nsp and prod.keys). It needs
// nothing installed: it runs the port's own scripts with Windows PowerShell and starts the compiled game.
// --root <folder> uses another port folder (for testing).
#include "common.h"

#include <commctrl.h>
#include <shellapi.h>
#include <uxtheme.h>

#include <algorithm>
#include <cwctype>

#include "../res/resource.h"
#include "pages.h"
#include "text.h"

AppState g;

namespace {

constexpr UINT_PTR kTimerRefresh = 1;

std::wstring Rel(const std::wstring& full) { return RelPath(full); }

// ---------------------------------------------------------------------------------------------- helpers
std::wstring Size(ULONGLONG bytes) {
    if (bytes >= (1ull << 30)) return Format(L"%.1f GB", double(bytes) / double(1ull << 30));
    if (bytes >= (1ull << 20)) return Format(L"%.0f MB", double(bytes) / double(1ull << 20));
    return Format(L"%llu KB", (bytes + 1023) / 1024);
}

std::vector<std::wstring> ListBackups(int ed) {   // newest first
    std::vector<std::wstring> out;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(BackupDir(ed), L"*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return out;
    do {
        std::wstring n = fd.cFileName;
        if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && n != L"." && n != L"..") out.push_back(n);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    std::sort(out.begin(), out.end(), [](const std::wstring& a, const std::wstring& b) { return a > b; });
    return out;
}

bool GameRunning(int ed) { return IsExeRunning(ExePath(ed)); }

}  // namespace

std::wstring EditionName(int ed) {
    return ed == 0 ? L2(L"Japanese (cartridge)", L"Japonesa (cartucho)") : L2(L"Global (eShop)", L"Global (eShop)");
}

std::wstring RelPath(const std::wstring& full) {
    if (full.size() > g.root.size() && _wcsnicmp(full.c_str(), g.root.c_str(), g.root.size()) == 0 && full[g.root.size()] == L'\\')
        return full.substr(g.root.size() + 1);
    return full;
}

int Message(HWND owner, UINT flags, const std::wstring& text) {
    return MessageBoxW(owner ? owner : g.main, text.c_str(), L2(L"Tsukihime launcher", L"Launcher de Tsukihime"), flags);
}

INT_PTR ColorStatic(HWND dlg, WPARAM wp, LPARAM lp, int id, COLORREF color) {
    HWND ctl = HWND(lp);
    if (GetDlgCtrlID(ctl) != id) return FALSE;
    HDC dc = HDC(wp);
    DrawThemeParentBackground(ctl, dc, nullptr);   // the tab page background (themed) under the text
    SetTextColor(dc, color);
    SetBkMode(dc, TRANSPARENT);
    (void)dlg;
    return INT_PTR(GetStockObject(NULL_BRUSH));
}

// ---------------------------------------------------------------------------------------------- license
namespace {

INT_PTR CALLBACK LicenseProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_INITDIALOG: {
        ApplyDialogText(dlg, IDD_LICENSE);
        SetDlgItemTextW(dlg, IDC_LIC_MS_TEXT,
            L2(L"To compile the game, the C/C++ runtime (CRT) and the SDK of Windows are downloaded from Microsoft with "
               L"xwin into %LOCALAPPDATA%\\tsuki-tools, for your user only. They are never redistributed. Microsoft asks "
               L"you to accept its license terms first (the same ones Visual Studio asks for). Nothing is installed "
               L"system-wide and no administrator is needed.",
               L"Para compilar el juego se descargan de Microsoft, con xwin, el runtime de C/C++ (CRT) y el SDK de Windows "
               L"en %LOCALAPPDATA%\\tsuki-tools, solo para tu usuario. Nunca se redistribuyen. Microsoft pide aceptar antes "
               L"sus t\u00e9rminos de licencia (los mismos que pide Visual Studio). No se instala nada en el sistema ni hace "
               L"falta ser administrador."));
        SetDlgItemTextW(dlg, IDC_LIC_LINK,
            L2(L"<a href=\"https://go.microsoft.com/fwlink/?LinkId=2086102\">Read the Microsoft license terms</a> (opens your browser)",
               L"<a href=\"https://go.microsoft.com/fwlink/?LinkId=2086102\">Leer los t\u00e9rminos de licencia de Microsoft</a> (se abre el navegador)"));
        std::wstring port = ReadTextFile(Join(g.root, L"LICENSE"));
        std::wstring notice = ReadTextFile(Join(g.root, L"NOTICE.md"));
        if (!notice.empty()) port += L"\r\n\r\n---------------- NOTICE.md ----------------\r\n\r\n" + notice;
        if (port.empty()) port = L2(L"(LICENSE and NOTICE.md are not in the port folder)", L"(LICENSE y NOTICE.md no est\u00e1n en la carpeta del port)");
        SetDlgItemTextW(dlg, IDC_LIC_PORT_TEXT, port.c_str());
        return TRUE;
    }
    case WM_NOTIFY: {
        auto* nm = reinterpret_cast<NMHDR*>(lp);
        if (nm->idFrom == IDC_LIC_LINK && (nm->code == NM_CLICK || nm->code == NM_RETURN)) {
            auto* link = reinterpret_cast<NMLINK*>(lp);
            ShellExecuteW(dlg, L"open", link->item.szUrl, nullptr, nullptr, SW_SHOWNORMAL);
            return TRUE;
        }
        break;
    }
    case WM_COMMAND:
        if (LOWORD(wp) == IDC_LIC_ACCEPT) EnableWindow(GetDlgItem(dlg, IDOK), IsDlgButtonChecked(dlg, IDC_LIC_ACCEPT) == BST_CHECKED);
        if (LOWORD(wp) == IDOK && IsDlgButtonChecked(dlg, IDC_LIC_ACCEPT) == BST_CHECKED) EndDialog(dlg, IDOK);
        if (LOWORD(wp) == IDCANCEL) EndDialog(dlg, IDCANCEL);
        return TRUE;
    }
    return FALSE;
}

}  // namespace

bool LicenseAccepted(HWND owner) {
    if (IniGet(L"license_accepted", L"") == L"1") return true;
    if (DialogBoxParamW(g.inst, MAKEINTRESOURCEW(IDD_LICENSE), owner, LicenseProc, 0) != IDOK) return false;
    IniSet(L"license_accepted", L"1");
    return true;
}

// ---------------------------------------------------------------------------------------------- saves
namespace {

struct RestorePick {
    int ed;
    std::vector<std::wstring> items;
    std::wstring chosen;
};

INT_PTR CALLBACK RestoreProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp) {
    auto* pick = reinterpret_cast<RestorePick*>(GetWindowLongPtrW(dlg, DWLP_USER));
    switch (msg) {
    case WM_INITDIALOG: {
        SetWindowLongPtrW(dlg, DWLP_USER, lp);
        pick = reinterpret_cast<RestorePick*>(lp);
        ApplyDialogText(dlg, IDD_RESTORE);
        SetDlgItemTextW(dlg, IDC_RESTORE_TEXT, Format(L2(L"Backups of the %ls edition (newest first). The current saves are backed up before restoring.",
                                                         L"Copias de la edici\u00f3n %ls (la m\u00e1s nueva primero). Las partidas actuales se copian antes de restaurar."),
                                                      EditionName(pick->ed).c_str()).c_str());
        HWND list = GetDlgItem(dlg, IDC_RESTORE_LIST);
        for (const std::wstring& b : pick->items) {
            int files = 0;
            ULONGLONG bytes = 0;
            FILETIME newest{};
            CountTree(Join(BackupDir(pick->ed), b), files, bytes, newest);
            std::wstring line = b + (files == 1 ? std::wstring(L2(L"   (1 file)", L"   (1 archivo)")) : Format(L2(L"   (%d files)", L"   (%d archivos)"), files));
            SendMessageW(list, LB_ADDSTRING, 0, LPARAM(line.c_str()));
        }
        SendMessageW(list, LB_SETCURSEL, 0, 0);
        return TRUE;
    }
    case WM_COMMAND:
        if (LOWORD(wp) == IDOK || (LOWORD(wp) == IDC_RESTORE_LIST && HIWORD(wp) == LBN_DBLCLK)) {
            LRESULT sel = SendDlgItemMessageW(dlg, IDC_RESTORE_LIST, LB_GETCURSEL, 0, 0);
            if (sel == LB_ERR) return TRUE;
            pick->chosen = pick->items[size_t(sel)];
            EndDialog(dlg, IDOK);
        }
        if (LOWORD(wp) == IDCANCEL) EndDialog(dlg, IDCANCEL);
        return TRUE;
    }
    return FALSE;
}

bool HasSaves(int ed) {
    int files = 0;
    ULONGLONG bytes = 0;
    FILETIME newest{};
    CountTree(SaveDir(ed), files, bytes, newest);
    return files > 0;
}

// Copies data\<edition>\user\save to save_backups\<edition>\<date><suffix>. Returns the folder ("" if it failed).
std::wstring BackupSaves(int ed, const wchar_t* suffix, int* files) {
    std::wstring dst = Join(BackupDir(ed), Stamp() + suffix);
    if (!EnsureDir(BackupDir(ed))) return L"";
    if (!CopyTree(SaveDir(ed), dst, files)) {
        DeleteTree(dst);
        return L"";
    }
    return dst;
}

void DoBackup(HWND page) {
    int ed = g.edition;
    if (!HasSaves(ed)) {
        Message(page, MB_ICONINFORMATION, L2(L"There are no saves of this edition yet.", L"Todav\u00eda no hay partidas de esta edici\u00f3n."));
        return;
    }
    int files = 0;
    std::wstring dst = BackupSaves(ed, L"", &files);
    if (dst.empty()) {
        Message(page, MB_ICONERROR, L2(L"The backup could not be made (is the disk full?).", L"No se pudo hacer la copia (\u00bfdisco lleno?)."));
        return;
    }
    Message(page, MB_ICONINFORMATION, Format(L2(L"Saves backed up (%d files) to %ls", L"Partidas copiadas (%d archivos) a %ls"), files, Rel(dst).c_str()));
    RefreshPlay();
}

void DoRestore(HWND page) {
    int ed = g.edition;
    RestorePick pick{ed, ListBackups(ed), L""};
    if (pick.items.empty()) {
        Message(page, MB_ICONINFORMATION, L2(L"There are no backups of this edition yet (\"Back up now\" makes one).",
                                             L"Todav\u00eda no hay copias de esta edici\u00f3n (\"Hacer una copia\" crea una)."));
        return;
    }
    if (GameRunning(ed)) {
        Message(page, MB_ICONWARNING, L2(L"Close the game first.", L"Cierra el juego primero."));
        return;
    }
    if (DialogBoxParamW(g.inst, MAKEINTRESOURCEW(IDD_RESTORE), page, RestoreProc, LPARAM(&pick)) != IDOK) return;
    if (GameRunning(ed)) {
        Message(page, MB_ICONWARNING, L2(L"Close the game first.", L"Cierra el juego primero."));
        return;
    }
    std::wstring save = SaveDir(ed);
    std::wstring safety;
    if (HasSaves(ed)) {   // the current saves are never lost: backed up first
        safety = BackupSaves(ed, L"_before_restore", nullptr);
        if (safety.empty()) {
            Message(page, MB_ICONERROR, L2(L"The current saves could not be backed up: nothing was changed.",
                                           L"No se pudo copiar las partidas actuales: no se cambi\u00f3 nada."));
            return;
        }
    }
    std::wstring aside = save + L".restoring";
    if (DirExists(aside)) DeleteTree(aside);
    bool hadDir = DirExists(save);
    if (hadDir && !MoveFileW(save.c_str(), aside.c_str())) {
        Message(page, MB_ICONERROR, L2(L"The save folder is in use: nothing was changed.", L"La carpeta de partidas est\u00e1 en uso: no se cambi\u00f3 nada."));
        return;
    }
    EnsureDir(Join(DataDir(ed), L"user"));
    int files = 0;
    if (!CopyTree(Join(BackupDir(ed), pick.chosen), save, &files)) {
        DeleteTree(save);
        if (hadDir) MoveFileW(aside.c_str(), save.c_str());
        Message(page, MB_ICONERROR, L2(L"The backup could not be copied: the saves were left as they were.",
                                       L"No se pudo copiar la copia: las partidas quedaron como estaban."));
        return;
    }
    if (hadDir) DeleteTree(aside);
    std::wstring t = Format(L2(L"Restored %ls (files: %d).", L"Restaurada %ls (archivos: %d)."), pick.chosen.c_str(), files);
    if (!safety.empty()) t += Format(L2(L"\nThe previous saves are in %ls", L"\nLas partidas anteriores est\u00e1n en %ls"), Rel(safety).c_str());
    Message(page, MB_ICONINFORMATION, t);
    RefreshPlay();
}

}  // namespace

// ---------------------------------------------------------------------------------------------- Play tab
void RefreshPlay() {
    HWND p = g.pages[kPagePlay];
    if (!p) return;
    int ed = g.edition;
    std::wstring exe = ExePath(ed);
    FILETIME ft{};
    bool built = GetFileTime64(exe, ft);
    bool running = GameRunning(ed);
    bool busy = RunnerBusy();

    SetDlgItemTextW(p, IDC_PLAY_EXE, built ? Format(L2(L"Game: %ls (compiled %ls)", L"Juego: %ls (compilado el %ls)"), Rel(exe).c_str(), FormatFileTime(ft).c_str()).c_str()
                                           : L2(L"Game: not compiled yet.", L"Juego: todav\u00eda no est\u00e1 compilado."));
    std::vector<DumpFile> dumps = FindDumps();
    std::wstring dumpLine;
    bool extracted = DirExists(Join(DataDir(ed), L"extract\\romfs\\shader"));
    if (extracted) dumpLine = L2(L"Game data: extracted in the data folder.", L"Datos del juego: extra\u00eddos en la carpeta de datos.");
    else if (dumps.empty()) dumpLine = L2(L"Game data: no .xci / .nsp in the port folder (the game reads its data from your dump).",
                                          L"Datos del juego: no hay .xci / .nsp en la carpeta del port (el juego lee sus datos del dump).");
    else {
        std::wstring names;
        for (size_t i = 0; i < dumps.size(); i++) names += (i ? L", " : L"") + dumps[i].name;
        dumpLine = Format(L2(L"Game data: read from your dump (%ls).", L"Datos del juego: se leen de tu dump (%ls)."), names.c_str());
    }
    SetDlgItemTextW(p, IDC_PLAY_DUMP, dumpLine.c_str());

    std::wstring state;
    if (running) state = L2(L"The game is running.", L"El juego est\u00e1 abierto.");
    else if (busy) state = L2(L"Compiling: wait until it finishes (Install and recompile tab).", L"Compilando: espera a que termine (pesta\u00f1a Instalar y recompilar).");
    else if (!built) state = L2(L"Compile it first in the Install and recompile tab.", L"Primero comp\u00edlalo en la pesta\u00f1a Instalar y recompilar.");
    else if (!GameDataReady(ed)) state = L2(L"The build has no game data table: recompile this edition.", L"A la compilaci\u00f3n le falta la tabla de datos del juego: recompila esta edici\u00f3n.");
    else if (!extracted && dumps.empty()) state = L2(L"Put your dump back in the port folder to play.", L"Vuelve a poner tu dump en la carpeta del port para jugar.");
    else state = L2(L"Ready to play.", L"Listo para jugar.");
    SetDlgItemTextW(p, IDC_PLAY_STATE, state.c_str());
    SetDlgItemTextW(p, IDC_PLAY_RUNNING, L"");
    EnableWindow(GetDlgItem(g.main, IDOK), built && !running && !busy);

    // save data
    std::wstring save = SaveDir(ed);
    SetDlgItemTextW(p, IDC_SAVE_PATH, Format(L2(L"Folder: %ls", L"Carpeta: %ls"), Rel(save).c_str()).c_str());
    int files = 0;
    ULONGLONG bytes = 0;
    FILETIME newest{};
    CountTree(save, files, bytes, newest);
    std::wstring info = L2(L"No saves yet.", L"Todav\u00eda no hay partidas.");
    if (files == 1) info = Format(L2(L"1 file (%ls), last saved %ls", L"1 archivo (%ls), guardado por \u00faltima vez el %ls"), Size(bytes).c_str(), FormatFileTime(newest).c_str());
    else if (files) info = Format(L2(L"%d files (%ls), last saved %ls", L"%d archivos (%ls), guardado por \u00faltima vez el %ls"), files, Size(bytes).c_str(), FormatFileTime(newest).c_str());
    SetDlgItemTextW(p, IDC_SAVE_INFO, info.c_str());
    std::vector<std::wstring> backups = ListBackups(ed);
    SetDlgItemTextW(p, IDC_SAVE_BACKUPS, backups.empty() ? Format(L2(L"Backups: none yet (they go to %ls)", L"Copias: ninguna todav\u00eda (van a %ls)"), Rel(BackupDir(ed)).c_str()).c_str()
                                                         : Format(L2(L"Backups: %d in %ls (newest %ls)", L"Copias: %d en %ls (la m\u00e1s nueva %ls)"),
                                                                  int(backups.size()), Rel(BackupDir(ed)).c_str(), backups[0].c_str()).c_str());
    EnableWindow(GetDlgItem(p, IDC_SAVE_RESTORE), !backups.empty() && !running);
    EnableWindow(GetDlgItem(p, IDC_SAVE_BACKUP), files > 0);
    EnableWindow(GetDlgItem(p, IDC_PLAY_LOG), FileExists(Join(DataDir(ed), L"nx.log")));
}

INT_PTR CALLBACK PlayProc(HWND dlg, UINT msg, WPARAM wp, LPARAM) {
    switch (msg) {
    case WM_INITDIALOG: {
        EnableThemeDialogTexture(dlg, ETDT_ENABLETAB);
        ApplyDialogText(dlg, IDD_PLAY);
        HWND combo = GetDlgItem(dlg, IDC_PLAY_EDITION);
        for (int ed = 0; ed < 2; ed++) SendMessageW(combo, CB_ADDSTRING, 0, LPARAM(EditionName(ed).c_str()));
        SendMessageW(combo, CB_SETCURSEL, g.edition, 0);
        CheckDlgButton(dlg, IDC_PLAY_CONSOLE, IniGet(L"show_console", L"0") == L"1" ? BST_CHECKED : BST_UNCHECKED);
        CheckDlgButton(dlg, IDC_PLAY_CLOSE, IniGet(L"close_on_play", L"0") == L"1" ? BST_CHECKED : BST_UNCHECKED);
        return TRUE;
    }
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case IDC_PLAY_EDITION:
            if (HIWORD(wp) == CBN_SELCHANGE) {
                g.edition = int(SendDlgItemMessageW(dlg, IDC_PLAY_EDITION, CB_GETCURSEL, 0, 0));
                IniSet(L"edition", kEditions[g.edition].key);
                RefreshAll();
            }
            return TRUE;
        case IDC_PLAY_CONSOLE: IniSet(L"show_console", IsDlgButtonChecked(dlg, IDC_PLAY_CONSOLE) == BST_CHECKED ? L"1" : L"0"); return TRUE;
        case IDC_PLAY_CLOSE: IniSet(L"close_on_play", IsDlgButtonChecked(dlg, IDC_PLAY_CLOSE) == BST_CHECKED ? L"1" : L"0"); return TRUE;
        case IDC_PLAY_LOG: OpenInShell(dlg, Join(DataDir(g.edition), L"nx.log")); return TRUE;
        case IDC_PLAY_DATA:
            if (DirExists(DataDir(g.edition))) OpenInShell(dlg, DataDir(g.edition));
            else Message(dlg, MB_ICONINFORMATION, L2(L"The data folder appears when the edition is compiled.", L"La carpeta de datos aparece al compilar la edici\u00f3n."));
            return TRUE;
        case IDC_PLAY_GOINSTALL: PreselectInstall(g.edition); ShowPage(kPageInstall); return TRUE;
        case IDC_SAVE_OPEN:
            if (DirExists(SaveDir(g.edition))) OpenInShell(dlg, SaveDir(g.edition));
            else Message(dlg, MB_ICONINFORMATION, L2(L"There are no saves of this edition yet.", L"Todav\u00eda no hay partidas de esta edici\u00f3n."));
            return TRUE;
        case IDC_SAVE_BACKUP: DoBackup(dlg); return TRUE;
        case IDC_SAVE_RESTORE: DoRestore(dlg); return TRUE;
        case IDC_SAVE_OPENBACKUPS:
            EnsureDir(BackupDir(g.edition));
            OpenInShell(dlg, BackupDir(g.edition));
            return TRUE;
        }
        break;
    }
    return FALSE;
}

// ---------------------------------------------------------------------------------------------- the game
namespace {

DWORD WINAPI GameWaiter(void* p) {
    HANDLE h = HANDLE(p);
    WaitForSingleObject(h, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(h, &code);
    if (g.main) PostMessageW(g.main, WM_APP_GAME_EXIT, code, 0);
    return 0;
}

}  // namespace

void PlayGame() {
    int ed = g.edition;
    std::wstring exe = ExePath(ed);
    if (!FileExists(exe)) {
        Message(g.main, MB_ICONINFORMATION, L2(L"This edition is not compiled yet. Compile it in the Install and recompile tab.",
                                               L"Esta edici\u00f3n todav\u00eda no est\u00e1 compilada. Comp\u00edlala en la pesta\u00f1a Instalar y recompilar."));
        PreselectInstall(ed);
        ShowPage(kPageInstall);
        return;
    }
    if (RunnerBusy()) return;
    if (GameRunning(ed)) {
        Message(g.main, MB_ICONINFORMATION, L2(L"The game is already running.", L"El juego ya est\u00e1 abierto."));
        return;
    }
    bool extracted = DirExists(Join(DataDir(ed), L"extract\\romfs\\shader"));
    if (!extracted && FindDumps().empty() &&
        Message(g.main, MB_ICONWARNING | MB_YESNO, L2(L"There is no .xci / .nsp in the port folder: the game reads its data from your dump and will say \"Game data not found\". Start it anyway?",
                                                      L"No hay .xci / .nsp en la carpeta del port: el juego lee sus datos de tu dump y dir\u00e1 \"Game data not found\". \u00bfIniciarlo igual?")) != IDYES)
        return;

    bool console = IniGet(L"show_console", L"0") == L"1";
    std::wstring dir = exe.substr(0, exe.find_last_of(L'\\'));
    std::wstring cmd = Quoted(exe);
    STARTUPINFOW si{};
    si.cb = sizeof si;
    PROCESS_INFORMATION pi{};
    // NX_CONSOLE=1: the console of the exe stays (consola_tsuki.cpp); it is read before main(), so it goes by environment.
    if (console) SetEnvironmentVariableW(L"NX_CONSOLE", L"1");
    BOOL ok = CreateProcessW(exe.c_str(), &cmd[0], nullptr, nullptr, FALSE, console ? CREATE_NEW_CONSOLE : CREATE_NO_WINDOW,
                             nullptr, dir.c_str(), &si, &pi);
    DWORD err = GetLastError();
    if (console) SetEnvironmentVariableW(L"NX_CONSOLE", nullptr);
    if (!ok) {
        Message(g.main, MB_ICONERROR, Format(L2(L"The game could not be started (error %lu).", L"No se pudo iniciar el juego (error %lu)."), err));
        return;
    }
    CloseHandle(pi.hThread);
    if (g.game) CloseHandle(g.game);
    g.game = pi.hProcess;
    HANDLE dup = nullptr;
    DuplicateHandle(GetCurrentProcess(), pi.hProcess, GetCurrentProcess(), &dup, SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, 0);
    if (dup) CloseHandle(CreateThread(nullptr, 0, GameWaiter, dup, 0, nullptr));
    if (IniGet(L"close_on_play", L"0") == L"1") {
        DestroyWindow(g.main);
        return;
    }
    RefreshPlay();
}

void OnGameExit(DWORD code) {
    if (g.game) {
        CloseHandle(g.game);
        g.game = nullptr;
    }
    RefreshAll();
    std::wstring t;
    switch (code) {
    case 0: return;   // closed normally (also with the X)
    case 4: t = L2(L"The game stopped because the GPU stopped responding (code 4).", L"El juego se detuvo porque la GPU dej\u00f3 de responder (c\u00f3digo 4)."); break;
    case 5: t = L2(L"Your graphics driver does not support Vulkan 1.3 (code 5). Update the graphics driver and try again.",
                   L"Tu driver de video no soporta Vulkan 1.3 (c\u00f3digo 5). Actualiza el driver de video y vuelve a intentarlo."); break;
    case 6: t = L2(L"Vulkan is not available on this PC (code 6). The game needs a GPU with a Vulkan driver (a virtual machine usually has none).",
                   L"Vulkan no est\u00e1 disponible en este PC (c\u00f3digo 6). El juego necesita una GPU con driver Vulkan (una m\u00e1quina virtual normalmente no tiene)."); break;
    default:
        t = Format(L2(L"The game closed unexpectedly (code 0x%08lX).", L"El juego se cerr\u00f3 de forma inesperada (c\u00f3digo 0x%08lX)."), code);
    }
    std::wstring log = Join(DataDir(g.edition), L"nx.log");
    if (FileExists(log)) {
        t += L2(L"\n\nOpen nx.log to see what happened?", L"\n\n\u00bfAbrir nx.log para ver qu\u00e9 pas\u00f3?");
        if (Message(g.main, MB_ICONWARNING | MB_YESNO, t) == IDYES) OpenInShell(g.main, log);
    } else {
        Message(g.main, MB_ICONWARNING, t);
    }
}

// ---------------------------------------------------------------------------------------------- tabs of parts 2 and 3
INT_PTR CALLBACK ModsProc(HWND dlg, UINT msg, WPARAM wp, LPARAM) {
    switch (msg) {
    case WM_INITDIALOG:
        EnableThemeDialogTexture(dlg, ETDT_ENABLETAB);
        ApplyDialogText(dlg, IDD_MODS);
        SetDlgItemTextW(dlg, IDC_MODS_TEXT,
            L2(L"Managing mods and updates from here comes in the next version of the launcher. Meanwhile:\n\n"
               L"- Updates and translation patches (.nsp): put them in the updates folder and recompile the edition.\n"
               L"- Loose mods: copy them to mods\\<edition>\\<any name>\\romfs\\ (no need to recompile). A folder whose name "
               L"starts with _ is turned off.",
               L"Gestionar mods y actualizaciones desde aqu\u00ed llega en la pr\u00f3xima versi\u00f3n del launcher. Mientras tanto:\n\n"
               L"- Actualizaciones y parches de traducci\u00f3n (.nsp): ponlos en la carpeta updates y recompila la edici\u00f3n.\n"
               L"- Mods sueltos: c\u00f3pialos a mods\\<edici\u00f3n>\\<cualquier nombre>\\romfs\\ (sin recompilar). Una carpeta cuyo "
               L"nombre empieza con _ queda desactivada."));
        return TRUE;
    case WM_COMMAND:
        if (LOWORD(wp) == IDC_MODS_OPEN) {
            std::wstring d = Join(g.root, std::wstring(L"mods\\") + kEditions[g.edition].key);
            EnsureDir(d);
            OpenInShell(dlg, d);
        }
        if (LOWORD(wp) == IDC_MODS_UPDATES) {
            std::wstring d = Join(g.root, L"updates");
            EnsureDir(d);
            OpenInShell(dlg, d);
        }
        return TRUE;
    }
    return FALSE;
}

INT_PTR CALLBACK ControlsProc(HWND dlg, UINT msg, WPARAM wp, LPARAM) {
    switch (msg) {
    case WM_INITDIALOG:
        EnableThemeDialogTexture(dlg, ETDT_ENABLETAB);
        ApplyDialogText(dlg, IDD_CONTROLS);
        SetDlgItemTextW(dlg, IDC_CTL_TEXT,
            L2(L"Editing the keyboard, mouse and controller (input.ini) and the title screen (title.ini) from here comes in a "
               L"later version of the launcher. Meanwhile: copy input.ini.example as input.ini and title.ini.example as "
               L"title.ini into the data folder of the edition and edit them; each file explains its options.",
               L"Editar el teclado, el mouse y el mando (input.ini) y la pantalla de t\u00edtulo (title.ini) desde aqu\u00ed llega en "
               L"una versi\u00f3n posterior del launcher. Mientras tanto: copia input.ini.example como input.ini y title.ini.example "
               L"como title.ini en la carpeta de datos de la edici\u00f3n y ed\u00edtalos; cada archivo explica sus opciones."));
        return TRUE;
    case WM_COMMAND:
        if (LOWORD(wp) == IDC_CTL_OPENDATA) {
            if (DirExists(DataDir(g.edition))) OpenInShell(dlg, DataDir(g.edition));
            else Message(dlg, MB_ICONINFORMATION, L2(L"The data folder appears when the edition is compiled.", L"La carpeta de datos aparece al compilar la edici\u00f3n."));
        }
        if (LOWORD(wp) == IDC_CTL_EXAMPLES) SelectInExplorer(Join(g.root, L"framework\\games\\tsukihime_common\\input.ini.example"));
        return TRUE;
    }
    return FALSE;
}

// ---------------------------------------------------------------------------------------------- main window
void ShowPage(int page) {
    TabCtrl_SetCurSel(g.tab, page);
    for (int i = 0; i < 4; i++) ShowWindow(g.pages[i], i == page ? SW_SHOW : SW_HIDE);
}

void RefreshAll() {
    RefreshPlay();
    RefreshInstall();
}

namespace {

void PlacePages(HWND dlg) {
    RECT r;
    GetWindowRect(g.tab, &r);
    TabCtrl_AdjustRect(g.tab, FALSE, &r);
    MapWindowPoints(nullptr, dlg, reinterpret_cast<POINT*>(&r), 2);
    for (HWND p : g.pages) SetWindowPos(p, HWND_TOP, r.left, r.top, r.right - r.left, r.bottom - r.top, SWP_NOACTIVATE);
}

INT_PTR CALLBACK MainProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_INITDIALOG: {
        g.main = dlg;
        ApplyDialogText(dlg, IDD_MAIN);
        HICON big = HICON(LoadImageW(g.inst, MAKEINTRESOURCEW(IDI_LAUNCHER), IMAGE_ICON, GetSystemMetrics(SM_CXICON), GetSystemMetrics(SM_CYICON), 0));
        HICON small = HICON(LoadImageW(g.inst, MAKEINTRESOURCEW(IDI_LAUNCHER), IMAGE_ICON, GetSystemMetrics(SM_CXSMICON), GetSystemMetrics(SM_CYSMICON), 0));
        SendMessageW(dlg, WM_SETICON, ICON_BIG, LPARAM(big));
        SendMessageW(dlg, WM_SETICON, ICON_SMALL, LPARAM(small));
        g.tab = GetDlgItem(dlg, IDC_TAB);
        const wchar_t* names[4] = {L2(L"Play and saves", L"Jugar y partidas"), L2(L"Install and recompile", L"Instalar y recompilar"),
                                   L2(L"Mods and updates", L"Mods y actualizaciones"), L2(L"Controls and title screen", L"Controles y t\u00edtulo")};
        for (int i = 0; i < 4; i++) {
            TCITEMW it{};
            it.mask = TCIF_TEXT;
            it.pszText = const_cast<wchar_t*>(names[i]);
            TabCtrl_InsertItem(g.tab, i, &it);
        }
        const int ids[4] = {IDD_PLAY, IDD_INSTALL, IDD_MODS, IDD_CONTROLS};
        DLGPROC procs[4] = {PlayProc, InstallProc, ModsProc, ControlsProc};
        for (int i = 0; i < 4; i++) g.pages[i] = CreateDialogParamW(g.inst, MAKEINTRESOURCEW(ids[i]), dlg, procs[i], 0);
        PlacePages(dlg);
        SetDlgItemTextW(dlg, IDC_FOOTER, Format(L2(L"Port folder: %ls", L"Carpeta del port: %ls"), g.root.c_str()).c_str());
        RefreshAll();
        ShowPage(FileExists(ExePath(g.edition)) ? kPagePlay : kPageInstall);
        SetTimer(dlg, kTimerRefresh, 3000, nullptr);
        return TRUE;
    }
    case WM_NOTIFY: {
        auto* nm = reinterpret_cast<NMHDR*>(lp);
        if (nm->idFrom == IDC_TAB && nm->code == TCN_SELCHANGE) {
            ShowPage(TabCtrl_GetCurSel(g.tab));
            RefreshAll();
        }
        break;
    }
    case WM_ACTIVATE:
        if (LOWORD(wp) != WA_INACTIVE && g.pages[0]) RefreshAll();   // files may have been copied in the meantime
        break;
    case WM_TIMER:
        if (wp == kTimerRefresh && !RunnerBusy()) RefreshPlay();   // the game started or closed outside the launcher
        return TRUE;
    case WM_APP_GAME_EXIT:
        OnGameExit(DWORD(wp));
        return TRUE;
    case WM_COMMAND:
        if (LOWORD(wp) == IDOK) { PlayGame(); return TRUE; }
        if (LOWORD(wp) == IDCANCEL) { SendMessageW(dlg, WM_CLOSE, 0, 0); return TRUE; }
        break;
    case WM_CLOSE:
        if (RunnerBusy()) {
            if (Message(dlg, MB_ICONWARNING | MB_YESNO, L2(L"A compilation is running. Stop it and close? Running it again later resumes where it stopped.",
                                                          L"Hay una compilaci\u00f3n en curso. \u00bfDetenerla y cerrar? Si la vuelves a lanzar, sigue donde qued\u00f3.")) != IDYES)
                return TRUE;
            RunnerCancel();
        }
        DestroyWindow(dlg);
        return TRUE;
    case WM_DESTROY:
        KillTimer(dlg, kTimerRefresh);
        g.main = nullptr;
        PostQuitMessage(0);
        return TRUE;
    }
    return FALSE;
}

std::wstring FullPath(const std::wstring& p) {
    wchar_t buf[MAX_PATH * 4];
    DWORD n = GetFullPathNameW(p.c_str(), MAX_PATH * 4, buf, nullptr);
    std::wstring s = (n && n < MAX_PATH * 4) ? std::wstring(buf, n) : p;
    while (s.size() > 3 && (s.back() == L'\\' || s.back() == L'/')) s.pop_back();
    return s;
}

}  // namespace

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, PWSTR, int) {
    g.inst = inst;
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    for (int i = 1; argv && i + 1 < argc; i++)
        if (wcscmp(argv[i], L"--root") == 0) g.root = FullPath(argv[i + 1]);
    if (argv) LocalFree(argv);
    if (g.root.empty()) {
        wchar_t self[MAX_PATH * 4];
        DWORD n = GetModuleFileNameW(nullptr, self, MAX_PATH * 4);
        std::wstring s(self, n);
        g.root = s.substr(0, s.find_last_of(L'\\'));
    }
    g.iniPath = Join(g.root, L"launcher.ini");
    g.wine = IsWine();
    if (g.wine && IniGet(L"wine_runs_scripts", L"0") == L"1") g.wine = false;   // testing only: a Wine prefix with a PowerShell
    DetectLanguage();

    if (!ValidRoot(g.root)) {
        // Only the exe (a GitHub release asset) is here: offer to download the port next to it (fetch.cpp).
        INITCOMMONCONTROLSEX early{sizeof early, ICC_PROGRESS_CLASS | ICC_LINK_CLASS | ICC_STANDARD_CLASSES};
        InitCommonControlsEx(&early);
        if (!DownloadPort() || !ValidRoot(g.root)) return 0;
    }

    // One launcher per port folder: a second one would fight over the scripts and the saves.
    std::wstring mutexName = L"Local\\tsukihime_launcher_";
    for (wchar_t c : g.root) mutexName += (c == L'\\' || c == L':') ? L'_' : wchar_t(towlower(c));
    HANDLE single = CreateMutexW(nullptr, TRUE, mutexName.c_str());
    if (single && GetLastError() == ERROR_ALREADY_EXISTS) {
        MessageBoxW(nullptr, L2(L"The launcher is already open for this folder.", L"El launcher ya est\u00e1 abierto para esta carpeta."), L"Tsukihime launcher", MB_ICONINFORMATION);
        return 0;
    }

    INITCOMMONCONTROLSEX icc{sizeof icc, ICC_TAB_CLASSES | ICC_PROGRESS_CLASS | ICC_LINK_CLASS | ICC_STANDARD_CLASSES};
    InitCommonControlsEx(&icc);
    g.edition = IniGet(L"edition", L"japan") == L"global" ? 1 : 0;

    HWND dlg = CreateDialogParamW(inst, MAKEINTRESOURCEW(IDD_MAIN), nullptr, MainProc, 0);
    if (!dlg) return 1;
    ShowWindow(dlg, SW_SHOW);
    MSG m;
    while (GetMessageW(&m, nullptr, 0, 0) > 0) {
        if (g.main && IsDialogMessageW(g.main, &m)) continue;
        TranslateMessage(&m);
        DispatchMessageW(&m);
    }
    if (single) CloseHandle(single);
    return 0;
}
