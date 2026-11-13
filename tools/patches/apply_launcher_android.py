"""apply_launcher_android.py - Install and recompile gets two sub-tabs: Windows and Android (AP_LAUNCHER_ANDROID, version 3).
Touches ../launcher/CMakeLists.txt (the new source), ../launcher/res/resource.h (IDD_ANDROID 115, IDD_INSTALLTAB 116,
controls 1800-1824), ../launcher/res/launcher.rc (IDD_INSTALL redone at 354 x 254, the same controls; IDD_INSTALLTAB;
IDD_ANDROID), ../launcher/src/text.cpp (Spanish), ../launcher/src/pages.h (declarations), ../launcher/src/main.cpp
(the tab is IDD_INSTALLTAB / InstallTabProc; RefreshAll) and ../launcher/src/install.cpp (the busy check and the
Windows sub-tab in PreselectInstall). Not the framework: no $Markers row in recomp_tsukihime.ps1. Idempotent (marker
AP_LAUNCHER_ANDROID), all or nothing, keeps CRLF and BOM, --remove leaves the files byte for byte as they were.
Needs tools/android/android_port.ps1 (+ .sh) and the android_common.ps1 / phone_data.ps1 of phase 2.3. Then rebuild
the launcher (tools\\dev\\build_launcher.ps1).
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_launcher_android.py
"""
from _patch import run

MARK = "AP_LAUNCHER_ANDROID"
CMAKE = "../launcher/CMakeLists.txt"
RES = "../launcher/res/resource.h"
RC = "../launcher/res/launcher.rc"
TEXT = "../launcher/src/text.cpp"
PAGES = "../launcher/src/pages.h"
MAIN = "../launcher/src/main.cpp"
INS = "../launcher/src/install.cpp"

OLD_INSTALL = '''IDD_INSTALL DIALOGEX 0, 0, 378, 288
STYLE DS_SHELLFONT | DS_CONTROL | WS_CHILD
FONT 8, "MS Shell Dlg", 400, 0, 0x1
BEGIN
    GROUPBOX        "Your files", IDC_INS_FILES_GROUP, 6, 4, 366, 50
    LTEXT           "", IDC_INS_DUMPS, 14, 16, 350, 8, SS_ENDELLIPSIS
    LTEXT           "", IDC_INS_KEYS, 14, 28, 350, 8, SS_ENDELLIPSIS
    LTEXT           "", IDC_INS_TOOLS, 14, 40, 350, 8, SS_ENDELLIPSIS

    GROUPBOX        "Compile", IDC_INS_RUN_GROUP, 6, 58, 366, 80
    AUTOCHECKBOX    "Japanese edition", IDC_INS_JP, 14, 70, 150, 10
    LTEXT           "", IDC_INS_JP_STATE, 168, 71, 196, 8, SS_ENDELLIPSIS
    AUTOCHECKBOX    "Global edition", IDC_INS_GL, 14, 83, 150, 10
    LTEXT           "", IDC_INS_GL_STATE, 168, 84, 196, 8, SS_ENDELLIPSIS
    AUTOCHECKBOX    "Install or check the requirements first (needed the first time)", IDC_INS_REQ, 14, 96, 350, 10
    AUTOCHECKBOX    "Answer yes to the install questions (it may replace tools that do not match)", IDC_INS_YES, 14, 108, 350, 10
    PUSHBUTTON      "Install and compile", IDC_INS_START, 14, 120, 110, 14
    PUSHBUTTON      "Open in a console", IDC_INS_CONSOLE, 130, 120, 90, 14
    PUSHBUTTON      "Cancel", IDC_INS_CANCEL, 226, 120, 60, 14, WS_DISABLED

    GROUPBOX        "Progress", IDC_INS_PROG_GROUP, 6, 141, 366, 143
    LTEXT           "", IDC_INS_STEP, 14, 153, 290, 8, SS_ENDELLIPSIS
    RTEXT           "", IDC_INS_TIME, 306, 153, 58, 8
    CONTROL         "", IDC_INS_PROGRESS, "msctls_progress32", WS_BORDER, 14, 164, 350, 8
    EDITTEXT        IDC_INS_LOG, 14, 176, 350, 81, ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | ES_AUTOHSCROLL | WS_VSCROLL | WS_HSCROLL
    LTEXT           "", IDC_INS_RESULT, 14, 261, 208, 18
    PUSHBUTTON      "Copy log", IDC_INS_COPY, 226, 264, 60, 14
    PUSHBUTTON      "Logs folder", IDC_INS_OPENLOGS, 292, 264, 72, 14
END
'''

