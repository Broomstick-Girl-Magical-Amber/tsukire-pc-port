// install.cpp - the "Install and recompile" tab: runs the port's scripts and shows their progress.
//
// - First time (requirements ticked): tools\install_requirements.ps1 -Recomp -Edition japan|global|both -AcceptLicense
//   -NonInteractive -Root <root>. It installs the tools (xwin flow: per user, no administrator) and recompiles.
// - Later: tools\recomp_tsukihime.ps1 -Edition <edition> -Root <root>, once per edition (it skips what is done).
// - Windows PowerShell 5.1 (System32), -ExecutionPolicy Bypass (scripts from a downloaded zip are not blocked).
// - Progress comes from the lines the scripts already print: "== step <id>: <name>" (install_requirements.ps1 TimedStep
//   and recomp_tsukihime.ps1 Step), "recomp_tsukihime: edition <e>, steps <a>..<b>, ..." (start of a recompile), "Done
//   (the game was not started)." (its end) and "ERROR in ...". A line that does not match is only shown.
// - "Open in a console" runs the same in a visible PowerShell window (there the [y/N] questions can be answered).
// - Under Wine / Proton the scripts are not run from here: the tab shows the Linux commands.
#include "common.h"

#include <commctrl.h>
#include <uxtheme.h>

#include <algorithm>
#include <set>

#include "../res/resource.h"
#include "pages.h"
#include "text.h"

