// android.cpp - the "Android" sub-tab of Install and recompile (AP_LAUNCHER_ANDROID,
// tools\patches\apply_launcher_android.py), and that tab itself: an inner tab control with "Windows" (install.cpp,
// IDD_INSTALL) and "Android" (IDD_ANDROID). Android prepares the phone after the game is recompiled in the Windows sub-tab.
//
// - Runs tools\android\android_port.ps1 -Edition <edition> once per ticked edition: the game's library for Android
//   (the Android pack + the LLVM clang of the PC install; no Android Studio, no NDK), the folder for the phone in
//   _android\<edition>\phone and, when "Send" is not "Do not send", adb to the phone (only what changed) and the app.
// - Progress from the lines the script prints: "== android: <part>" starts a part, "[n/N]" (Ninja, the compile) and
//   "adb: N% ..." (the send) move the bar inside it, "ERROR in ..." is the result when it fails.
// - The first send asks once (USB debugging; adb, Google's Android SDK Platform-Tools, is downloaded into the tools
//   folder if this PC has none): launcher.ini adb_ok=1. Choices kept in launcher.ini: android_send, android_mods.
// - Under Wine / Proton nothing runs from here: the sub-tab shows the Linux commands (tools/android/android_port.sh).
// - The sub-tab last shown is kept in launcher.ini (install_sub).
#include "common.h"

#include <commctrl.h>
#include <uxtheme.h>

#include <algorithm>

#include "../res/resource.h"
#include "pages.h"
#include "text.h"