NEW_INSTALL = '''IDD_INSTALL DIALOGEX 0, 0, 354, 254
STYLE DS_SHELLFONT | DS_CONTROL | WS_CHILD
FONT 8, "MS Shell Dlg", 400, 0, 0x1
BEGIN
    // AP_LAUNCHER_ANDROID (apply_launcher_android.py): now the "Windows" sub-tab of Install and recompile (354 x 254)
    GROUPBOX        "Your files", IDC_INS_FILES_GROUP, 4, 2, 346, 44
    LTEXT           "", IDC_INS_DUMPS, 12, 12, 330, 8, SS_ENDELLIPSIS
    LTEXT           "", IDC_INS_KEYS, 12, 23, 330, 8, SS_ENDELLIPSIS
    LTEXT           "", IDC_INS_TOOLS, 12, 34, 330, 8, SS_ENDELLIPSIS

    GROUPBOX        "Compile", IDC_INS_RUN_GROUP, 4, 48, 346, 78
    AUTOCHECKBOX    "Japanese edition", IDC_INS_JP, 12, 59, 144, 10
    LTEXT           "", IDC_INS_JP_STATE, 160, 60, 182, 8, SS_ENDELLIPSIS
    AUTOCHECKBOX    "Global edition", IDC_INS_GL, 12, 71, 144, 10
    LTEXT           "", IDC_INS_GL_STATE, 160, 72, 182, 8, SS_ENDELLIPSIS
    AUTOCHECKBOX    "Install or check the requirements first (needed the first time)", IDC_INS_REQ, 12, 83, 330, 10
    AUTOCHECKBOX    "Answer yes to the install questions (it may replace tools that do not match)", IDC_INS_YES, 12, 95, 330, 10
    PUSHBUTTON      "Install and compile", IDC_INS_START, 12, 108, 110, 14
    PUSHBUTTON      "Open in a console", IDC_INS_CONSOLE, 128, 108, 90, 14
    PUSHBUTTON      "Cancel", IDC_INS_CANCEL, 224, 108, 60, 14, WS_DISABLED

    GROUPBOX        "Progress", IDC_INS_PROG_GROUP, 4, 129, 346, 122
    LTEXT           "", IDC_INS_STEP, 12, 140, 270, 8, SS_ENDELLIPSIS
    RTEXT           "", IDC_INS_TIME, 286, 140, 56, 8
    CONTROL         "", IDC_INS_PROGRESS, "msctls_progress32", WS_BORDER, 12, 151, 330, 8
    EDITTEXT        IDC_INS_LOG, 12, 162, 330, 60, ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | ES_AUTOHSCROLL | WS_VSCROLL | WS_HSCROLL
    LTEXT           "", IDC_INS_RESULT, 12, 226, 196, 18
    PUSHBUTTON      "Copy log", IDC_INS_COPY, 214, 229, 60, 14
    PUSHBUTTON      "Logs folder", IDC_INS_OPENLOGS, 278, 229, 64, 14
END

// AP_LAUNCHER_ANDROID: the Install and recompile tab holds two sub-tabs, Windows (IDD_INSTALL) and Android (IDD_ANDROID)
IDD_INSTALLTAB DIALOGEX 0, 0, 378, 288
STYLE DS_SHELLFONT | DS_CONTROL | WS_CHILD
FONT 8, "MS Shell Dlg", 400, 0, 0x1
BEGIN
    CONTROL         "", IDC_INSTALL_TABS, "SysTabControl32", WS_TABSTOP, 6, 4, 366, 280
END

// The Android sub-tab (src/android.cpp)
IDD_ANDROID DIALOGEX 0, 0, 354, 254
STYLE DS_SHELLFONT | DS_CONTROL | WS_CHILD
FONT 8, "MS Shell Dlg", 400, 0, 0x1
BEGIN
    GROUPBOX        "Edition (recompiled in the Windows sub-tab)", IDC_AND_ED_GROUP, 4, 2, 346, 58
    AUTOCHECKBOX    "Japanese edition", IDC_AND_JP, 12, 13, 144, 10
    LTEXT           "", IDC_AND_JP_STATE, 160, 14, 182, 8, SS_ENDELLIPSIS
    AUTOCHECKBOX    "Global edition", IDC_AND_GL, 12, 25, 144, 10
    LTEXT           "", IDC_AND_GL_STATE, 160, 26, 182, 8, SS_ENDELLIPSIS
    LTEXT           "", IDC_AND_INFO, 12, 38, 330, 18

    GROUPBOX        "Phone", IDC_AND_PHONE_GROUP, 4, 62, 346, 66
    LTEXT           "Send", IDC_AND_SEND_LABEL, 12, 75, 40, 8
    COMBOBOX        IDC_AND_SEND, 54, 73, 200, 60, CBS_DROPDOWNLIST | WS_VSCROLL | WS_TABSTOP
    AUTOCHECKBOX    "Active mod layers", IDC_AND_MODS, 12, 89, 144, 10
    AUTOCHECKBOX    "Saves of this PC (they replace the phone's)", IDC_AND_SAVES, 160, 89, 182, 10
    AUTOCHECKBOX    "The dump too (about 30 GB)", IDC_AND_DUMP, 12, 101, 144, 10
    PUSHBUTTON      "Prepare for Android", IDC_AND_START, 12, 112, 100, 14
    PUSHBUTTON      "Open in a console", IDC_AND_CONSOLE, 116, 112, 84, 14
    PUSHBUTTON      "Cancel", IDC_AND_CANCEL, 204, 112, 50, 14, WS_DISABLED
    PUSHBUTTON      "Phone folder", IDC_AND_OPEN, 258, 112, 84, 14

    GROUPBOX        "Progress", IDC_AND_PROG_GROUP, 4, 131, 346, 120
    LTEXT           "", IDC_AND_STEP, 12, 142, 270, 8, SS_ENDELLIPSIS
    RTEXT           "", IDC_AND_TIME, 286, 142, 56, 8
    CONTROL         "", IDC_AND_PROGRESS, "msctls_progress32", WS_BORDER, 12, 153, 330, 8
    EDITTEXT        IDC_AND_LOG, 12, 164, 330, 58, ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | ES_AUTOHSCROLL | WS_VSCROLL | WS_HSCROLL
    LTEXT           "", IDC_AND_RESULT, 12, 226, 196, 18
    PUSHBUTTON      "Copy log", IDC_AND_COPY, 214, 229, 60, 14
    PUSHBUTTON      "Logs folder", IDC_AND_OPENLOGS, 278, 229, 64, 14
END
'''