namespace {

constexpr UINT_PTR kTimerElapsed = 2;

struct Task {
    std::wstring script;               // tools\<name>.ps1
    std::vector<std::wstring> args;    // after -File <script>
    bool install = false;
    int editions = 1;
};

// Weights of the steps in seconds (measured: tsukihime-instalacion-tiempos.md / tsukihime-xwin.md, Windows xwin flow).
struct W { const wchar_t* id; double w; };
const W kInstallWeights[] = {{L"0", 2},  {L"1", 90}, {L"2", 40}, {L"3", 1},  {L"4", 1},  {L"5", 5},  {L"6", 30},
                             {L"7", 10}, {L"8", 20}, {L"R1", 0}, {L"R2", 0}, {L"9", 2},  {L"10", 1}, {L"11", 1}};
const double kR1PerEdition = 170, kR2PerEdition = 160;
const double kRecompWeights[11] = {2, 4, 10, 2, 4, 5, 70, 1, 15, 150, 1};

struct Progress {
    // install_requirements.ps1
    std::wstring phase;          // its current step
    std::set<std::wstring> done; // its finished steps
    int runsDone = 0;            // recompiles finished inside the current R1 / R2
    // recomp_tsukihime.ps1 (alone, or inside R1 / R2)
    bool inRecomp = false;
    int from = 0, to = 10, step = -1;
    std::wstring recompEdition;
    std::wstring label;
    std::wstring error;
    std::wstring hint;
};

struct State {
    HWND dlg = nullptr;
    std::vector<Task> tasks;
    size_t current = 0;
    ULONGLONG started = 0;
    Progress p;
    HFONT mono = nullptr;
    bool failed = false;
    std::wstring result;
    COLORREF resultColor = 0;
} s;

double InstallWeight(const std::wstring& id, int editions) {
    if (id == L"R1") return kR1PerEdition * editions;
    if (id == L"R2") return kR2PerEdition * editions;
    for (const W& w : kInstallWeights) if (id == w.id) return w.w;
    return 1;
}

double InstallTotal(int editions) {
    double t = 0;
    for (const W& w : kInstallWeights) t += InstallWeight(w.id, editions);
    return t;
}

double RecompFraction(const Progress& p) {   // 0..1 inside one recomp_tsukihime.ps1 run
    double all = 0, before = 0;
    for (int k = p.from; k <= p.to && k <= 10; k++) {
        all += kRecompWeights[k];
        if (k < p.step) before += kRecompWeights[k];
    }
    return all > 0 ? before / all : 0;
}

double TaskFraction() {
    const Task& t = s.tasks[s.current];
    const Progress& p = s.p;
    if (!t.install) return p.inRecomp ? RecompFraction(p) : 0;
    double doneW = 0;
    for (const std::wstring& id : p.done) doneW += InstallWeight(id, t.editions);
    double cur = 0;
    if (!p.phase.empty() && !p.done.count(p.phase)) {
        double sub = 0;
        if (p.phase == L"R1" || p.phase == L"R2") sub = (p.runsDone + (p.inRecomp ? RecompFraction(p) : 0)) / double(t.editions);
        cur = InstallWeight(p.phase, t.editions) * std::min(sub, 1.0);
    }
    return std::min((doneW + cur) / InstallTotal(t.editions), 0.99);
}

void SetProgress() {
    if (s.tasks.empty()) return;
    double f = (double(s.current) + TaskFraction()) / double(s.tasks.size());
    SendDlgItemMessageW(s.dlg, IDC_INS_PROGRESS, PBM_SETPOS, WPARAM(f * 1000), 0);
}

std::wstring EditionOfKey(const std::wstring& key) { return key == L"global" ? EditionName(1) : EditionName(0); }

void ParseLine(const std::wstring& raw) {
    Progress& p = s.p;
    std::wstring line = raw;
    size_t a = line.find_first_not_of(L' ');
    std::wstring t = a == std::wstring::npos ? L"" : line.substr(a);
    NoteRecompLine(t);   // mods.cpp: an .nsp "not for this edition"

    if (t.rfind(L"recomp_tsukihime: edition ", 0) == 0) {   // a recompile starts
        p.inRecomp = true;
        p.step = -1;
        size_t e = t.find(L',', 26);
        p.recompEdition = t.substr(26, e == std::wstring::npos ? std::wstring::npos : e - 26);
        size_t st = t.find(L"steps ");
        if (st != std::wstring::npos) swscanf(t.c_str() + st, L"steps %d..%d", &p.from, &p.to);
        return;
    }
    if (t.rfind(L"== step ", 0) == 0) {
        size_t colon = t.find(L':');
        std::wstring id = t.substr(8, colon == std::wstring::npos ? std::wstring::npos : colon - 8);
        std::wstring name = colon == std::wstring::npos ? L"" : t.substr(colon + 2);
        if (p.inRecomp) {
            int n = _wtoi(id.c_str());
            if (n >= p.from) p.step = n;   // "3 (always)" before the first step of a resumed run does not move the bar
            p.label = Format(L2(L"%ls - step %d of %d: %ls", L"%ls - paso %d de %d: %ls"), EditionOfKey(p.recompEdition).c_str(), n, p.to, name.c_str());
        } else {
            if (!p.phase.empty() && p.phase != id) p.done.insert(p.phase);
            if (p.phase != id) p.runsDone = 0;
            p.phase = id;
            p.label = Format(L2(L"Requirements - step %ls: %ls", L"Requisitos - paso %ls: %ls"), id.c_str(), name.c_str());
        }
        SetProgress();
        return;
    }
    if (t.rfind(L"Done (the game was not started).", 0) == 0) {   // end of a recompile
        if (p.inRecomp) p.runsDone++;
        p.inRecomp = false;
        SetProgress();
        return;
    }
    if (t.rfind(L"ERROR in ", 0) == 0) {
        if (s.p.error.empty()) p.error = t;   // the first one is the precise one (recomp's, before install's summary)
        p.inRecomp = false;
        return;
    }
    if (p.hint.empty() && (t.rfind(L"Resume with -From", 0) == 0 || t.rfind(L"What the step managed", 0) == 0 ||
                           t.rfind(L"Run the same command again", 0) == 0))
        p.hint = t;
}

void AppendLog(const std::wstring& line) {
    HWND e = GetDlgItem(s.dlg, IDC_INS_LOG);
    int len = GetWindowTextLengthW(e);
    if (len > 1500000) {   // keep the control light: drop the first half (the log file keeps everything)
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
    SetDlgItemTextW(s.dlg, IDC_INS_RESULT, text.c_str());
    InvalidateRect(GetDlgItem(s.dlg, IDC_INS_RESULT), nullptr, TRUE);
}

std::wstring PsArg(const std::wstring& v) {   // a value inside a PowerShell single-quoted string
    std::wstring out = L"'";
    for (wchar_t c : v) { out += c; if (c == L'\'') out += L'\''; }
    return out + L"'";
}

std::wstring CommandLine(const Task& t) {
    std::wstring c = Quoted(PowerShellExe()) + L" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File " +
                     Quoted(Join(g.root, t.script));
    for (const std::wstring& a : t.args) c += L" " + Quoted(a);
    return c;
}

// -AssumeYes exists once tools\patches\apply_install_yes.py (AP_INSTALL_YES) is applied; without it PowerShell
// would reject the unknown parameter, so the launcher only passes it when the script has it.
bool ScriptHasAssumeYes() {
    return ReadTextFile(Join(g.root, L"tools\\install_requirements.ps1"), 4u << 20).find(L"AP_INSTALL_YES") != std::wstring::npos;
}

bool WantsAssumeYes() {
    return IsDlgButtonChecked(s.dlg, IDC_INS_YES) == BST_CHECKED && ScriptHasAssumeYes();
}

std::vector<Task> BuildTasks(bool forConsole) {
    std::vector<Task> out;
    bool jp = IsDlgButtonChecked(s.dlg, IDC_INS_JP) == BST_CHECKED, gl = IsDlgButtonChecked(s.dlg, IDC_INS_GL) == BST_CHECKED;
    if (IsDlgButtonChecked(s.dlg, IDC_INS_REQ) == BST_CHECKED) {
        Task t;
        t.script = L"tools\\install_requirements.ps1";
        t.install = true;
        t.editions = (jp && gl) ? 2 : 1;
        t.args = {L"-Recomp", L"-Edition", jp && gl ? L"both" : jp ? L"japan" : L"global", L"-AcceptLicense"};
        if (WantsAssumeYes()) t.args.push_back(L"-AssumeYes");   // every question but the Microsoft license is answered yes
        if (!forConsole) t.args.push_back(L"-NonInteractive");   // a question fails with its message instead of waiting
        t.args.push_back(L"-Root");
        t.args.push_back(g.root);
        out.push_back(t);
        return out;
    }
    for (int ed = 0; ed < 2; ed++) {
        if (!(ed == 0 ? jp : gl)) continue;
        Task t;
        t.script = L"tools\\recomp_tsukihime.ps1";
        t.args = {L"-Edition", kEditions[ed].key, L"-Root", g.root};
        out.push_back(t);
    }
    return out;
}

void SetBusyUi(bool busy) {
    for (int id : {IDC_INS_START, IDC_INS_CONSOLE, IDC_INS_REQ}) EnableWindow(GetDlgItem(s.dlg, id), !busy && !g.wine);
    EnableWindow(GetDlgItem(s.dlg, IDC_INS_YES), !busy && !g.wine && ScriptHasAssumeYes());
    for (int id : {IDC_INS_JP, IDC_INS_GL}) EnableWindow(GetDlgItem(s.dlg, id), !busy);   // under Wine they pick the commands shown
    EnableWindow(GetDlgItem(s.dlg, IDC_INS_CANCEL), busy);
    if (busy) SetTimer(s.dlg, kTimerElapsed, 1000, nullptr);
    else KillTimer(s.dlg, kTimerElapsed);
}

bool StartCurrent() {
    const Task& t = s.tasks[s.current];
    s.p = Progress{};
    if (!t.install) {   // recomp alone: its header line sets the range; until then, the whole range
        s.p.inRecomp = true;
        s.p.recompEdition = t.args[1];
    }
    std::wstring prep = Join(g.root, L"_prepare");
    EnsureDir(prep);
    std::wstring log = Join(prep, L"launcher_" + Stamp() + (t.install ? L"_install" : L"_recomp_" + t.args[1]) + L".log");
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
        SendDlgItemMessageW(s.dlg, IDC_INS_PROGRESS, PBM_SETPOS, 1000, 0);
        SetDlgItemTextW(s.dlg, IDC_INS_STEP, L2(L"Finished.", L"Terminado."));
    } else {
        SendDlgItemMessageW(s.dlg, IDC_INS_PROGRESS, PBM_SETSTATE, PBST_ERROR, 0);
    }
    CheckDlgButton(s.dlg, IDC_INS_REQ, ToolsReady(nullptr) ? BST_UNCHECKED : BST_CHECKED);
    RefreshAll();
    FLASHWINFO fi{sizeof fi, g.main, FLASHW_ALL | FLASHW_TIMERNOFG, 3, 0};
    FlashWindowEx(&fi);
}

void OnDone(DWORD code, bool cancelled) {
    if (cancelled) {
        Finish(false, L2(L"Cancelled. Starting it again resumes where it stopped.", L"Cancelado. Si lo vuelves a iniciar, sigue donde qued\u00f3."));
        return;
    }
    if (code != 0) {
        std::wstring t = s.p.error.empty() ? Format(L2(L"Failed (exit code %lu): see the log.", L"Fall\u00f3 (c\u00f3digo %lu): mira el log."), code) : s.p.error;
        if (t.find(L"-NonInteractive") != std::wstring::npos || t.find(L"without permission") != std::wstring::npos)
            t += L2(L" Use \"Recomp in a console\" to answer the question.", L" Usa \"Recompilar en una consola\" para responder la pregunta.");
        Finish(false, t);
        return;
    }
    if (++s.current < s.tasks.size()) {
        AppendLog(L"");
        if (!StartCurrent()) Finish(false, L2(L"PowerShell could not be started.", L"No se pudo iniciar PowerShell."));
        return;
    }
    Finish(true, Format(L2(L"Done in %ls. You can play now. Go to play and saves tab.", L"Listo en %ls. Ya puedes jugar. Ve a la pesta\u00f1a jugar y partidas."),
                        FormatElapsed(GetTickCount64() - s.started).c_str()));
}

void Start() {
    if (RunnerBusy()) {   // AP_LAUNCHER_ANDROID: the Android sub-tab may be running
        Message(s.dlg, MB_ICONINFORMATION, L2(L"Wait for the task that is running (Android sub-tab) to end.",
                                              L"Espera a que termine la tarea en curso (subpesta\u00f1a Android)."));
        return;
    }
    bool jp = IsDlgButtonChecked(s.dlg, IDC_INS_JP) == BST_CHECKED, gl = IsDlgButtonChecked(s.dlg, IDC_INS_GL) == BST_CHECKED;
    if (!jp && !gl) {
        Message(s.dlg, MB_ICONINFORMATION, L2(L"Tick the edition(s) to compile.", L"Marca la(s) edici\u00f3n(es) que quieres compilar."));
        return;
    }
    std::vector<DumpFile> dumps = FindDumps();
    if (dumps.empty()) {
        Message(s.dlg, MB_ICONWARNING, L2(L"Copy your dump (.xci or .nsp, base version 1.0.0) to the port folder first.",
                                          L"Copia primero tu dump (.xci o .nsp, versi\u00f3n base 1.0.0) a la carpeta del port."));
        return;
    }
    if (jp && gl && dumps.size() < 2) {
        Message(s.dlg, MB_ICONWARNING, L2(L"Both editions need one dump each (.xci / .nsp) in the port folder, and there is only one.",
                                          L"Las dos ediciones necesitan un dump cada una (.xci / .nsp) en la carpeta del port, y solo hay uno."));
        return;
    }
    if (FindKeys() == KeysAt::None) {
        Message(s.dlg, MB_ICONWARNING, L2(L"Copy your prod.keys to the port folder first.",
                                          L"Copia primero tu prod.keys a la carpeta del port."));
        return;
    }
    for (int ed = 0; ed < 2; ed++) {
        if ((ed == 0 ? jp : gl) && IsExeRunning(ExePath(ed))) {
            Message(s.dlg, MB_ICONWARNING, L2(L"Close the game first. Its .exe is rebuilt.", L"Cierra el juego primero. Su .exe se vuelve a compilar."));
            return;
        }
    }
    bool req = IsDlgButtonChecked(s.dlg, IDC_INS_REQ) == BST_CHECKED;
    if (req && !LicenseAccepted(g.main)) return;

    s.tasks = BuildTasks(false);
    s.current = 0;
    s.started = GetTickCount64();
    SetDlgItemTextW(s.dlg, IDC_INS_LOG, L"");
    SetResult(L"", 0);
    SetDlgItemTextW(s.dlg, IDC_INS_STEP, L2(L"Starting...", L"Iniciando..."));
    SetDlgItemTextW(s.dlg, IDC_INS_TIME, L"00:00");
    SendDlgItemMessageW(s.dlg, IDC_INS_PROGRESS, PBM_SETSTATE, PBST_NORMAL, 0);
    SendDlgItemMessageW(s.dlg, IDC_INS_PROGRESS, PBM_SETPOS, 0, 0);
    SetBusyUi(true);
    RefreshPlay();
    if (!StartCurrent()) Finish(false, L2(L"PowerShell could not be started.", L"No se pudo iniciar PowerShell."));
}

void OpenConsole() {
    bool jp = IsDlgButtonChecked(s.dlg, IDC_INS_JP) == BST_CHECKED, gl = IsDlgButtonChecked(s.dlg, IDC_INS_GL) == BST_CHECKED;
    if (!jp && !gl) {
        Message(s.dlg, MB_ICONINFORMATION, L2(L"Tick the edition(s) to compile.", L"Marca la(s) edici\u00f3n(es) que quieres compilar."));
        return;
    }
    if (IsDlgButtonChecked(s.dlg, IDC_INS_REQ) == BST_CHECKED && !LicenseAccepted(g.main)) return;
    std::wstring ps;
    for (const Task& t : BuildTasks(true)) {
        std::wstring one = L"& " + PsArg(Join(g.root, t.script));
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
    std::wstring key = IsDlgButtonChecked(s.dlg, IDC_INS_GL) == BST_CHECKED ? (IsDlgButtonChecked(s.dlg, IDC_INS_JP) == BST_CHECKED ? L"both" : L"global") : L"japan";
    std::wstring t = L2(L"Under Proton / Wine the compilation runs on Linux, not from here.\r\nIn a terminal, in the port folder:\r\n\r\n",
                        L"Con Proton / Wine la compilaci\u00f3n se hace en Linux, no desde aqu\u00ed.\r\nEn una terminal, en la carpeta del port:\r\n\r\n");
    t += L2(L"First time (installs the tools and compiles):\r\n", L"La primera vez (instala las herramientas y compila):\r\n");
    t += L"  bash tools/linux/install_requirements_linux.sh -Recomp -Edition " + key + L"\r\n\r\n";
    t += L2(L"Later (after an update, a patch or a new version of the port):\r\n", L"Despu\u00e9s (tras una actualizaci\u00f3n, un parche o una versi\u00f3n nueva del port):\r\n");
    if (key == L"both") t += L"  bash tools/linux/recomp_linux.sh -Edition japan\r\n  bash tools/linux/recomp_linux.sh -Edition global\r\n";
    else t += L"  bash tools/linux/recomp_linux.sh -Edition " + key + L"\r\n";
    t += L2(L"\r\nPlaying, saves and options work from this launcher under Proton.", L"\r\nJugar, las partidas y las opciones s\u00ed funcionan desde este launcher con Proton.");
    SetDlgItemTextW(s.dlg, IDC_INS_LOG, t.c_str());
}

}  // namespace

void PreselectInstall(int ed) {
    ShowInstallSub(0);   // AP_LAUNCHER_ANDROID: the Windows sub-tab
    if (!s.dlg || RunnerBusy()) return;
    CheckDlgButton(s.dlg, IDC_INS_JP, ed == 0 ? BST_CHECKED : BST_UNCHECKED);
    CheckDlgButton(s.dlg, IDC_INS_GL, ed == 1 ? BST_CHECKED : BST_UNCHECKED);
    RefreshInstall();
}

void RefreshInstall() {
    HWND d = s.dlg;
    if (!d) return;
    std::vector<DumpFile> dumps = FindDumps();
    std::wstring line;
    if (dumps.empty()) {
        line = L2(L"Dump: no .xci / .nsp in the port folder. Copy yours there.",
                  L"Dump: no hay .xci / .nsp en la carpeta del port. Copia el tuyo ah\u00ed.");
    } else {
        line = L2(L"Dump: ", L"Dump: ");
        for (size_t i = 0; i < dumps.size(); i++)
            line += (i ? L", " : L"") + dumps[i].name + Format(L" (%.1f GB)", double(dumps[i].size) / double(1ull << 30));
    }
    SetDlgItemTextW(d, IDC_INS_DUMPS, line.c_str());
    KeysAt k = FindKeys();
    SetDlgItemTextW(d, IDC_INS_KEYS, k == KeysAt::Data ? L2(L"Keys: data\\prod.keys found.", L"Claves: data\\prod.keys encontrado.")
                                    : k == KeysAt::Root ? L2(L"Keys: prod.keys found.", L"Claves: prod.keys encontrado.")
                                                        : L2(L"Keys: prod.keys is missing. Copy yours to the port folder.", L"Claves: falta prod.keys. Copia el tuyo a la carpeta del port."));
    std::wstring detail;
    bool tools = ToolsReady(&detail);
    SetDlgItemTextW(d, IDC_INS_TOOLS, tools ? Format(L2(L"Tools: installed.", L"Herramientas: instaladas."), detail.c_str()).c_str()
                                            : L2(L"Tools: not installed yet; the first run installs them (about 5 GB).",
                                                 L"Herramientas: no instaladas; la primera vez se instalan (unos 5 GB)."));
    for (int ed = 0; ed < 2; ed++) {
        FILETIME ft{};
        std::wstring st;
        if (IsExeRunning(ExePath(ed))) st = L2(L"the game is running", L"el juego est\u00e1 abierto");
        else if (GetFileTime64(ExePath(ed), ft)) st = Format(L2(L"compiled %ls", L"compilada el %ls"), FormatFileTime(ft).c_str());
        else st = L2(L"not compiled", L"sin compilar");
        SetDlgItemTextW(d, ed == 0 ? IDC_INS_JP_STATE : IDC_INS_GL_STATE, st.c_str());
    }
    bool req = IsDlgButtonChecked(d, IDC_INS_REQ) == BST_CHECKED;
    bool yes = ScriptHasAssumeYes();
    SetDlgItemTextW(d, IDC_INS_YES, yes ? L2(L"Answer yes to the install questions.",
                                              L"Responder s\u00ed a las preguntas de la instalaci\u00f3n.")
                                        : L2(L"Answer yes to the questions: needs tools\\patches\\apply_install_yes.py",
                                              L"Responder s\u00ed a las preguntas: falta aplicar tools\\patches\\apply_install_yes.py"));
    EnableWindow(GetDlgItem(d, IDC_INS_YES), yes && !RunnerBusy() && !g.wine);
    SetDlgItemTextW(d, IDC_INS_START, req ? L2(L"Install and compile", L"Instalar y compilar") : L2(L"Recompile", L"Recompilar"));
    if (g.wine) {
        SetBusyUi(false);
        ShowLinuxCommands();
    }
}

INT_PTR CALLBACK InstallProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_INITDIALOG: {
        s.dlg = dlg;
        EnableThemeDialogTexture(dlg, ETDT_ENABLETAB);
        ApplyDialogText(dlg, IDD_INSTALL);
        HDC dc = GetDC(dlg);
        int px = -MulDiv(9, GetDeviceCaps(dc, LOGPIXELSY), 72);
        ReleaseDC(dlg, dc);
        s.mono = CreateFontW(px, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                             CLEARTYPE_QUALITY, FIXED_PITCH | FF_MODERN, L"Consolas");
        SendDlgItemMessageW(dlg, IDC_INS_LOG, WM_SETFONT, WPARAM(s.mono), FALSE);
        SendDlgItemMessageW(dlg, IDC_INS_LOG, EM_SETLIMITTEXT, 0, 0);   // no 32K limit
        SendDlgItemMessageW(dlg, IDC_INS_PROGRESS, PBM_SETRANGE32, 0, 1000);
        CheckDlgButton(dlg, g.edition == 0 ? IDC_INS_JP : IDC_INS_GL, BST_CHECKED);
        CheckDlgButton(dlg, IDC_INS_REQ, ToolsReady(nullptr) ? BST_UNCHECKED : BST_CHECKED);
        CheckDlgButton(dlg, IDC_INS_YES, IniGet(L"auto_yes", L"1") == L"0" ? BST_UNCHECKED : BST_CHECKED);   // on by default
        SetDlgItemTextW(dlg, IDC_INS_STEP, L2(L"Not started.", L"Sin iniciar."));
        SetBusyUi(false);
        return TRUE;
    }
    case WM_CTLCOLORSTATIC:
        if (GetDlgCtrlID(HWND(lp)) == IDC_INS_RESULT && !s.result.empty()) return ColorStatic(dlg, wp, lp, IDC_INS_RESULT, s.resultColor);
        break;
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case IDC_INS_START: Start(); return TRUE;
        case IDC_INS_CONSOLE: OpenConsole(); return TRUE;
        case IDC_INS_CANCEL:
            if (Message(dlg, MB_ICONQUESTION | MB_YESNO, L2(L"Stop the compilation? Starting it again later resumes where it stopped.",
                                                           L"\u00bfDetener la compilaci\u00f3n? Si la vuelves a iniciar, sigue donde qued\u00f3.")) == IDYES)
                RunnerCancel();
            return TRUE;
        case IDC_INS_YES: IniSet(L"auto_yes", IsDlgButtonChecked(dlg, IDC_INS_YES) == BST_CHECKED ? L"1" : L"0"); return TRUE;
        case IDC_INS_REQ:
        case IDC_INS_JP:
        case IDC_INS_GL: RefreshInstall(); return TRUE;
        case IDC_INS_COPY: CopyToClipboard(dlg, GetDlgText(dlg, IDC_INS_LOG)); return TRUE;
        case IDC_INS_OPENLOGS: {
            std::wstring prep = Join(g.root, L"_prepare");
            EnsureDir(prep);
            OpenInShell(dlg, prep);
            return TRUE;
        }
        }
        break;
    case WM_TIMER:
        if (wp == kTimerElapsed) {
            SetDlgItemTextW(dlg, IDC_INS_TIME, FormatElapsed(GetTickCount64() - s.started).c_str());
            if (!s.p.label.empty()) SetDlgItemTextW(dlg, IDC_INS_STEP, s.p.label.c_str());
        }
        return TRUE;
    case WM_APP_LINE: {
        std::wstring* line = reinterpret_cast<std::wstring*>(lp);
        AppendLog(*line);
        ParseLine(*line);
        if (!s.p.label.empty()) SetDlgItemTextW(dlg, IDC_INS_STEP, s.p.label.c_str());
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