namespace {

constexpr UINT_PTR kTimerElapsed = 3;

struct Task {
    int ed = 0;
    std::vector<std::wstring> args;   // after -File tools\android\android_port.ps1
    int parts = 2;                    // its "== android:" parts (3 when it sends: the app)
};

struct State {
    HWND dlg = nullptr;
    std::vector<Task> tasks;
    size_t current = 0;
    ULONGLONG started = 0;
    int part = 0, sub = 0;
    std::wstring label, error, result;
    COLORREF resultColor = 0;
    HFONT mono = nullptr;
    bool sent = false;
} s;

const wchar_t* kScript = L"tools\\android\\android_port.ps1";

std::wstring GameOf(int ed) { return kEditions[ed].game; }
std::wstring AndroidDir(int ed) { return Join(g.root, std::wstring(L"_android\\") + kEditions[ed].key); }
std::wstring LibPath(int ed) { return Join(AndroidDir(ed), L"lib" + GameOf(ed) + L".so"); }
std::wstring PhoneDir(int ed) { return Join(AndroidDir(ed), L"phone"); }

// What android_port.ps1 needs from the recompile (steps 0-8 of recomp_tsukihime.ps1).
bool Recompiled(int ed) {
    const std::wstring d = DataDir(ed);
    for (const wchar_t* f : {L"analysis\\main.bin", L"analysis\\sdk.bin", L"romfs_source.bin", L"spirv\\results.tsv",
                             L"fonts\\shared_0.ttf", L"gen\\main\\funcs.h", L"gen\\sdk\\funcs.h"})
        if (!FileExists(Join(d, f))) return false;
    return true;
}

int SendMode() { return int(SendDlgItemMessageW(s.dlg, IDC_AND_SEND, CB_GETCURSEL, 0, 0)); }   // 0 none, 1 internal, 2 SD
bool Checked(int id) { return IsDlgButtonChecked(s.dlg, id) == BST_CHECKED; }

// "version" of a small JSON file (pack.json, android_pack.json): enough for display.
std::wstring JsonValue(const std::wstring& text, const wchar_t* key) {
    std::wstring k = std::wstring(L"\"") + key + L"\"";
    size_t a = text.find(k);
    if (a == std::wstring::npos) return L"";
    a = text.find(L'"', text.find(L':', a + k.size()) + 1);
    if (a == std::wstring::npos) return L"";
    size_t b = text.find(L'"', a + 1);
    return b == std::wstring::npos ? L"" : text.substr(a + 1, b - a - 1);
}

std::vector<Task> BuildTasks() {
    std::vector<Task> out;
    int send = SendMode();
    for (int ed = 0; ed < 2; ed++) {
        if (!Checked(ed == 0 ? IDC_AND_JP : IDC_AND_GL)) continue;
        Task t;
        t.ed = ed;
        t.args = {L"-Edition", kEditions[ed].key};
        if (Checked(IDC_AND_MODS)) t.args.push_back(L"-Mods");
        if (send == 1 || send == 2) {
            t.args.push_back(send == 1 ? L"-InternalStorage" : L"-ExternalStorage");
            if (Checked(IDC_AND_SAVES)) t.args.push_back(L"-Saves");
            if (Checked(IDC_AND_DUMP)) t.args.push_back(L"-Dump");
            t.args.push_back(L"-InstallApp");
            t.args.push_back(L"-GetAdb");
            t.parts = 3;
        }
        t.args.push_back(L"-Root");
        t.args.push_back(g.root);
        out.push_back(t);
    }
    return out;
}

std::wstring CommandLine(const Task& t) {
    std::wstring c = Quoted(PowerShellExe()) + L" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File " +
                     Quoted(Join(g.root, kScript));
    for (const std::wstring& a : t.args) c += L" " + Quoted(a);
    return c;
}

std::wstring PsArg(const std::wstring& v) {   // a value inside a PowerShell single-quoted string
    std::wstring out = L"'";
    for (wchar_t c : v) { out += c; if (c == L'\'') out += L'\''; }
    return out + L"'";
}

void AppendLog(const std::wstring& line) {
    HWND e = GetDlgItem(s.dlg, IDC_AND_LOG);
    int len = GetWindowTextLengthW(e);
    if (len > 1500000) {   // keep the control light (the log file keeps everything)
        SendMessageW(e, EM_SETSEL, 0, len / 2);
        SendMessageW(e, EM_REPLACESEL, FALSE, LPARAM(L"[...]\r\n"));
        len = GetWindowTextLengthW(e);
    }
    std::wstring add = line + L"\r\n";
    SendMessageW(e, EM_SETSEL, len, len);
    SendMessageW(e, EM_REPLACESEL, FALSE, LPARAM(add.c_str()));
}

void SetResult(const std::wstring& text, COLORREF color) {
    s.result = text;
    s.resultColor = color;
    SetDlgItemTextW(s.dlg, IDC_AND_RESULT, text.c_str());
    InvalidateRect(GetDlgItem(s.dlg, IDC_AND_RESULT), nullptr, TRUE);
}

void SetProgress() {
    if (s.tasks.empty()) return;
    const Task& t = s.tasks[s.current];
    double inTask = s.part <= 0 ? 0 : std::min((s.part - 1 + s.sub / 100.0) / double(t.parts), 0.99);
    double f = (double(s.current) + inTask) / double(s.tasks.size());
    SendDlgItemMessageW(s.dlg, IDC_AND_PROGRESS, PBM_SETPOS, WPARAM(f * 1000), 0);
}

void ParseLine(const std::wstring& raw) {
    size_t a = raw.find_first_not_of(L' ');
    std::wstring t = a == std::wstring::npos ? L"" : raw.substr(a);
    const std::wstring ed = EditionName(s.tasks[s.current].ed);
    if (t.rfind(L"== android: ", 0) == 0) {
        std::wstring part = t.substr(12);
        if (g_spanish) {
            if (part.rfind(L"game library", 0) == 0) part = L"librer\u00eda del juego";
            else if (part.rfind(L"folder for the phone, sent", 0) == 0) part = L"carpeta del tel\u00e9fono, env\u00edo por USB";
            else if (part.rfind(L"folder for the phone", 0) == 0) part = L"carpeta del tel\u00e9fono";
            else if (part.rfind(L"app on the phone", 0) == 0) part = L"app en el tel\u00e9fono";
        }
        s.part++;
        s.sub = 0;
        s.label = Format(L"%ls - %ls", ed.c_str(), part.c_str());
        SetProgress();
        return;
    }
    int n = 0, of = 0;
    if (t.size() > 1 && t[0] == L'[' && swscanf(t.c_str(), L"[%d/%d]", &n, &of) == 2 && of > 0) {   // Ninja
        s.sub = std::min(100, n * 100 / of);
        SetProgress();
        return;
    }
    if (t.rfind(L"adb: ", 0) == 0 && t.find(L'%') != std::wstring::npos) {
        s.sub = std::min(100, std::max(0, _wtoi(t.c_str() + 5)));
        std::wstring rest = t.substr(5);
        size_t o = rest.find(L" of ");
        if (g_spanish && o != std::wstring::npos) rest.replace(o, 4, L" de ");
        s.label = Format(L"%ls - adb: %ls", ed.c_str(), rest.c_str());
        SetProgress();
        return;
    }
    if (t.rfind(L"ERROR in ", 0) == 0 && s.error.empty()) s.error = t;   // the first one is the precise one
}

void SetBusyUi(bool busy) {
    bool can = !busy && !g.wine && !RunnerBusy();   // not while a PC task runs either
    for (int id : {IDC_AND_START, IDC_AND_CONSOLE}) EnableWindow(GetDlgItem(s.dlg, id), can);
    for (int id : {IDC_AND_JP, IDC_AND_GL, IDC_AND_SEND, IDC_AND_MODS}) EnableWindow(GetDlgItem(s.dlg, id), !busy);
    bool sending = SendMode() > 0;
    for (int id : {IDC_AND_SAVES, IDC_AND_DUMP}) EnableWindow(GetDlgItem(s.dlg, id), !busy && sending);
    EnableWindow(GetDlgItem(s.dlg, IDC_AND_CANCEL), busy);
    if (busy) SetTimer(s.dlg, kTimerElapsed, 1000, nullptr);
    else KillTimer(s.dlg, kTimerElapsed);
}

bool StartCurrent() {
    const Task& t = s.tasks[s.current];
    s.part = 0;
    s.sub = 0;
    s.error.clear();
    std::wstring prep = Join(g.root, L"_prepare");
    EnsureDir(prep);
    std::wstring log = Join(prep, L"launcher_" + Stamp() + L"_android_" + kEditions[t.ed].key + L".log");
    std::wstring cmd = CommandLine(t);
    AppendLog(L"> " + cmd);
    if (!RunnerStart(s.dlg, cmd, g.root, log)) {
        AppendLog(Format(L2(L"[launcher] PowerShell could not be started (error %lu).", L"[launcher] No se pudo iniciar PowerShell (error %lu)."), GetLastError()));
        return false;
    }
    return true;
}

void Finish(bool ok, const std::wstring& text) {
    SetBusyUi(false);
    SetResult(text, ok ? RGB(0, 110, 0) : RGB(190, 0, 0));
    if (ok) {
        SendDlgItemMessageW(s.dlg, IDC_AND_PROGRESS, PBM_SETPOS, 1000, 0);
        SetDlgItemTextW(s.dlg, IDC_AND_STEP, L2(L"Finished.", L"Terminado."));
    } else {
        SendDlgItemMessageW(s.dlg, IDC_AND_PROGRESS, PBM_SETSTATE, PBST_ERROR, 0);
    }
    RefreshAll();
    FLASHWINFO fi{sizeof fi, g.main, FLASHW_ALL | FLASHW_TIMERNOFG, 3, 0};
    FlashWindowEx(&fi);
}

void OnDone(DWORD code, bool cancelled) {
    if (cancelled) {
        Finish(false, L2(L"Cancelled. Starting it again only does what is missing.", L"Cancelado. Si lo vuelves a iniciar, solo hace lo que falta."));
        return;
    }
    if (code != 0) {
        Finish(false, s.error.empty() ? Format(L2(L"Failed (exit code %lu): see the log.", L"Fall\u00f3 (c\u00f3digo %lu): mira el log."), code) : s.error);
        return;
    }
    if (++s.current < s.tasks.size()) {
        AppendLog(L"");
        if (!StartCurrent()) Finish(false, L2(L"PowerShell could not be started.", L"No se pudo iniciar PowerShell."));
        return;
    }
    std::wstring elapsed = FormatElapsed(GetTickCount64() - s.started);
    if (s.sent)
        Finish(true, Format(L2(L"Done in %ls. On the phone: open Tsukihime, choose the port folder (the first time) and Play.",
                               L"Listo en %ls. En el tel\u00e9fono: abre Tsukihime, elige la carpeta del port (la primera vez) y Jugar."), elapsed.c_str()));
    else
        Finish(true, Format(L2(L"Done in %ls. Copy the contents of the phone folder to the phone and choose that folder in the app.",
                               L"Listo en %ls. Copia el contenido de la carpeta del tel\u00e9fono al tel\u00e9fono y elige esa carpeta en la app."), elapsed.c_str()));
}

std::wstring EditionLower(int ed) {   // "japonesa" inside a Spanish sentence ("Japanese" stays as it is)
    std::wstring n = EditionName(ed);
    if (g_spanish && !n.empty()) n[0] = towlower(n[0]);
    return n;
}

// The ticked editions must be recompiled; otherwise offer the Windows sub-tab.
bool CheckReady() {
    bool jp = Checked(IDC_AND_JP), gl = Checked(IDC_AND_GL);
    if (!jp && !gl) {
        Message(s.dlg, MB_ICONINFORMATION, L2(L"Tick the edition(s) to prepare.", L"Marca la(s) edici\u00f3n(es) que quieres preparar."));
        return false;
    }
    for (int ed = 0; ed < 2; ed++) {
        if (!(ed == 0 ? jp : gl) || Recompiled(ed)) continue;
        if (Message(s.dlg, MB_ICONINFORMATION | MB_YESNO,
                    Format(L2(L"The %ls edition is not recompiled on this PC yet: Android uses the same recompile. Go to the Windows sub-tab to do it?",
                              L"La edici\u00f3n %ls todav\u00eda no est\u00e1 recompilada en este PC: Android usa esa misma recompilaci\u00f3n. \u00bfIr a la subpesta\u00f1a Windows para hacerlo?"),
                           EditionLower(ed).c_str())) == IDYES) {
            PreselectInstall(ed);   // also shows the Windows sub-tab
        }
        return false;
    }
    if (!ToolsReady(nullptr)) {
        Message(s.dlg, MB_ICONINFORMATION, L2(L"The tools are not installed yet: install them in the Windows sub-tab first (Android uses its compiler).",
                                              L"Las herramientas no est\u00e1n instaladas: inst\u00e1lalas primero en la subpesta\u00f1a Windows (Android usa su compilador)."));
        return false;
    }
    if (SendMode() > 0 && IniGet(L"adb_ok", L"0") != L"1") {
        if (Message(s.dlg, MB_ICONQUESTION | MB_YESNO,
                    L2(L"To send the folder to the phone by USB:\n\n"
                       L"- Turn on USB debugging on the phone (Settings > About phone > tap Build number 7 times; then "
                       L"Developer options > USB debugging), connect it and accept the prompt on the phone.\n"
                       L"- The launcher uses adb. If this PC has none, Google's Android SDK Platform-Tools (about 7 MB) are "
                       L"downloaded into %LOCALAPPDATA%\\tsuki-tools (their license: developer.android.com/license).\n\n"
                       L"Continue?",
                       L"Para mandar la carpeta al tel\u00e9fono por USB:\n\n"
                       L"- Activa la depuraci\u00f3n USB en el tel\u00e9fono (Ajustes > Acerca del tel\u00e9fono > toca 7 veces "
                       L"N\u00famero de compilaci\u00f3n; luego Opciones de desarrollador > Depuraci\u00f3n USB), con\u00e9ctalo y "
                       L"acepta el aviso en el tel\u00e9fono.\n"
                       L"- El launcher usa adb. Si este PC no lo tiene, se descargan las Android SDK Platform-Tools de Google "
                       L"(unos 7 MB) en %LOCALAPPDATA%\\tsuki-tools (su licencia: developer.android.com/license).\n\n"
                       L"\u00bfContinuar?")) != IDYES)
            return false;
        IniSet(L"adb_ok", L"1");
    }
    if (Checked(IDC_AND_DUMP) && SendMode() > 0 &&
        Message(s.dlg, MB_ICONQUESTION | MB_YESNO, L2(L"The dump is about 30 GB: over USB that takes a while. Send it too?",
                                                      L"El dump pesa unos 30 GB: por USB tarda un rato. \u00bfMandarlo tambi\u00e9n?")) != IDYES)
        return false;
    if (Checked(IDC_AND_SAVES) && SendMode() > 0 &&
        Message(s.dlg, MB_ICONWARNING | MB_YESNO, L2(L"Your saves of this PC replace the ones on the phone. Continue?",
                                                     L"Tus partidas de este PC reemplazan las del tel\u00e9fono. \u00bfContinuar?")) != IDYES)
        return false;
    return true;
}

void Start() {
    if (RunnerBusy()) {
        Message(s.dlg, MB_ICONINFORMATION, L2(L"Wait for the task that is running (Windows sub-tab) to end.", L"Espera a que termine la tarea en curso (subpesta\u00f1a Windows)."));
        return;
    }
    if (!CheckReady()) return;
    s.tasks = BuildTasks();
    s.current = 0;
    s.started = GetTickCount64();
    s.sent = SendMode() > 0;
    s.label.clear();
    SetDlgItemTextW(s.dlg, IDC_AND_LOG, L"");
    SetResult(L"", 0);
    SetDlgItemTextW(s.dlg, IDC_AND_STEP, L2(L"Starting...", L"Iniciando..."));
    SetDlgItemTextW(s.dlg, IDC_AND_TIME, L"00:00");
    SendDlgItemMessageW(s.dlg, IDC_AND_PROGRESS, PBM_SETSTATE, PBST_NORMAL, 0);
    SendDlgItemMessageW(s.dlg, IDC_AND_PROGRESS, PBM_SETPOS, 0, 0);
    if (!StartCurrent()) {
        Finish(false, L2(L"PowerShell could not be started.", L"No se pudo iniciar PowerShell."));
        return;
    }
    SetBusyUi(true);
}

void OpenConsole() {
    if (!CheckReady()) return;
    std::wstring ps;
    for (const Task& t : BuildTasks()) {
        std::wstring one = L"& " + PsArg(Join(g.root, kScript));
        for (const std::wstring& a : t.args) one += L" " + (a[0] == L'-' ? a : PsArg(a));
        ps += ps.empty() ? one : L"; if ($LASTEXITCODE -eq 0) { " + one + L" }";
    }
    std::wstring cmd = Quoted(PowerShellExe()) + L" -NoLogo -NoProfile -NoExit -ExecutionPolicy Bypass -Command \"" + ps + L"\"";
    STARTUPINFOW si{};
    si.cb = sizeof si;
    PROCESS_INFORMATION pi{};
    if (CreateProcessW(nullptr, &cmd[0], nullptr, nullptr, FALSE, CREATE_NEW_CONSOLE, nullptr, g.root.c_str(), &si, &pi)) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    } else {
        Message(s.dlg, MB_ICONERROR, Format(L2(L"PowerShell could not be started (error %lu).", L"No se pudo iniciar PowerShell (error %lu)."), GetLastError()));
    }
}