EDITS = [
    (CMAKE,
     '''    src/text.cpp
''',
     '''    src/text.cpp
    src/android.cpp   # AP_LAUNCHER_ANDROID (apply_launcher_android.py): the Android sub-tab
'''),

    (RES,
     '''#define IDD_PLAYTAB             114
''',
     '''#define IDD_PLAYTAB             114
#define IDD_ANDROID             115   // AP_LAUNCHER_ANDROID (apply_launcher_android.py): the Android sub-tab
#define IDD_INSTALLTAB          116   // Install and recompile: the Windows and Android sub-tabs

// Install and recompile: sub-tabs; Android sub-tab
#define IDC_INSTALL_TABS        1824
#define IDC_AND_ED_GROUP        1800
#define IDC_AND_JP              1801
#define IDC_AND_JP_STATE        1802
#define IDC_AND_GL              1803
#define IDC_AND_GL_STATE        1804
#define IDC_AND_INFO            1805
#define IDC_AND_PHONE_GROUP     1806
#define IDC_AND_SEND_LABEL      1807
#define IDC_AND_SEND            1808
#define IDC_AND_MODS            1809
#define IDC_AND_SAVES           1810
#define IDC_AND_DUMP            1811
#define IDC_AND_START           1812
#define IDC_AND_CONSOLE         1813
#define IDC_AND_CANCEL          1814
#define IDC_AND_OPEN            1815
#define IDC_AND_PROG_GROUP      1816
#define IDC_AND_STEP            1817
#define IDC_AND_TIME            1818
#define IDC_AND_PROGRESS        1819
#define IDC_AND_LOG             1820
#define IDC_AND_RESULT          1821
#define IDC_AND_COPY            1822
#define IDC_AND_OPENLOGS        1823
'''),

    (RC, OLD_INSTALL, NEW_INSTALL),

    (TEXT,
     '''    {IDD_INSTALL, IDC_INS_OPENLOGS, L"Carpeta de logs"},
''',
     '''    {IDD_INSTALL, IDC_INS_OPENLOGS, L"Carpeta de logs"},

    // AP_LAUNCHER_ANDROID (apply_launcher_android.py): the Android sub-tab
    {IDD_ANDROID, IDC_AND_ED_GROUP, L"Edici\\u00f3n (recompilada en la subpesta\\u00f1a Windows)"},
    {IDD_ANDROID, IDC_AND_JP, L"Edici\\u00f3n japonesa"},
    {IDD_ANDROID, IDC_AND_GL, L"Edici\\u00f3n global"},
    {IDD_ANDROID, IDC_AND_PHONE_GROUP, L"Tel\\u00e9fono"},
    {IDD_ANDROID, IDC_AND_SEND_LABEL, L"Mandar"},
    {IDD_ANDROID, IDC_AND_MODS, L"Capas de mods activas"},
    {IDD_ANDROID, IDC_AND_SAVES, L"Partidas de este PC (reemplazan las del tel\\u00e9fono)"},
    {IDD_ANDROID, IDC_AND_DUMP, L"Tambi\\u00e9n el dump (unos 30 GB)"},
    {IDD_ANDROID, IDC_AND_START, L"Preparar para Android"},
    {IDD_ANDROID, IDC_AND_CONSOLE, L"Abrir en una consola"},
    {IDD_ANDROID, IDC_AND_CANCEL, L"Cancelar"},
    {IDD_ANDROID, IDC_AND_OPEN, L"Carpeta del tel\\u00e9fono"},
    {IDD_ANDROID, IDC_AND_PROG_GROUP, L"Progreso"},
    {IDD_ANDROID, IDC_AND_COPY, L"Copiar el log"},
    {IDD_ANDROID, IDC_AND_OPENLOGS, L"Carpeta de logs"},
'''),

    (PAGES,
     '''INT_PTR CALLBACK InstallProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp);
''',
     '''INT_PTR CALLBACK InstallProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp);
// AP_LAUNCHER_ANDROID (android.cpp): Install and recompile = sub-tabs Windows (InstallProc) and Android (AndroidProc)
INT_PTR CALLBACK InstallTabProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp);
INT_PTR CALLBACK AndroidProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp);
void ShowInstallSub(int sub);   // 0 Windows, 1 Android
void RefreshAndroid();
'''),

    (MAIN,
     '''void RefreshAll() {
    RefreshPlay();
    RefreshInstall();
''',
     '''void RefreshAll() {
    RefreshPlay();
    RefreshInstall();
    RefreshAndroid();   // AP_LAUNCHER_ANDROID
'''),
    (MAIN,
     '''        const int ids[4] = {IDD_PLAYTAB, IDD_INSTALL, IDD_MODS, IDD_CONTROLS};
        DLGPROC procs[4] = {PlayTabProc, InstallProc, ModsProc, ControlsProc};
''',
     '''        // AP_LAUNCHER_ANDROID: Install and recompile holds the Windows and Android sub-tabs (android.cpp)
        const int ids[4] = {IDD_PLAYTAB, IDD_INSTALLTAB, IDD_MODS, IDD_CONTROLS};
        DLGPROC procs[4] = {PlayTabProc, InstallTabProc, ModsProc, ControlsProc};
'''),

    (INS,
     '''void Start() {
''',
     '''void Start() {
    if (RunnerBusy()) {   // AP_LAUNCHER_ANDROID: the Android sub-tab may be running
        Message(s.dlg, MB_ICONINFORMATION, L2(L"Wait for the task that is running (Android sub-tab) to end.",
                                              L"Espera a que termine la tarea en curso (subpesta\\u00f1a Android)."));
        return;
    }
'''),
    (INS,
     '''void PreselectInstall(int ed) {
''',
     '''void PreselectInstall(int ed) {
    ShowInstallSub(0);   // AP_LAUNCHER_ANDROID: the Windows sub-tab
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