void ShowLinuxCommands() {
    std::wstring t = L2(L"Under Proton / Wine this runs on Linux, not from here. In a terminal, in the port folder, after the recompile:\r\n\r\n",
                        L"Con Proton / Wine esto se hace en Linux, no desde aqu\u00ed. En una terminal, en la carpeta del port, despu\u00e9s de recompilar:\r\n\r\n");
    for (const Task& tk : BuildTasks()) {
        t += L"  bash tools/android/android_port.sh";
        for (size_t i = 0; i + 2 < tk.args.size(); i++) t += L" " + tk.args[i];   // without -Root <folder>
        t += L"\r\n";
    }
    SetDlgItemTextW(s.dlg, IDC_AND_LOG, t.c_str());
}

}  // namespace

void RefreshAndroid() {
    HWND d = s.dlg;
    if (!d) return;
    for (int ed = 0; ed < 2; ed++) {
        std::wstring st;
        FILETIME ft{};
        if (!Recompiled(ed)) st = L2(L"recompile it first (Windows sub-tab)", L"recomp\u00edlala primero (subpesta\u00f1a Windows)");
        else if (GetFileTime64(LibPath(ed), ft))
            st = Format(L2(L"library built %ls", L"librer\u00eda compilada el %ls"), FormatFileTime(ft).c_str());
        else st = L2(L"ready to prepare", L"lista para preparar");
        SetDlgItemTextW(d, ed == 0 ? IDC_AND_JP_STATE : IDC_AND_GL_STATE, st.c_str());
    }
    // The Android pack and the app: here, to be downloaded, or not published yet.
    std::wstring pin = ReadTextFile(Join(g.root, L"tools\\internal\\android_pack.json"), 1 << 16);
    std::wstring pack = ReadTextFile(Join(g.root, L"_prepare\\android-pack\\pack.json"), 1 << 16);
    std::wstring line;
    if (!pack.empty()) line = Format(L2(L"Android pack: v%ls on this PC.", L"Paquete Android: v%ls en este PC."), JsonValue(pack, L"version").c_str());
    else if (!JsonValue(pin, L"url").empty())
        line = Format(L2(L"Android pack: v%ls, downloaded the first time (about 80 MB).", L"Paquete Android: v%ls, se descarga la primera vez (unos 80 MB)."),
                      JsonValue(pin, L"version").c_str());
    else line = L2(L"Android pack: not published yet.", L"Paquete Android: todav\u00eda no publicado.");
    line += L"  ";
    if (FileExists(Join(g.root, L"_android\\tsukihime.apk"))) line += L2(L"App: _android\\tsukihime.apk.", L"App: _android\\tsukihime.apk.");
    else if (!JsonValue(pin, L"apk_url").empty())
        line += Format(L2(L"App: v%ls (installed when sending).", L"App: v%ls (se instala al mandar)."), JsonValue(pin, L"apk_version").c_str());
    else line += L2(L"App: install the APK of the release by hand.", L"App: instala a mano la APK del release.");
    SetDlgItemTextW(d, IDC_AND_INFO, line.c_str());
    if (!s.tasks.empty() && RunnerBusy() && IsWindowEnabled(GetDlgItem(d, IDC_AND_CANCEL))) return;   // ours is running
    SetBusyUi(false);
    if (g.wine) {
        for (int id : {IDC_AND_START, IDC_AND_CONSOLE}) EnableWindow(GetDlgItem(d, id), FALSE);
        ShowLinuxCommands();
    }
}

INT_PTR CALLBACK AndroidProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_INITDIALOG: {
        s.dlg = dlg;
        EnableThemeDialogTexture(dlg, ETDT_ENABLETAB);
        ApplyDialogText(dlg, IDD_ANDROID);
        HDC dc = GetDC(dlg);
        int px = -MulDiv(9, GetDeviceCaps(dc, LOGPIXELSY), 72);
        ReleaseDC(dlg, dc);
        s.mono = CreateFontW(px, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                             CLEARTYPE_QUALITY, FIXED_PITCH | FF_MODERN, L"Consolas");
        SendDlgItemMessageW(dlg, IDC_AND_LOG, WM_SETFONT, WPARAM(s.mono), FALSE);
        SendDlgItemMessageW(dlg, IDC_AND_LOG, EM_SETLIMITTEXT, 0, 0);
        SendDlgItemMessageW(dlg, IDC_AND_PROGRESS, PBM_SETRANGE32, 0, 1000);
        HWND cb = GetDlgItem(dlg, IDC_AND_SEND);
        SendMessageW(cb, CB_ADDSTRING, 0, LPARAM(L2(L"Do not send (copy the folder by hand)", L"No mandar (copiar la carpeta a mano)")));
        SendMessageW(cb, CB_ADDSTRING, 0, LPARAM(L2(L"By USB to the phone's internal storage", L"Por USB a la memoria interna del tel\u00e9fono")));
        SendMessageW(cb, CB_ADDSTRING, 0, LPARAM(L2(L"By USB to the phone's SD card", L"Por USB a la tarjeta SD del tel\u00e9fono")));
        int sel = _wtoi(IniGet(L"android_send", L"0").c_str());
        SendMessageW(cb, CB_SETCURSEL, (sel >= 0 && sel <= 2) ? sel : 0, 0);
        CheckDlgButton(dlg, g.edition == 0 ? IDC_AND_JP : IDC_AND_GL, BST_CHECKED);
        CheckDlgButton(dlg, IDC_AND_MODS, IniGet(L"android_mods", L"1") == L"0" ? BST_UNCHECKED : BST_CHECKED);
        SetDlgItemTextW(dlg, IDC_AND_STEP, L2(L"Not started.", L"Sin iniciar."));
        SetBusyUi(false);
        return TRUE;
    }
    case WM_CTLCOLORSTATIC:
        if (GetDlgCtrlID(HWND(lp)) == IDC_AND_RESULT && !s.result.empty()) return ColorStatic(dlg, wp, lp, IDC_AND_RESULT, s.resultColor);
        break;
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case IDC_AND_START: Start(); return TRUE;
        case IDC_AND_CONSOLE: OpenConsole(); return TRUE;
        case IDC_AND_CANCEL:
            if (Message(dlg, MB_ICONQUESTION | MB_YESNO, L2(L"Stop? Starting it again later only does what is missing.",
                                                           L"\u00bfDetener? Si lo vuelves a iniciar, solo hace lo que falta.")) == IDYES)
                RunnerCancel();
            return TRUE;
        case IDC_AND_SEND:
            if (HIWORD(wp) == CBN_SELCHANGE) {
                IniSet(L"android_send", Format(L"%d", SendMode()));
                RefreshAndroid();
            }
            return TRUE;
        case IDC_AND_MODS: IniSet(L"android_mods", Checked(IDC_AND_MODS) ? L"1" : L"0"); if (g.wine) RefreshAndroid(); return TRUE;
        case IDC_AND_JP:
        case IDC_AND_GL:
        case IDC_AND_SAVES:
        case IDC_AND_DUMP: if (g.wine) RefreshAndroid(); return TRUE;
        case IDC_AND_OPEN: {
            int ed = Checked(IDC_AND_GL) && !Checked(IDC_AND_JP) ? 1 : Checked(IDC_AND_JP) ? 0 : g.edition;
            std::wstring dir = DirExists(PhoneDir(ed)) ? PhoneDir(ed) : Join(g.root, L"_android");
            EnsureDir(dir);
            OpenInShell(dlg, dir);
            return TRUE;
        }
        case IDC_AND_COPY: CopyToClipboard(dlg, GetDlgText(dlg, IDC_AND_LOG)); return TRUE;
        case IDC_AND_OPENLOGS: {
            std::wstring prep = Join(g.root, L"_prepare");
            EnsureDir(prep);
            OpenInShell(dlg, prep);
            return TRUE;
        }
        }
        break;
    case WM_TIMER:
        if (wp == kTimerElapsed) {
            SetDlgItemTextW(dlg, IDC_AND_TIME, FormatElapsed(GetTickCount64() - s.started).c_str());
            if (!s.label.empty()) SetDlgItemTextW(dlg, IDC_AND_STEP, s.label.c_str());
        }
        return TRUE;
    case WM_APP_LINE: {
        std::wstring* line = reinterpret_cast<std::wstring*>(lp);
        AppendLog(*line);
        if (!s.tasks.empty()) ParseLine(*line);
        if (!s.label.empty()) SetDlgItemTextW(dlg, IDC_AND_STEP, s.label.c_str());
        delete line;
        return TRUE;
    }
    case WM_APP_DONE:
        OnDone(DWORD(wp), lp != 0);
        return TRUE;
    case WM_DESTROY:
        if (s.mono) DeleteObject(s.mono);
        s.mono = nullptr;
        break;
    }
    return FALSE;
}

// ---------------------------------------------------------------------------------------------- Install and recompile
// The tab: an inner tab control with "Windows" (IDD_INSTALL, install.cpp) and "Android" (IDD_ANDROID, above), like
// "Play and saves".
namespace {
HWND g_installTabs = nullptr;
HWND g_installInner[2] = {};

void ShowSub(int sub) {
    if (!g_installTabs) return;
    TabCtrl_SetCurSel(g_installTabs, sub);
    for (int k = 0; k < 2; k++) ShowWindow(g_installInner[k], k == sub ? SW_SHOW : SW_HIDE);
    IniSet(L"install_sub", sub == 1 ? L"android" : L"windows");
    if (sub == 0) RefreshInstall();
    else RefreshAndroid();
}
}  // namespace

void ShowInstallSub(int sub) { ShowSub(sub == 1 ? 1 : 0); }

INT_PTR CALLBACK InstallTabProc(HWND dlg, UINT msg, WPARAM, LPARAM lp) {
    switch (msg) {
    case WM_INITDIALOG: {
        g_installTabs = GetDlgItem(dlg, IDC_INSTALL_TABS);
        EnableThemeDialogTexture(dlg, ETDT_ENABLETAB);
        const wchar_t* names[2] = {L"Windows", L"Android"};
        for (int i = 0; i < 2; i++) {
            TCITEMW it{};
            it.mask = TCIF_TEXT;
            it.pszText = const_cast<wchar_t*>(names[i]);
            TabCtrl_InsertItem(g_installTabs, i, &it);
        }
        g_installInner[0] = CreateDialogParamW(g.inst, MAKEINTRESOURCEW(IDD_INSTALL), dlg, InstallProc, 0);
        g_installInner[1] = CreateDialogParamW(g.inst, MAKEINTRESOURCEW(IDD_ANDROID), dlg, AndroidProc, 0);
        RECT r;
        GetWindowRect(g_installTabs, &r);
        TabCtrl_AdjustRect(g_installTabs, FALSE, &r);
        MapWindowPoints(nullptr, dlg, reinterpret_cast<POINT*>(&r), 2);
        for (HWND p : g_installInner) SetWindowPos(p, HWND_TOP, r.left, r.top, r.right - r.left, r.bottom - r.top, SWP_NOACTIVATE);
        int sub = IniGet(L"install_sub", L"windows") == L"android" ? 1 : 0;
        TabCtrl_SetCurSel(g_installTabs, sub);
        for (int k = 0; k < 2; k++) ShowWindow(g_installInner[k], k == sub ? SW_SHOW : SW_HIDE);
        return TRUE;
    }
    case WM_NOTIFY: {
        auto* nm = reinterpret_cast<NMHDR*>(lp);
        if (nm->hwndFrom == g_installTabs && nm->code == TCN_SELCHANGE) ShowSub(TabCtrl_GetCurSel(g_installTabs));
        break;
    }
    }
    return FALSE;
}
