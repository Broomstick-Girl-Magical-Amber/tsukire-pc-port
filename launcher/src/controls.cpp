// controls.cpp - the "Controls and title screen" tab: an editor of the files the game reads when it starts.
//
//   input.ini     keyboard and mouse (games/tsukihime_common/input_tsuki.cpp; [mando] is kept as it is)
//   title.ini     video and music of the title screen (title_bg.cpp)
//   language.txt  language of the global edition (runtime/src/language.h)
//   config.txt    the NX_* options (AP_CONFIG_ENV, tools/patches/apply_config_env.py: the exe turns its lines into
//                 environment variables at start, without replacing one already set)
//
// Every file is the one the game would read: input.ini / title.ini next to the exe if there is one there (it wins),
// else in the data root (data\<edition>); language.txt and config.txt in the data root. Each change is saved at
// once (a temporary file, then a rename); the game reads them when it starts. The launcher keeps what it does not
// edit: the header and the comment lines of input.ini, every other line of title.ini and config.txt. A value equal
// to the game's default is not written in config.txt (the line goes away), so the defaults of the port can change
// later without a stale copy here.
#include "common.h"

#include <commctrl.h>
#include <shellapi.h>
#include <uxtheme.h>

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <cwchar>
#include <cwctype>

#include "../res/resource.h"
#include "pages.h"
#include "text.h"

namespace {

// The launcher edits the keyboard and the mouse; [mando] of input.ini is read and written back as it is (no UI).
enum Dev { kKb = 0, kPad = 1, kMouse = 2, kDevs = 3 };
constexpr UINT WM_APP_KEY = WM_APP + 40;     // wParam = virtual key captured

// ---------------------------------------------------------------------------------------------- small helpers
std::wstring Trim(const std::wstring& s) {
    size_t a = s.find_first_not_of(L" \t\r\n"), b = s.find_last_not_of(L" \t\r\n");
    return a == std::wstring::npos ? std::wstring() : s.substr(a, b - a + 1);
}

std::wstring Upper(std::wstring s) {
    for (wchar_t& c : s) c = wchar_t(towupper(c));
    return s;
}

std::wstring Lower(std::wstring s) {
    for (wchar_t& c : s) c = wchar_t(towlower(c));
    return s;
}

bool Same(const std::wstring& a, const std::wstring& b) { return _wcsicmp(a.c_str(), b.c_str()) == 0; }

std::vector<std::wstring> SplitLines(const std::wstring& text) {   // text with CRLF (ReadTextFile)
    std::vector<std::wstring> out;
    size_t pos = 0;
    while (pos < text.size()) {
        size_t e = text.find(L'\n', pos);
        std::wstring line = text.substr(pos, e == std::wstring::npos ? std::wstring::npos : e - pos);
        if (!line.empty() && line.back() == L'\r') line.pop_back();
        out.push_back(line);
        pos = e == std::wstring::npos ? text.size() : e + 1;
    }
    if (!out.empty() && out[0].size() && out[0][0] == 0xFEFF) out[0].erase(0, 1);
    return out;
}

std::wstring JoinLines(const std::vector<std::wstring>& lines) {
    std::wstring t;
    for (const std::wstring& l : lines) t += l + L"\r\n";
    return t;
}

// UTF-8 without BOM, through a temporary file and a rename: the game never sees half a file.
bool WriteText(const std::wstring& path, const std::wstring& text) {
    size_t cut = path.find_last_of(L'\\');
    if (cut != std::wstring::npos) EnsureDir(path.substr(0, cut));
    std::string data = WideToUtf8(text);
    std::wstring tmp = path + L".launcher";
    HANDLE h = CreateFileW(tmp.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    DWORD put = 0;
    bool ok = WriteFile(h, data.data(), DWORD(data.size()), &put, nullptr) && put == data.size();
    CloseHandle(h);
    if (ok) ok = MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING) != 0;
    if (!ok) DeleteFileW(tmp.c_str());
    return ok;
}

std::wstring ExeDir(int ed) {
    std::wstring e = ExePath(ed);
    return e.substr(0, e.find_last_of(L'\\'));
}

bool GameRunningNow(int ed) { return IsExeRunning(ExePath(ed)); }

// ---------------------------------------------------------------------------------------------- config.txt
// Kept as its lines; the launcher edits the NX_* it knows and leaves everything else as it was.
struct ConfigFile {
    std::wstring path;
    std::vector<std::wstring> lines;
    bool exists = false;
};

bool ConfigKeyOf(const std::wstring& line, std::wstring* key, std::wstring* value) {
    std::wstring s = line;
    size_t c = s.find_first_of(L";#");
    if (c != std::wstring::npos) s.erase(c);
    size_t eq = s.find(L'=');
    if (eq == std::wstring::npos) return false;
    std::wstring k = Upper(Trim(s.substr(0, eq)));
    if (k.size() < 4 || k.compare(0, 3, L"NX_") != 0) return false;
    *key = k;
    if (value) *value = Trim(s.substr(eq + 1));
    return true;
}

ConfigFile LoadConfig(int ed) {
    ConfigFile f;
    f.path = Join(DataDir(ed), L"config.txt");
    f.exists = FileExists(f.path);
    if (f.exists) f.lines = SplitLines(ReadTextFile(f.path));
    return f;
}

// The first line of the key with a value: the exe sets that one and skips the later ones (already set).
std::wstring ConfigGet(const ConfigFile& f, const wchar_t* key) {
    std::wstring k, v;
    for (const std::wstring& l : f.lines)
        if (ConfigKeyOf(l, &k, &v) && k == key && !v.empty()) return v;
    return L"";
}

std::vector<std::wstring> ConfigHeader() {
    return {L"; config.txt - options of the game, read when it starts (one NX_NAME=value per line; comments with ; or #).",
            L"; Written by the launcher (Controls and title screen tab). An option set in the environment wins over this file."};
}

void ConfigSet(ConfigFile& f, const wchar_t* key, const std::wstring& value) {   // "" removes the key
    std::wstring k;
    bool done = false;
    for (size_t i = 0; i < f.lines.size();) {
        if (ConfigKeyOf(f.lines[i], &k, nullptr) && k == key) {
            if (!value.empty() && !done) {
                f.lines[i] = std::wstring(key) + L"=" + value;
                done = true;
                i++;
            } else {
                f.lines.erase(f.lines.begin() + std::ptrdiff_t(i));
            }
            continue;
        }
        i++;
    }
    if (!value.empty() && !done) {
        if (f.lines.empty()) f.lines = ConfigHeader();
        f.lines.push_back(std::wstring(key) + L"=" + value);
    }
}

// The exe reads config.txt only with AP_CONFIG_ENV (tools\patches\apply_config_env.py); a text of its log says so.
bool ExeReadsConfig(int ed) {
    static std::wstring cachedKey;
    static bool cached = false;
    std::wstring exe = ExePath(ed);
    WIN32_FILE_ATTRIBUTE_DATA a{};
    if (!GetFileAttributesExW(exe.c_str(), GetFileExInfoStandard, &a)) return false;
    std::wstring key = exe + Format(L"|%lu|%lu|%lu", a.nFileSizeLow, a.ftLastWriteTime.dwLowDateTime, a.ftLastWriteTime.dwHighDateTime);
    if (key == cachedKey) return cached;
    cachedKey = key;
    cached = false;
    HANDLE h = CreateFileW(exe.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING, 0, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    HANDLE m = CreateFileMappingW(h, nullptr, PAGE_READONLY, 0, 0, nullptr);
    if (m) {
        const char* p = static_cast<const char*>(MapViewOfFile(m, FILE_MAP_READ, 0, 0, 0));
        if (p) {
            const char kNeedle[] = "already in the environment (kept)";
            size_t n = (size_t(a.nFileSizeHigh) << 32) | a.nFileSizeLow;
            cached = std::search(p, p + n, kNeedle, kNeedle + sizeof kNeedle - 1) != p + n;
            UnmapViewOfFile(p);
        }
        CloseHandle(m);
    }
    CloseHandle(h);
    return cached;
}

// ---------------------------------------------------------------------------------------------- names
struct Action {
    const wchar_t* id;    // the Npad button of input.ini
    const wchar_t* en;
    const wchar_t* es;
    const wchar_t* sw;    // how it looks on a Switch
};
const Action kActions[] = {
    {L"A", L"Advance / accept", L"Avanzar / aceptar", L"A"},
    {L"B", L"Hide the text / back", L"Ocultar el texto / atr\u00e1s", L"B"},
    {L"X", L"Dialogue menu", L"Men\u00fa de di\u00e1logos", L"X"},
    {L"Y", L"System menu", L"Men\u00fa del sistema", L"Y"},
    {L"L", L"Auto mode", L"Modo autom\u00e1tico", L"L"},
    {L"R", L"Fast forward", L"Avance r\u00e1pido", L"R"},
    {L"ZL", L"Previous text", L"Texto anterior", L"ZL"},
    {L"ZR", L"Speed up while held", L"Acelerar (mientras se mantiene)", L"ZR"},
    {L"UP", L"Up (mini menu)", L"Arriba (mini men\u00fa)", L"Up"},
    {L"DOWN", L"Down (next text)", L"Abajo (avanza el texto)", L"Down"},
    {L"LEFT", L"Left", L"Izquierda", L"Left"},
    {L"RIGHT", L"Right", L"Derecha", L"Right"},
    {L"PLUS", L"Plus button", L"Bot\u00f3n +", L"+"},
    {L"MINUS", L"Minus button", L"Bot\u00f3n \u2212", L"\u2212"},
    {L"STICKL", L"Left stick press", L"Presionar el stick izquierdo", L"LS"},
    {L"STICKR", L"Right stick press", L"Presionar el stick derecho", L"RS"},
    {L"SUP", L"Left stick up (a page up in lists)", L"Stick izq. arriba (una p\u00e1gina en listas)", L"LS Up"},
    {L"SDOWN", L"Left stick down (a page down in lists)", L"Stick izq. abajo (una p\u00e1gina en listas)", L"LS Down"},
    {L"SLEFT", L"Left stick left", L"Stick izq. izquierda", L"LS Left"},
    {L"SRIGHT", L"Left stick right", L"Stick izq. derecha", L"LS Right"},
    {L"RSUP", L"Right stick up (one item in lists)", L"Stick der. arriba (de a uno en listas)", L"RS Up"},
    {L"RSDOWN", L"Right stick down (one item in lists)", L"Stick der. abajo (de a uno en listas)", L"RS Down"},
    {L"RSLEFT", L"Right stick left", L"Stick der. izquierda", L"RS Left"},
    {L"RSRIGHT", L"Right stick right", L"Stick der. derecha", L"RS Right"},
};
constexpr int kActionCount = int(sizeof(kActions) / sizeof(kActions[0]));

int ActionIndex(const std::wstring& name) {
    std::wstring n = Upper(Trim(name));
    if (n == L"+") n = L"PLUS";
    if (n == L"-") n = L"MINUS";
    for (int i = 0; i < kActionCount; i++)
        if (n == kActions[i].id) return i;
    return -1;
}

std::wstring ActionText(int i) { return g_spanish ? kActions[i].es : kActions[i].en; }

// Keyboard: the virtual keys input_tsuki.cpp knows (key_code), written with one name each.
struct KeyName {
    const wchar_t* name;
    int vk;
};
const KeyName kKeyNames[] = {
    {L"Space", 0x20}, {L"Enter", 0x0D}, {L"Escape", 0x1B}, {L"Tab", 0x09}, {L"Backspace", 0x08},
    {L"Shift", 0x10}, {L"LShift", 0xA0}, {L"RShift", 0xA1}, {L"Ctrl", 0x11}, {L"LCtrl", 0xA2}, {L"RCtrl", 0xA3},
    {L"Alt", 0x12}, {L"LAlt", 0xA4}, {L"RAlt", 0xA5}, {L"Up", 0x26}, {L"Down", 0x28}, {L"Left", 0x25},
    {L"Right", 0x27}, {L"PageUp", 0x21}, {L"PageDown", 0x22}, {L"Home", 0x24}, {L"End", 0x23},
    {L"Insert", 0x2D}, {L"Delete", 0x2E}, {L"Comma", 0xBC}, {L"Period", 0xBE},
};
const KeyName kKeyAliases[] = {   // other names the game accepts
    {L"ESPACIO", 0x20}, {L"RETURN", 0x0D}, {L"INTRO", 0x0D}, {L"ESC", 0x1B}, {L"CONTROL", 0x11},
    {L"ARRIBA", 0x26}, {L"ABAJO", 0x28}, {L"IZQUIERDA", 0x25}, {L"DERECHA", 0x27}, {L"INICIO", 0x24},
    {L"FIN", 0x23}, {L"SUPR", 0x2E},
};
constexpr int kVkF11 = 0x7A;

int KeyVk(const std::wstring& upper) {
    const std::wstring& n = upper;
    if (n.size() == 1 && ((n[0] >= L'A' && n[0] <= L'Z') || (n[0] >= L'0' && n[0] <= L'9'))) return n[0];
    if (n.size() >= 2 && n.size() <= 3 && n[0] == L'F' && iswdigit(n[1])) {
        int k = _wtoi(n.c_str() + 1);
        if (k >= 1 && k <= 24 && Format(L"%d", k) == n.substr(1)) return 0x70 + k - 1;
    }
    if (n.size() == 4 && n.compare(0, 3, L"NUM") == 0 && iswdigit(n[3])) return 0x60 + (n[3] - L'0');
    for (const KeyName& k : kKeyNames) if (Upper(k.name) == n) return k.vk;
    for (const KeyName& k : kKeyAliases) if (n == k.name) return k.vk;
    return 0;
}

std::wstring VkName(int vk) {   // "" = a key the game does not know
    if ((vk >= 'A' && vk <= 'Z') || (vk >= '0' && vk <= '9')) return std::wstring(1, wchar_t(vk));
    if (vk >= 0x70 && vk <= 0x87) return Format(L"F%d", vk - 0x70 + 1);
    if (vk >= 0x60 && vk <= 0x69) return Format(L"Num%d", vk - 0x60);
    for (const KeyName& k : kKeyNames) if (k.vk == vk) return k.name;
    return L"";
}

// Controller: the Xbox names by position (A = bottom), as input_tsuki.cpp / pad_sdl.cpp read them.
struct PadName {
    const wchar_t* name;
    const wchar_t* en;
    const wchar_t* es;
    const wchar_t* aliases;   // space separated, uppercase
    WORD xinput;              // its XInput bit (0 = none; the triggers are separate)
};
const PadName kPadNames[] = {
    {L"A", L"A (bottom, Cross)", L"A (abajo, Cruz)", L"SUR SOUTH CRUZ CROSS", 0x1000},
    {L"B", L"B (right, Circle)", L"B (derecha, C\u00edrculo)", L"ESTE EAST CIRCULO CIRCLE", 0x2000},
    {L"X", L"X (left, Square)", L"X (izquierda, Cuadrado)", L"OESTE WEST CUADRADO SQUARE", 0x4000},
    {L"Y", L"Y (top, Triangle)", L"Y (arriba, Tri\u00e1ngulo)", L"NORTE NORTH TRIANGULO TRIANGLE", 0x8000},
    {L"LB", L"LB (L1, L)", L"LB (L1, L)", L"L1 L", 0x0100},
    {L"RB", L"RB (R1, R)", L"RB (R1, R)", L"R1 R", 0x0200},
    {L"LT", L"LT (L2, ZL)", L"LT (L2, ZL)", L"L2 ZL", 0},
    {L"RT", L"RT (R2, ZR)", L"RT (R2, ZR)", L"R2 ZR", 0},
    {L"Start", L"Start (Options, +)", L"Start (Options, +)", L"MENU OPTIONS PLUS +", 0x0010},
    {L"Back", L"Back (Share, \u2212)", L"Back (Share, \u2212)", L"VIEW SHARE CREATE MINUS -", 0x0020},
    {L"LS", L"Left stick press (L3)", L"Presionar stick izq. (L3)", L"L3", 0x0040},
    {L"RS", L"Right stick press (R3)", L"Presionar stick der. (R3)", L"R3", 0x0080},
    {L"DPadUp", L"D-pad up", L"Cruceta arriba", L"", 0x0001},
    {L"DPadDown", L"D-pad down", L"Cruceta abajo", L"", 0x0002},
    {L"DPadLeft", L"D-pad left", L"Cruceta izquierda", L"", 0x0004},
    {L"DPadRight", L"D-pad right", L"Cruceta derecha", L"", 0x0008},
    {L"Guide", L"Guide (Home, PS)", L"Guide (Home, PS)", L"HOME PS", 0},
    {L"Touchpad", L"Touchpad (PlayStation)", L"Panel t\u00e1ctil (PlayStation)", L"PANEL", 0},
    {L"Capture", L"Capture (Switch), Mic (PS5)", L"Captura (Switch), Mic (PS5)", L"CAPTURA MIC MISC1", 0},
};

struct MouseName {
    const wchar_t* name;
    const wchar_t* en;
    const wchar_t* es;
    const wchar_t* alias;
};
const MouseName kMouseNames[] = {
    {L"Right", L"Right button", L"Bot\u00f3n derecho", L"DERECHO"},
    {L"Middle", L"Middle button (wheel click)", L"Bot\u00f3n central (clic de la rueda)", L"MEDIO"},
    {L"WheelUp", L"Wheel up", L"Rueda arriba", L"RUEDAARRIBA"},
    {L"WheelDown", L"Wheel down", L"Rueda abajo", L"RUEDAABAJO"},
};

bool InList(const wchar_t* list, const std::wstring& word) {   // space separated
    std::wstring l = std::wstring(L" ") + list + L" ";
    return l.find(L" " + word + L" ") != std::wstring::npos;
}

std::wstring Canonical(int dev, const std::wstring& item) {   // "" = unknown
    std::wstring n = Upper(Trim(item));
    if (n.empty()) return L"";
    if (dev == kKb) {
        int vk = KeyVk(n);
        return vk && vk != kVkF11 ? VkName(vk) : L"";
    }
    if (dev == kPad) {
        for (const PadName& p : kPadNames)
            if (Upper(p.name) == n || InList(p.aliases, n)) return p.name;
        return L"";
    }
    for (const MouseName& m : kMouseNames)
        if (Upper(m.name) == n || n == m.alias) return m.name;
    return L"";
}

std::wstring Display(int dev, const std::wstring& name) {
    if (dev == kKb) {
        static const struct { const wchar_t* n; const wchar_t* en; const wchar_t* es; } kShow[] = {
            {L"Up", L"Up", L"Arriba"}, {L"Down", L"Down", L"Abajo"}, {L"Left", L"Left", L"Izquierda"},
            {L"Right", L"Right", L"Derecha"}, {L"Space", L"Space", L"Espacio"}, {L"Backspace", L"Backspace", L"Retroceso"},
            {L"Escape", L"Esc", L"Esc"}, {L"PageUp", L"Page Up", L"Re P\u00e1g"}, {L"PageDown", L"Page Down", L"Av P\u00e1g"},
            {L"Delete", L"Delete", L"Supr"}, {L"Home", L"Home", L"Inicio"}, {L"End", L"End", L"Fin"},
            {L"Comma", L",", L","}, {L"Period", L".", L"."}, {L"Shift", L"Shift", L"May\u00fas"},
        };
        for (const auto& k : kShow) if (name == k.n) return g_spanish ? k.es : k.en;
        return name;
    }
    if (dev == kPad) {
        for (const PadName& p : kPadNames) if (name == p.name) return g_spanish ? p.es : p.en;
    } else {
        for (const MouseName& m : kMouseNames) if (name == m.name) return g_spanish ? m.es : m.en;
    }
    return name + L" (?)";   // a name the game does not know: kept as it was in the file
}

// The defaults of input_tsuki.cpp (defaults_keys / defaults_pad / defaults_mouse): what a missing section means.
void Defaults(int dev, std::vector<std::wstring>* b) {
    for (int i = 0; i < kActionCount; i++) b[i].clear();
    auto set = [&](const wchar_t* action, std::initializer_list<const wchar_t*> names) {
        int i = ActionIndex(action);
        for (const wchar_t* n : names) b[i].push_back(n);
    };
    if (dev == kKb) {
        set(L"A", {L"Space", L"Enter"}), set(L"UP", {L"Up"}), set(L"DOWN", {L"Down"}), set(L"LEFT", {L"Left"});
        set(L"RIGHT", {L"Right"}), set(L"L", {L"A"}), set(L"ZL", {L"R"}), set(L"R", {L"F"}), set(L"ZR", {L"Ctrl"});
        set(L"Y", {L"Escape", L"S", L"L", L"C"}), set(L"X", {L"Tab"}), set(L"B", {L"Backspace"});
        set(L"SUP", {L"PageUp"}), set(L"SDOWN", {L"PageDown"});
    } else if (dev == kPad) {   // Nintendo layout: the right button (Xbox B) is the Npad A
        set(L"A", {L"B"}), set(L"B", {L"A"}), set(L"X", {L"Y"}), set(L"Y", {L"X"}), set(L"L", {L"LB"}), set(L"R", {L"RB"});
        set(L"ZL", {L"LT"}), set(L"ZR", {L"RT"}), set(L"PLUS", {L"Start"}), set(L"MINUS", {L"Back"});
        set(L"STICKL", {L"LS"}), set(L"STICKR", {L"RS"}), set(L"UP", {L"DPadUp"}), set(L"DOWN", {L"DPadDown"});
        set(L"LEFT", {L"DPadLeft"}), set(L"RIGHT", {L"DPadRight"});
    } else {
        set(L"Y", {L"Right"}), set(L"X", {L"Middle"}), set(L"DOWN", {L"WheelDown"}), set(L"ZL", {L"WheelUp"});
    }
}

// ---------------------------------------------------------------------------------------------- input.ini
const wchar_t* const kSectionNames[kDevs] = {L"[teclado]", L"[mando]", L"[mouse]"};

struct InputFile {
    std::wstring path;
    bool exists = false;
    std::vector<std::wstring> header;                // lines before the first section
    bool present[kDevs] = {false, false, false};     // the section is in the file (it replaces the defaults)
    std::wstring head[kDevs];                         // its [line] as written
    std::vector<std::wstring> kept[kDevs];           // its comment lines and the lines the launcher does not understand
    std::vector<std::wstring> binds[kDevs][kActionCount];
    std::vector<std::wstring> extra;                 // other sections (the game ignores them), kept as they were
};

int SectionDev(const std::wstring& name) {
    std::wstring n = Upper(Trim(name));
    if (n == L"TECLADO" || n == L"KEYBOARD") return kKb;
    // [mando] / [pad] / [gamepad]: not edited by the launcher; like an unknown section, kept exactly as it is written.
    if (n == L"MOUSE" || n == L"RATON") return kMouse;
    return -1;
}

// input.ini / title.ini: NX_<NAME> from config.txt or the environment, else next to the exe if it is there, else
// the data root (the order the game looks in).
std::wstring GameFilePath(int ed, const wchar_t* file, const wchar_t* envName, const ConfigFile& cfg) {
    std::wstring v = ConfigGet(cfg, envName);
    if (v.empty()) {
        wchar_t buf[MAX_PATH * 2];
        DWORD n = GetEnvironmentVariableW(envName, buf, MAX_PATH * 2);
        if (n && n < MAX_PATH * 2) v.assign(buf, n);
    }
    if (!v.empty()) return v;
    std::wstring nextToExe = Join(ExeDir(ed), file);
    if (FileExists(nextToExe)) return nextToExe;
    return Join(DataDir(ed), file);
}

InputFile LoadInput(int ed, const ConfigFile& cfg) {
    InputFile f;
    f.path = GameFilePath(ed, L"input.ini", L"NX_INPUT_INI", cfg);
    for (int d = 0; d < kDevs; d++) Defaults(d, f.binds[d]);
    f.exists = FileExists(f.path);
    if (!f.exists) return f;
    int sec = -2;   // -2 before any section, -1 an unknown one
    bool started[kDevs] = {false, false, false};
    for (const std::wstring& raw : SplitLines(ReadTextFile(f.path))) {
        std::wstring s = raw;
        size_t c = s.find_first_of(L";#");
        if (c != std::wstring::npos) s.erase(c);
        s = Trim(s);
        if (!s.empty() && s[0] == L'[') {
            size_t close = s.find(L']');
            int d = SectionDev(s.substr(1, close == std::wstring::npos ? std::wstring::npos : close - 1));
            sec = d;
            if (d < 0) {
                f.extra.push_back(raw);
            } else if (!started[d]) {
                started[d] = true;
                f.present[d] = true;
                f.head[d] = Trim(raw);
                for (int i = 0; i < kActionCount; i++) f.binds[d][i].clear();
            }
            continue;
        }
        if (sec == -2) {
            f.header.push_back(raw);
            continue;
        }
        if (sec == -1) {
            f.extra.push_back(raw);
            continue;
        }
        if (s.empty()) {
            if (!Trim(raw).empty()) f.kept[sec].push_back(Trim(raw));   // a comment line
            continue;
        }
        size_t eq = s.find(L'=');
        int a = eq == std::wstring::npos ? -1 : ActionIndex(s.substr(0, eq));
        if (a < 0) {
            f.kept[sec].push_back(Trim(raw));   // not understood: the game logs it and skips it; so is it kept here
            continue;
        }
        std::wstring list = s.substr(eq + 1);
        size_t pos = 0;
        while (pos <= list.size()) {
            size_t comma = list.find(L',', pos);
            std::wstring item = Trim(list.substr(pos, comma == std::wstring::npos ? std::wstring::npos : comma - pos));
            pos = comma == std::wstring::npos ? list.size() + 1 : comma + 1;
            if (item.empty()) continue;
            std::wstring canon = Canonical(sec, item);
            std::wstring add = canon.empty() ? item : canon;
            auto& v = f.binds[sec][a];
            if (std::find(v.begin(), v.end(), add) == v.end()) v.push_back(add);
        }
    }
    while (!f.header.empty() && Trim(f.header.back()).empty()) f.header.pop_back();
    return f;
}

std::wstring InputText(const InputFile& f) {
    std::vector<std::wstring> out = f.header;
    if (!f.exists && out.empty()) {
        out = {L"; input.ini - keyboard, controller and mouse of Tsukihime, written by the launcher (Controls and title screen tab).",
               L"; It can be edited by hand too: format and names in framework\\games\\tsukihime_common\\input.ini.example.",
               L"; A section that is here replaces all the defaults of that device; a device without its section keeps them."};
    }
    for (int d = 0; d < kDevs; d++) {
        if (!f.present[d]) continue;
        out.push_back(L"");
        out.push_back(f.head[d].empty() ? kSectionNames[d] : f.head[d]);
        for (const std::wstring& k : f.kept[d]) out.push_back(k);
        for (int i = 0; i < kActionCount; i++) {
            if (f.binds[d][i].empty()) continue;
            std::wstring line = kActions[i].id;
            while (line.size() < 7) line += L' ';
            line += L"= ";
            for (size_t k = 0; k < f.binds[d][i].size(); k++) line += (k ? L", " : L"") + f.binds[d][i][k];
            out.push_back(line);
        }
    }
    if (!f.extra.empty()) {
        out.push_back(L"");
        for (const std::wstring& l : f.extra) out.push_back(l);
    }
    return JoinLines(out);
}

// ---------------------------------------------------------------------------------------------- title.ini
struct TitleFile {
    std::wstring path;
    bool exists = false;
    std::vector<std::wstring> lines;
    // what the game reads (title_bg.cpp), with its defaults
    int video = 0;                      // 0 auto, 1-4, 5 random, 6 cycle
    std::vector<int> pool = {1, 2, 3, 4};
    int music = 0;                      // 0 follow, 1 game, 2-5 = music of video 1-4
    bool hotkeys = true, click = true, unlock = true;
    int fade = 500, delay = 450;
};

bool IsTitleSection(const std::wstring& s) {
    std::wstring n = Lower(Trim(s));
    return n == L"[title]" || n == L"[titulo]";
}

TitleFile LoadTitle(int ed, const ConfigFile& cfg) {
    TitleFile t;
    t.path = GameFilePath(ed, L"title.ini", L"NX_TITLE_INI", cfg);
    t.exists = FileExists(t.path);
    if (!t.exists) return t;
    t.lines = SplitLines(ReadTextFile(t.path));
    bool in = false;
    for (const std::wstring& raw : t.lines) {
        std::wstring s = raw;
        size_t c = s.find_first_of(L";#");
        if (c != std::wstring::npos) s.erase(c);
        s = Trim(s);
        if (s.empty()) continue;
        if (s[0] == L'[') {
            size_t close = s.find(L']');
            in = close != std::wstring::npos && IsTitleSection(s.substr(0, close + 1));
            continue;
        }
        size_t eq = s.find(L'=');
        if (!in || eq == std::wstring::npos) continue;
        std::wstring k = Lower(Trim(s.substr(0, eq))), v = Lower(Trim(s.substr(eq + 1)));
        auto onoff = [&](bool* out) {
            if (v == L"on" || v == L"1" || v == L"yes") *out = true;
            else if (v == L"off" || v == L"0" || v == L"no") *out = false;
        };
        int n = _wtoi(v.c_str());
        bool num = !v.empty() && v.find_first_not_of(L"0123456789") == std::wstring::npos;
        if (k == L"video") {
            if (v == L"auto" || v == L"game" || v == L"default" || v == L"off") t.video = 0;
            else if (v == L"random") t.video = 5;
            else if (v == L"cycle") t.video = 6;
            else if (num && n >= 1 && n <= 4) t.video = n;
        } else if (k == L"pool") {
            std::vector<int> p;
            size_t pos = 0;
            while (pos <= v.size()) {
                size_t comma = v.find(L',', pos);
                int x = _wtoi(Trim(v.substr(pos, comma == std::wstring::npos ? std::wstring::npos : comma - pos)).c_str());
                pos = comma == std::wstring::npos ? v.size() + 1 : comma + 1;
                if (x >= 1 && std::find(p.begin(), p.end(), x) == p.end()) p.push_back(x);
            }
            if (!p.empty()) t.pool = p;
        } else if (k == L"music") {
            if (v == L"follow" || v == L"video") t.music = 0;
            else if (v == L"game" || v == L"auto" || v == L"default" || v == L"off") t.music = 1;
            else if (num && n >= 1 && n <= 4) t.music = n + 1;
        } else if (k == L"hotkeys") {
            onoff(&t.hotkeys);
        } else if (k == L"click") {
            onoff(&t.click);
        } else if (k == L"unlock") {
            if (v == L"progress") t.unlock = true;
            else if (v == L"all") t.unlock = false;
            else onoff(&t.unlock);
        } else if (k == L"fade") {
            if (num && n <= 5000) t.fade = n;
        } else if (k == L"swap_delay") {
            if (num && n <= 5000) t.delay = n;
        }
    }
    return t;
}

// Sets "key = value" in [title]: the active line if there is one; else the commented example line of that key (the
// one title.ini.example has, so the value stays next to its explanation); else at the end of the section. A value
// equal to the default with no line of its own is not written.
void TitleSet(TitleFile& t, const wchar_t* key, const std::wstring& value, bool isDefault) {
    std::vector<std::wstring>& L = t.lines;
    size_t start = std::wstring::npos, end = L.size();
    for (size_t i = 0; i < L.size(); i++) {
        std::wstring s = Trim(L[i]);
        if (s.empty() || s[0] != L'[') continue;
        if (start == std::wstring::npos && IsTitleSection(s.substr(0, s.find(L']') + 1))) start = i;
        else if (start != std::wstring::npos) { end = i; break; }
    }
    if (start == std::wstring::npos) {
        if (isDefault) return;
        if (!L.empty() && !Trim(L.back()).empty()) L.push_back(L"");
        L.push_back(L"[title]");
        start = L.size() - 1;
        end = L.size();
    }
    std::wstring line = std::wstring(key) + L" = " + value;
    size_t commented = std::wstring::npos;
    for (size_t i = start + 1; i < end; i++) {
        std::wstring s = Trim(L[i]);
        bool comment = !s.empty() && (s[0] == L';' || s[0] == L'#');
        std::wstring body = comment ? Trim(s.substr(1)) : s;
        size_t eq = body.find(L'=');
        if (eq == std::wstring::npos || !Same(Trim(body.substr(0, eq)), key)) continue;
        if (!comment) {
            L[i] = line;
            return;
        }
        if (commented == std::wstring::npos) commented = i;
    }
    if (isDefault) return;
    if (commented != std::wstring::npos) {
        L[commented] = line;
        return;
    }
    size_t at = end;
    while (at > start + 1 && Trim(L[at - 1]).empty()) at--;
    L.insert(L.begin() + std::ptrdiff_t(at), line);
}

void TitleWriteAll(TitleFile& t) {
    if (!t.exists && t.lines.empty()) {
        // A new title.ini starts from the example of the port: every option explained, all of them commented.
        std::wstring ex = ReadTextFile(Join(g.root, L"framework\\games\\tsukihime_common\\title.ini.example"));
        t.lines = ex.empty() ? std::vector<std::wstring>{L"; title.ini - video and music of the title screen (written by the launcher).", L"", L"[title]"}
                             : SplitLines(ex);
    }
    static const wchar_t* const kVideo[] = {L"auto", L"1", L"2", L"3", L"4", L"random", L"cycle"};
    TitleSet(t, L"video", kVideo[t.video], t.video == 0);
    std::wstring pool;
    for (size_t i = 0; i < t.pool.size(); i++) pool += (i ? L", " : L"") + Format(L"%d", t.pool[i]);
    std::vector<int> def = {1, 2, 3, 4}, sorted = t.pool;
    std::sort(sorted.begin(), sorted.end());
    TitleSet(t, L"pool", pool, sorted == def);
    static const wchar_t* const kMusic[] = {L"follow", L"game", L"1", L"2", L"3", L"4"};
    TitleSet(t, L"music", kMusic[t.music], t.music == 0);
    TitleSet(t, L"hotkeys", t.hotkeys ? L"on" : L"off", t.hotkeys);
    TitleSet(t, L"click", t.click ? L"on" : L"off", t.click);
    TitleSet(t, L"unlock", t.unlock ? L"on" : L"off", t.unlock);
    TitleSet(t, L"fade", Format(L"%d", t.fade), t.fade == 500);
    TitleSet(t, L"swap_delay", Format(L"%d", t.delay), t.delay == 450);
}

// ---------------------------------------------------------------------------------------------- the tab's state
struct State {
    HWND dlg = nullptr, tabs = nullptr;
    HWND pages[2] = {};
    HWND list = nullptr;
    int ed = -1;
    int dev = kKb;
    bool loading = false;
    bool dirty = true;
    ConfigFile cfg;
    InputFile in;
    TitleFile ti;
} s;

// The Options page (config.txt) lives in the Play and saves tab (CreateOptionsPage); its own state.
struct OptState {
    HWND page = nullptr;
    int ed = -1;
    bool loading = false;
    bool dirty = true;
    ConfigFile cfg;
    std::vector<std::pair<std::wstring, std::wstring>> gpus;   // value, text
} o;

HWND Page(int i) { return s.pages[i]; }

void Note(const std::wstring& extra) {
    std::wstring t = L2(L"The game read the config when it starts.",
                        L"El juego lee los cambios al iniciar.");
    if (GameRunningNow(0) || GameRunningNow(1))
        t = L2(L"The game is running: the changes apply the next time you start it.",
               L"El juego est\u00e1 abierto: los cambios valen la pr\u00f3xima vez que lo inicies.");
    if (!extra.empty()) t = extra;
    SetDlgItemTextW(s.dlg, IDC_CTL_NOTE, t.c_str());
}

void Saved(bool ok, const std::wstring& path) {
    if (ok) {
        Note(Format(L2(L"Saved: %ls", L"Guardado: %ls"), RelPath(path).c_str()));
    } else {
        DWORD e = GetLastError();
        Message(s.dlg, MB_ICONERROR, Format(L2(L"%ls could not be written (error %lu).", L"No se pudo escribir %ls (error %lu)."), RelPath(path).c_str(), e));
    }
}

// ---------------------------------------------------------------------------------------------- both editions
// The tab applies to both editions: it shows the files of one of them (SourceEdition) and writes every change to the
// input.ini / title.ini of each edition that has a data folder (once if both resolve to the same file).
int SourceEdition() {
    if (DirExists(DataDir(g.edition))) return g.edition;
    if (DirExists(DataDir(1 - g.edition))) return 1 - g.edition;
    return g.edition;
}

std::vector<std::wstring> TargetPaths(const wchar_t* file, const wchar_t* envName) {
    std::vector<std::wstring> v;
    for (int ed : {s.ed, 1 - s.ed}) {
        if (!DirExists(DataDir(ed))) continue;
        std::wstring p = GameFilePath(ed, file, envName, ed == s.ed ? s.cfg : LoadConfig(ed));
        bool dup = false;
        for (const std::wstring& q : v) dup |= Same(p, q);
        if (!dup) v.push_back(p);
    }
    return v;
}

std::wstring PathsText(const std::vector<std::wstring>& paths) {
    std::wstring t;
    for (size_t i = 0; i < paths.size(); i++) t += (i ? L2(L" and ", L" y ") : L"") + RelPath(paths[i]);
    return t;
}

// For the file line of a page: "input.ini of both editions", or the path when there is only one.
std::wstring FileLine(const std::vector<std::wstring>& paths, const std::wstring& fallback) {
    if (paths.size() == 2) {
        std::wstring name = paths[0].substr(paths[0].find_last_of(L"\\/") + 1);
        return Format(L2(L"File: %ls of both editions", L"Archivo: %ls de las dos ediciones"), name.c_str());
    }
    return std::wstring(L2(L"File: ", L"Archivo: ")) + (paths.empty() ? RelPath(fallback) : RelPath(paths[0]));
}

// Writes `text` to every edition's file; the note says where (or the error of the first that failed).
bool WriteAll(const wchar_t* file, const wchar_t* envName, const std::wstring& text) {
    std::vector<std::wstring> paths = TargetPaths(file, envName);
    for (const std::wstring& p : paths)
        if (!WriteText(p, text)) {
            Saved(false, p);
            return false;
        }
    if (!paths.empty()) Note(Format(L2(L"Saved: %ls", L"Guardado: %ls"), PathsText(paths).c_str()));
    return !paths.empty();
}

// An edition that has no copy of a file the other one has gets it (e.g. compiled after the changes were made). If
// both have it and they differ, the note says so: the next change makes them equal again.
std::wstring SyncEditions(const wchar_t* file, const wchar_t* envName, const std::wstring& source) {
    if (!FileExists(source)) return L"";
    std::wstring text = ReadTextFile(source), note;
    for (const std::wstring& p : TargetPaths(file, envName)) {
        if (Same(p, source)) continue;
        if (!FileExists(p)) {
            if (WriteText(p, text))
                note = Format(L2(L"%ls copied to the other edition.", L"%ls copiado a la otra edici\u00f3n."), file);
        } else if (ReadTextFile(p) != text) {
            note = Format(L2(L"The editions have different %ls: %ls is shown; the next change is saved in both.",
                             L"Las ediciones tienen distinto %ls: se muestra %ls; el pr\u00f3ximo cambio se guarda en las dos."),
                          file, RelPath(source).c_str());
        }
    }
    return note;
}

// ---------------------------------------------------------------------------------------------- controls page
std::wstring BindsText(int dev, const std::vector<std::wstring>& v) {
    std::wstring t;
    for (size_t i = 0; i < v.size(); i++) t += (i ? L", " : L"") + Display(dev, v[i]);
    return t;
}

int SelectedAction() { return int(SendMessageW(s.list, LVM_GETNEXTITEM, WPARAM(-1), LVNI_SELECTED)); }

void SetCell(HWND list, int row, int col, const std::wstring& text) {
    LVITEMW it{};
    it.iSubItem = col;
    it.pszText = const_cast<wchar_t*>(text.c_str());
    SendMessageW(list, LVM_SETITEMTEXTW, row, LPARAM(&it));
}

void InputDetail() {
    HWND p = Page(0);
    int a = SelectedAction();
    std::wstring t;
    if (!s.in.present[s.dev]) {
        t = s.dev == kKb ? L2(L"The port's default keyboard.",
                              L"El teclado por defecto del port.")
                          : L2(L"The port's default mouse. The left button is the touch screen; here go the others and the wheel.",
                               L"El mouse por defecto del port. El bot\u00f3n izquierdo es la pantalla t\u00e1ctil; aqu\u00ed van los otros y la rueda.");
    } else if (a >= 0) {
        t = Format(L2(L"%ls (%ls on a Switch). Double-click or \"Change...\" to edit it.", L"%ls (%ls en una Switch). Doble clic o \"Cambiar...\" para editarlo."),
                   ActionText(a).c_str(), kActions[a].sw);
    } else {
        t = s.dev == kMouse ? L2(L"The left button is the touch screen; here go the others and the wheel.",
                                 L"El bot\u00f3n izquierdo es la pantalla t\u00e1ctil: aqu\u00ed van los otros y la rueda.")
                            : L2(L"F11 and Alt+Enter are fullscreen and cannot be assigned.", L"F11 y Alt+Enter son pantalla completa y no se pueden asignar.");
    }
    SetDlgItemTextW(p, IDC_IN_DETAIL, t.c_str());
    bool any = a >= 0;
    EnableWindow(GetDlgItem(p, IDC_IN_EDIT), any);
    EnableWindow(GetDlgItem(p, IDC_IN_CLEAR), any && !s.in.binds[s.dev][a].empty());
    EnableWindow(GetDlgItem(p, IDC_IN_RESET), s.in.present[s.dev]);
}

void FillInput() {
    HWND p = Page(0);
    int keep = SelectedAction();
    int top = int(SendMessageW(s.list, LVM_GETTOPINDEX, 0, 0));
    SendMessageW(s.list, WM_SETREDRAW, FALSE, 0);
    SendMessageW(s.list, LVM_DELETEALLITEMS, 0, 0);
    for (int i = 0; i < kActionCount; i++) {
        std::wstring a = ActionText(i);
        LVITEMW it{};
        it.mask = LVIF_TEXT;
        it.iItem = i;
        it.pszText = const_cast<wchar_t*>(a.c_str());
        SendMessageW(s.list, LVM_INSERTITEMW, 0, LPARAM(&it));
        SetCell(s.list, i, 1, kActions[i].sw);
        SetCell(s.list, i, 2, BindsText(s.dev, s.in.binds[s.dev][i]));
    }
    if (keep >= 0) ListView_SetItemState(s.list, keep, LVIS_SELECTED | LVIS_FOCUSED, LVIS_SELECTED | LVIS_FOCUSED);
    if (top > 0) ListView_EnsureVisible(s.list, std::min(top + ListView_GetCountPerPage(s.list) - 1, kActionCount - 1), FALSE);
    SendMessageW(s.list, WM_SETREDRAW, TRUE, 0);
    InvalidateRect(s.list, nullptr, TRUE);
    CheckRadioButton(p, IDC_IN_KB, IDC_IN_MOUSE, s.dev == kMouse ? IDC_IN_MOUSE : IDC_IN_KB);
    std::vector<std::wstring> inPaths = TargetPaths(L"input.ini", L"NX_INPUT_INI");
    SetDlgItemTextW(p, IDC_IN_FILE, (FileLine(inPaths, s.in.path) +
                                     (s.in.exists ? L"" : L2(L" (not created yet: the defaults)", L" (todav\u00eda no existe: valores por defecto)"))).c_str());
    InputDetail();
}

void SaveInput() {
    bool any = s.in.present[kKb] || s.in.present[kPad] || s.in.present[kMouse] || !s.in.extra.empty();
    bool ok = true;
    if (!any && !s.in.exists) {   // everything default and no file: nothing to write
        FillInput();
        return;
    }
    ok = WriteAll(L"input.ini", L"NX_INPUT_INI", InputText(s.in));
    if (ok) s.in.exists = true;
    FillInput();
}

// The buttons of one action, in a small window: a list, Remove, and a way to add one (press a key, or pick a mouse
// button from the list).
struct BindEdit {
    int dev = kKb;
    int action = 0;
    std::vector<std::wstring> items;
    std::vector<std::wstring> (*others)[kActionCount] = nullptr;   // the other actions, to warn about a repeat
    std::vector<std::pair<int, std::wstring>> moveFrom;              // taken from another action (applied on OK)
    HHOOK hook = nullptr;
    bool capturing = false;
} *g_bind = nullptr;

HWND g_bindDlg = nullptr;

LRESULT CALLBACK KeyHook(int code, WPARAM wp, LPARAM lp) {
    if (code == HC_ACTION && g_bind && g_bind->capturing) {
        if (!(lp & 0x80000000)) PostMessageW(g_bindDlg, WM_APP_KEY, wp, 0);
        return 1;   // eaten while capturing (Tab, Esc and Enter would move the dialog)
    }
    return CallNextHookEx(nullptr, code, wp, lp);
}

void BindFill(HWND dlg) {
    HWND lb = GetDlgItem(dlg, IDC_BI_LIST);
    SendMessageW(lb, LB_RESETCONTENT, 0, 0);
    for (const std::wstring& n : g_bind->items) SendMessageW(lb, LB_ADDSTRING, 0, LPARAM(Display(g_bind->dev, n).c_str()));
    EnableWindow(GetDlgItem(dlg, IDC_BI_REMOVE), SendMessageW(lb, LB_GETCURSEL, 0, 0) != LB_ERR);
}

void BindStatus(HWND dlg, const std::wstring& t) { SetDlgItemTextW(dlg, IDC_BI_STATUS, t.c_str()); }

void StopCapture(HWND dlg) {
    BindEdit& b = *g_bind;
    b.capturing = false;
    if (b.hook) UnhookWindowsHookEx(b.hook);
    b.hook = nullptr;
    SetDlgItemTextW(dlg, IDC_BI_CAPTURE, L2(L"Press a key...", L"Presionar una tecla..."));
}

// Adds a name; if another action has it, asks whether to take it from there.
void BindAdd(HWND dlg, const std::wstring& name) {
    BindEdit& b = *g_bind;
    if (std::find(b.items.begin(), b.items.end(), name) != b.items.end()) {
        BindStatus(dlg, Format(L2(L"%ls is already here.", L"%ls ya est\u00e1 aqu\u00ed."), Display(b.dev, name).c_str()));
        return;
    }
    for (int i = 0; i < kActionCount; i++) {
        if (i == b.action) continue;
        const auto& v = (*b.others)[i];
        if (std::find(v.begin(), v.end(), name) == v.end()) continue;
        int r = Message(dlg, MB_ICONQUESTION | MB_YESNOCANCEL,
                        Format(L2(L"%ls is also \"%ls\".\n\nYes: take it from there (only here).\nNo: keep it in both (it does the two things).",
                                  L"%ls tambi\u00e9n es \"%ls\".\n\nS\u00ed: quitarlo de ah\u00ed (solo aqu\u00ed).\nNo: dejarlo en los dos (hace las dos cosas)."),
                               Display(b.dev, name).c_str(), ActionText(i).c_str()));
        if (r == IDCANCEL) return;
        if (r == IDYES) b.moveFrom.push_back({i, name});
        break;
    }
    b.items.push_back(name);
    BindFill(dlg);
    BindStatus(dlg, Format(L2(L"Added: %ls", L"Agregado: %ls"), Display(b.dev, name).c_str()));
}

INT_PTR CALLBACK BindProc(HWND dlg, UINT msg, WPARAM wp, LPARAM) {
    BindEdit* b = g_bind;
    switch (msg) {
    case WM_INITDIALOG: {
        g_bindDlg = dlg;
        ApplyDialogText(dlg, IDD_BIND);
        const wchar_t* devName = b->dev == kKb ? L2(L"keyboard", L"teclado") : L2(L"mouse", L"mouse");
        SetWindowTextW(dlg, Format(L"%ls - %ls", ActionText(b->action).c_str(), devName).c_str());
        const wchar_t* what = b->dev == kKb ? L2(L"Keys", L"Teclas") : L2(L"Mouse buttons", L"Botones del mouse");
        SetDlgItemTextW(dlg, IDC_BI_TEXT, Format(L2(L"%ls for \"%ls\" (%ls on a Switch):", L"%ls para \"%ls\" (%ls en una Switch):"),
                                                 what, ActionText(b->action).c_str(), kActions[b->action].sw).c_str());
        HWND combo = GetDlgItem(dlg, IDC_BI_COMBO);
        if (b->dev == kMouse)
            for (const MouseName& m : kMouseNames) SendMessageW(combo, CB_ADDSTRING, 0, LPARAM(g_spanish ? m.es : m.en));
        SendMessageW(combo, CB_SETCURSEL, 0, 0);
        ShowWindow(combo, b->dev == kKb ? SW_HIDE : SW_SHOW);
        ShowWindow(GetDlgItem(dlg, IDC_BI_ADD), b->dev == kKb ? SW_HIDE : SW_SHOW);
        ShowWindow(GetDlgItem(dlg, IDC_BI_CAPTURE), b->dev == kKb ? SW_SHOW : SW_HIDE);
        StopCapture(dlg);
        BindFill(dlg);
        return TRUE;
    }
    case WM_APP_KEY: {
        if (!b->capturing) return TRUE;
        int vk = int(wp);
        StopCapture(dlg);
        if (vk == kVkF11) {
            BindStatus(dlg, L2(L"F11 is fullscreen: it cannot be assigned.", L"F11 es pantalla completa: no se puede asignar."));
            return TRUE;
        }
        std::wstring name = VkName(vk);
        if (name.empty()) {
            BindStatus(dlg, L2(L"The game does not know that key (letters, numbers, F1-F24, numpad, arrows, Space, Enter, Esc, Tab, Shift, Ctrl, Alt...).",
                               L"El juego no conoce esa tecla (letras, n\u00fameros, F1-F24, teclado num\u00e9rico, flechas, Espacio, Enter, Esc, Tab, Shift, Ctrl, Alt...)."));
            return TRUE;
        }
        BindAdd(dlg, name);
        return TRUE;
    }
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case IDC_BI_LIST:
            if (HIWORD(wp) == LBN_SELCHANGE) EnableWindow(GetDlgItem(dlg, IDC_BI_REMOVE), TRUE);
            return TRUE;
        case IDC_BI_REMOVE: {
            int i = int(SendDlgItemMessageW(dlg, IDC_BI_LIST, LB_GETCURSEL, 0, 0));
            if (i >= 0 && i < int(b->items.size())) b->items.erase(b->items.begin() + i);
            BindFill(dlg);
            return TRUE;
        }
        case IDC_BI_ADD: {
            int i = int(SendDlgItemMessageW(dlg, IDC_BI_COMBO, CB_GETCURSEL, 0, 0));
            if (i < 0) return TRUE;
            BindAdd(dlg, kMouseNames[i].name);
            return TRUE;
        }
        case IDC_BI_CAPTURE:
            if (b->capturing) {
                StopCapture(dlg);
                BindStatus(dlg, L"");
                return TRUE;
            }
            b->capturing = true;
            SetDlgItemTextW(dlg, IDC_BI_CAPTURE, L2(L"Cancel", L"Cancelar"));
            b->hook = SetWindowsHookExW(WH_KEYBOARD, KeyHook, nullptr, GetCurrentThreadId());
            BindStatus(dlg, L2(L"Press the key now (Esc, Tab and Enter count too).", L"Presiona la tecla ahora (Esc, Tab y Enter tambi\u00e9n cuentan)."));
            return TRUE;
        case IDOK:
        case IDCANCEL:
            StopCapture(dlg);
            EndDialog(dlg, LOWORD(wp));
            return TRUE;
        }
        break;
    case WM_DESTROY:
        g_bindDlg = nullptr;
        break;
    }
    return FALSE;
}

void EditAction(int a) {
    if (a < 0) return;
    BindEdit b;
    b.dev = s.dev;
    b.action = a;
    b.items = s.in.binds[s.dev][a];
    b.others = &s.in.binds[s.dev];
    g_bind = &b;
    INT_PTR r = DialogBoxParamW(g.inst, MAKEINTRESOURCEW(IDD_BIND), Page(0), BindProc, 0);
    g_bind = nullptr;
    if (r != IDOK) return;
    if (!s.in.present[s.dev]) {   // the first change of a device writes all of it (the section replaces the defaults)
        s.in.present[s.dev] = true;
        if (s.in.head[s.dev].empty()) s.in.head[s.dev] = kSectionNames[s.dev];
    }
    for (const auto& m : b.moveFrom) {
        auto& v = s.in.binds[s.dev][m.first];
        v.erase(std::remove(v.begin(), v.end(), m.second), v.end());
    }
    s.in.binds[s.dev][a] = b.items;
    SaveInput();
}

void ClearAction(int a) {
    if (a < 0) return;
    if (!s.in.present[s.dev]) {
        s.in.present[s.dev] = true;
        s.in.head[s.dev] = kSectionNames[s.dev];
    }
    s.in.binds[s.dev][a].clear();
    SaveInput();
}

void ResetDevice() {
    const wchar_t* devName = s.dev == kKb ? L2(L"keyboard", L"teclado") : L2(L"mouse", L"mouse");
    if (Message(s.dlg, MB_ICONQUESTION | MB_YESNO | MB_DEFBUTTON2,
                Format(L2(L"Go back to the port's default %ls? Its section of input.ini is removed.", L"\u00bfVolver al %ls por defecto del port? Se quita su secci\u00f3n de input.ini."), devName)) != IDYES)
        return;
    s.in.present[s.dev] = false;
    s.in.kept[s.dev].clear();
    Defaults(s.dev, s.in.binds[s.dev]);
    SaveInput();
}

// ---------------------------------------------------------------------------------------------- title page
void FillTitle() {
    HWND p = Page(1);
    s.loading = true;
    SendDlgItemMessageW(p, IDC_TI_VIDEO, CB_SETCURSEL, s.ti.video, 0);
    for (int i = 0; i < 4; i++)
        CheckDlgButton(p, IDC_TI_POOL1 + i, std::find(s.ti.pool.begin(), s.ti.pool.end(), i + 1) != s.ti.pool.end() ? BST_CHECKED : BST_UNCHECKED);
    // music combo: 0 follow, 1 game, 2 = music of videos 1-3 (internal 2, 3 or 4: one track), 3 = music of video 4
    SendDlgItemMessageW(p, IDC_TI_MUSIC, CB_SETCURSEL, s.ti.music <= 1 ? s.ti.music : s.ti.music == 5 ? 3 : 2, 0);
    CheckDlgButton(p, IDC_TI_HOTKEYS, s.ti.hotkeys ? BST_CHECKED : BST_UNCHECKED);
    CheckDlgButton(p, IDC_TI_CLICK, s.ti.click ? BST_CHECKED : BST_UNCHECKED);
    CheckDlgButton(p, IDC_TI_UNLOCK, s.ti.unlock ? BST_CHECKED : BST_UNCHECKED);
    SetDlgItemTextW(p, IDC_TI_FADE, Format(L"%d", s.ti.fade).c_str());
    SetDlgItemTextW(p, IDC_TI_DELAY, Format(L"%d", s.ti.delay).c_str());
    bool pool = s.ti.video == 5 || s.ti.video == 6;
    for (int id = IDC_TI_POOL1; id <= IDC_TI_POOL4; id++) EnableWindow(GetDlgItem(p, id), pool);
    EnableWindow(GetDlgItem(p, IDC_TI_POOL_L), pool);
    std::vector<std::wstring> tiPaths = TargetPaths(L"title.ini", L"NX_TITLE_INI");
    SetDlgItemTextW(p, IDC_TI_FILE, (FileLine(tiPaths, s.ti.path) +
                                     (s.ti.exists ? L"" : L2(L" (not created yet: the defaults)", L" (todav\u00eda no existe: valores por defecto)"))).c_str());
    s.loading = false;
}

void SaveTitle() {
    TitleWriteAll(s.ti);
    bool ok = WriteAll(L"title.ini", L"NX_TITLE_INI", JoinLines(s.ti.lines));
    if (ok) s.ti.exists = true;
    FillTitle();
}

void ReadTitleControls() {
    HWND p = Page(1);
    s.ti.video = std::max(0, int(SendDlgItemMessageW(p, IDC_TI_VIDEO, CB_GETCURSEL, 0, 0)));
    int mi = std::max(0, int(SendDlgItemMessageW(p, IDC_TI_MUSIC, CB_GETCURSEL, 0, 0)));
    if (mi <= 1) s.ti.music = mi;
    else if (mi == 3) s.ti.music = 5;
    else if (s.ti.music < 2 || s.ti.music > 4) s.ti.music = 2;   // keeps 2 / 3 / 4 if title.ini had one of them (same track)
    std::vector<int> pool;
    for (int i = 0; i < 4; i++) if (IsDlgButtonChecked(p, IDC_TI_POOL1 + i) == BST_CHECKED) pool.push_back(i + 1);
    for (int x : s.ti.pool) if (x > 4) pool.push_back(x);   // numbers of videos beyond the four of the game stay
    if (!pool.empty()) s.ti.pool = pool;
    s.ti.hotkeys = IsDlgButtonChecked(p, IDC_TI_HOTKEYS) == BST_CHECKED;
    s.ti.click = IsDlgButtonChecked(p, IDC_TI_CLICK) == BST_CHECKED;
    s.ti.unlock = IsDlgButtonChecked(p, IDC_TI_UNLOCK) == BST_CHECKED;
}

// ---------------------------------------------------------------------------------------------- options page
struct Choice {
    const wchar_t* value;
    const wchar_t* en;
    const wchar_t* es;
};
const Choice kRes[] = {
    {L"", L"Default 1920x1080", L"Por defecto 1920x1080"},
    {L"1280x720", L"1280x720", L"1280x720"},
    {L"2560x1440", L"2560x1440", L"2560x1440"},
    {L"3840x2160", L"3840x2160", L"3840x2160"},
    {L"*", L"Other...", L"Otra..."},
};
const Choice kFps[] = {
    {L"", L"60 FPS", L"60 FPS"},
    {L"30 FPS", L"30 FPS", L"30 FPS"},
};
const Choice kHud[] = {
    {L"", L"Automatic", L"Autom\u00e1tico"},
    {L"xbox", L"Xbox", L"Xbox"},
    {L"ps", L"PlayStation", L"PlayStation"},
    {L"switch", L"Switch", L"Switch"},
    {L"keyboard", L"Keyboard", L"Teclado"},
    {L"off", L"Off", L"Desactivados"},
};

void FillChoices(HWND combo, const Choice* c, int n) {
    SendMessageW(combo, CB_RESETCONTENT, 0, 0);
    for (int i = 0; i < n; i++) SendMessageW(combo, CB_ADDSTRING, 0, LPARAM(g_spanish ? c[i].es : c[i].en));
}

int ChoiceIndex(const Choice* c, int n, const std::wstring& v) {
    for (int i = 0; i < n; i++) if (Same(v, c[i].value)) return i;
    return -1;
}

// The GPUs of the PC, by name (DXGI, loaded at run time: no import library). NX_GPU takes a part of the Vulkan name.
// The list is "Automatic" plus one item per graphics card; "discrete" / "integrated" written by hand still work.
void ListGpus() {
    o.gpus.clear();
    o.gpus.push_back({L"", L2(L"Automatic", L"Autom\u00e1tica")});
    HMODULE dxgi = LoadLibraryW(L"dxgi.dll");
    if (!dxgi) return;
    using Create = HRESULT(WINAPI*)(REFIID, void**);
    auto create = reinterpret_cast<Create>(reinterpret_cast<void*>(GetProcAddress(dxgi, "CreateDXGIFactory1")));
    static const GUID kIid = {0x770aae78, 0xf26f, 0x4dba, {0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87}};   // IID_IDXGIFactory1
    // The few vtable slots used (IDXGIFactory1: EnumAdapters1 = 12; IDXGIAdapter1: GetDesc1 = 10; Release = 2).
    struct Desc1 { WCHAR Description[128]; UINT VendorId, DeviceId, SubSysId, Revision; SIZE_T Ded, Sys, Shared; LUID Luid; UINT Flags; };
    void* factory = nullptr;
    if (create && SUCCEEDED(create(kIid, &factory)) && factory) {
        auto vt = *reinterpret_cast<void***>(factory);
        auto enumAdapters = reinterpret_cast<HRESULT(STDMETHODCALLTYPE*)(void*, UINT, void**)>(vt[12]);
        for (UINT i = 0; i < 16; i++) {
            void* adapter = nullptr;
            if (FAILED(enumAdapters(factory, i, &adapter)) || !adapter) break;
            auto avt = *reinterpret_cast<void***>(adapter);
            Desc1 d{};
            if (SUCCEEDED(reinterpret_cast<HRESULT(STDMETHODCALLTYPE*)(void*, Desc1*)>(avt[10])(adapter, &d)) && !(d.Flags & 2)) {   // 2 = software
                std::wstring name = Trim(d.Description);
                bool dup = false;
                for (const auto& gpu : o.gpus) dup |= Same(gpu.first, name);
                if (!name.empty() && !dup) o.gpus.push_back({name, name});
            }
            reinterpret_cast<ULONG(STDMETHODCALLTYPE*)(void*)>(avt[2])(adapter);
        }
        reinterpret_cast<ULONG(STDMETHODCALLTYPE*)(void*)>(vt[2])(factory);
    }
    FreeLibrary(dxgi);
}

// The notice of the Options page goes in its file line (the page has no note of its own).
void OptNote(const std::wstring& t) { SetDlgItemTextW(o.page, IDC_OP_FILE, t.c_str()); }

void OptSaved(bool ok, const std::wstring& path) {
    if (ok) {
        std::wstring t = Format(L2(L"Saved: %ls", L"Guardado: %ls"), RelPath(path).c_str());
        if (o.ed >= 0 && GameRunningNow(o.ed)) t += L2(L". Applies the next time you start the game", L". Vale la pr\u00f3xima vez que inicies el juego");
        OptNote(t);
    } else {
        DWORD e = GetLastError();
        Message(o.page, MB_ICONERROR, Format(L2(L"%ls could not be written, error %lu.", L"No se pudo escribir %ls, error %lu."), RelPath(path).c_str(), e));
    }
}

void FillOptions() {
    HWND p = o.page;
    o.loading = true;
    const ConfigFile& f = o.cfg;
    CheckDlgButton(p, IDC_OP_FULLSCREEN, ConfigGet(f, L"NX_FULLSCREEN") == L"1" ? BST_CHECKED : BST_UNCHECKED);
    std::wstring res = ConfigGet(f, L"NX_RENDER_RES");
    int ri = ChoiceIndex(kRes, 4, res);
    if (ri < 0) ri = 4;
    SendDlgItemMessageW(p, IDC_OP_RES, CB_SETCURSEL, ri, 0);
    SetDlgItemTextW(p, IDC_OP_RES_CUSTOM, ri == 4 ? res.c_str() : L"");
    ShowWindow(GetDlgItem(p, IDC_OP_RES_CUSTOM), ri == 4 ? SW_SHOW : SW_HIDE);
    std::wstring fps = ConfigGet(f, L"NX_FPS_CAP");
    int cap = _wtoi(fps.c_str());
    SendDlgItemMessageW(p, IDC_OP_FPS, CB_SETCURSEL, cap > 0 && cap <= 30 ? 1 : 0, 0);
    CheckDlgButton(p, IDC_OP_FIFO, !ConfigGet(f, L"NX_FIFO").empty() && ConfigGet(f, L"NX_FIFO") != L"0" ? BST_CHECKED : BST_UNCHECKED);
    std::wstring gpu = ConfigGet(f, L"NX_GPU");
    HWND gc = GetDlgItem(p, IDC_OP_GPU);
    SendMessageW(gc, CB_RESETCONTENT, 0, 0);
    int gi = -1;
    for (size_t i = 0; i < o.gpus.size(); i++) {
        SendMessageW(gc, CB_ADDSTRING, 0, LPARAM(o.gpus[i].second.c_str()));
        if (Same(o.gpus[i].first, gpu)) gi = int(i);
    }
    if (gi < 0) {   // a value written by hand (an index, a part of a name): shown as it is
        o.gpus.push_back({gpu, gpu});
        SendMessageW(gc, CB_ADDSTRING, 0, LPARAM(gpu.c_str()));
        gi = int(o.gpus.size()) - 1;
    }
    SendMessageW(gc, CB_SETCURSEL, gi, 0);
    std::wstring hud = Lower(ConfigGet(f, L"NX_HUD_GLYPHS"));
    if (hud == L"auto") hud.clear();
    if (hud == L"playstation") hud = L"ps";
    if (hud == L"teclado") hud = L"keyboard";
    if (hud == L"0") hud = L"off";
    int hi = ChoiceIndex(kHud, 6, hud);
    SendDlgItemMessageW(p, IDC_OP_HUD, CB_SETCURSEL, hi < 0 ? 0 : hi, 0);
    CheckDlgButton(p, IDC_OP_SDL, ConfigGet(f, L"NX_SDL_PAD").rfind(L"0", 0) == 0 ? BST_UNCHECKED : BST_CHECKED);
    CheckDlgButton(p, IDC_OP_DISCORD, ConfigGet(f, L"NX_DISCORD") == L"0" ? BST_UNCHECKED : BST_CHECKED);
    CheckDlgButton(p, IDC_OP_MUTE, !ConfigGet(f, L"NX_MUTE").empty() && ConfigGet(f, L"NX_MUTE") != L"0" ? BST_CHECKED : BST_UNCHECKED);

    SetDlgItemTextW(p, IDC_OP_FILE, (std::wstring(L2(L"File: ", L"Archivo: ")) + RelPath(f.path) +
                                     (f.exists ? L"" : L2(L". Not created yet, the defaults apply", L". Todav\u00eda no existe, valen los valores por defecto"))).c_str());
    std::wstring warn;
    if (FileExists(ExePath(o.ed)) && !ExeReadsConfig(o.ed))
        warn = L2(L"This build does not read config.txt yet: apply tools\\patches\\apply_config_env.py and recompile.",
                  L"Esta compilaci\u00f3n a\u00fan no lee config.txt: aplica tools\\patches\\apply_config_env.py y recompila.");
    SetDlgItemTextW(p, IDC_OP_WARN, warn.c_str());
    o.loading = false;
}

void SaveOptions() {
    HWND p = o.page;
    ConfigFile& f = o.cfg;
    ConfigSet(f, L"NX_FULLSCREEN", IsDlgButtonChecked(p, IDC_OP_FULLSCREEN) == BST_CHECKED ? L"1" : L"");
    int ri = int(SendDlgItemMessageW(p, IDC_OP_RES, CB_GETCURSEL, 0, 0));
    std::wstring res = ri >= 0 && ri < 4 ? kRes[ri].value : Trim(GetDlgText(p, IDC_OP_RES_CUSTOM));
    unsigned w = 0, h = 0;
    if (ri == 4 && !(swscanf(res.c_str(), L"%ux%u", &w, &h) == 2 && w >= 320 && h >= 180)) res = ConfigGet(f, L"NX_RENDER_RES");   // until it is valid
    ConfigSet(f, L"NX_RENDER_RES", res);
    int fi = int(SendDlgItemMessageW(p, IDC_OP_FPS, CB_GETCURSEL, 0, 0));
    ConfigSet(f, L"NX_FPS_CAP", fi == 1 ? L"30" : L"");
    ConfigSet(f, L"NX_FIFO", IsDlgButtonChecked(p, IDC_OP_FIFO) == BST_CHECKED ? L"1" : L"");
    int gi = int(SendDlgItemMessageW(p, IDC_OP_GPU, CB_GETCURSEL, 0, 0));
    ConfigSet(f, L"NX_GPU", gi >= 0 && gi < int(o.gpus.size()) ? o.gpus[size_t(gi)].first : L"");
    int hi = int(SendDlgItemMessageW(p, IDC_OP_HUD, CB_GETCURSEL, 0, 0));
    ConfigSet(f, L"NX_HUD_GLYPHS", hi >= 0 ? kHud[hi].value : L"");
    ConfigSet(f, L"NX_SDL_PAD", IsDlgButtonChecked(p, IDC_OP_SDL) == BST_CHECKED ? L"" : L"0");
    ConfigSet(f, L"NX_DISCORD", IsDlgButtonChecked(p, IDC_OP_DISCORD) == BST_CHECKED ? L"" : L"0");
    ConfigSet(f, L"NX_MUTE", IsDlgButtonChecked(p, IDC_OP_MUTE) == BST_CHECKED ? L"1" : L"");
    bool ok = true;
    bool onlyHeader = true;
    std::wstring k;
    for (const std::wstring& l : f.lines) onlyHeader &= !ConfigKeyOf(l, &k, nullptr) && (Trim(l).empty() || Trim(l)[0] == L';' || Trim(l)[0] == L'#');
    if (onlyHeader && !f.exists) {
        FillOptions();   // all defaults and no file: nothing to write
        return;
    }
    ok = WriteText(f.path, JoinLines(f.lines));
    if (ok) f.exists = true;
    FillOptions();
    OptSaved(ok, f.path);
}

void ResetOptions() {
    if (Message(o.page, MB_ICONQUESTION | MB_YESNO | MB_DEFBUTTON2,
                L2(L"Put every option of this page back to the game's default? The other lines of config.txt stay.",
                   L"\u00bfVolver todas las opciones de esta p\u00e1gina a las del juego? Las dem\u00e1s l\u00edneas de config.txt se quedan.")) != IDYES)
        return;
    for (const wchar_t* key : {L"NX_FULLSCREEN", L"NX_RENDER_RES", L"NX_FSR", L"NX_FSR_SHARP", L"NX_FPS_CAP", L"NX_FIFO", L"NX_GPU",
                               L"NX_HUD_GLYPHS", L"NX_SDL_PAD", L"NX_DISCORD", L"NX_MUTE"})
        ConfigSet(o.cfg, key, L"");
    bool ok = !o.cfg.exists || WriteText(o.cfg.path, JoinLines(o.cfg.lines));
    FillOptions();
    if (o.cfg.exists) OptSaved(ok, o.cfg.path);
}

void OpenFile(const std::wstring& path, const std::wstring& seed) {   // created with seed if it does not exist
    if (!FileExists(path) && !WriteText(path, seed)) return;
    OpenInShell(s.dlg, path);
}

// ---------------------------------------------------------------------------------------------- loading
void ShowInner(int i) {
    TabCtrl_SetCurSel(s.tabs, i);
    for (int k = 0; k < 2; k++) ShowWindow(s.pages[k], k == i ? SW_SHOW : SW_HIDE);
}

// Reads the files of the source edition (after giving the other edition what it lacks). Returns the sync note.
std::wstring ReadFiles() {
    s.ed = SourceEdition();
    s.cfg = LoadConfig(s.ed);
    s.in = LoadInput(s.ed, s.cfg);
    s.ti = LoadTitle(s.ed, s.cfg);
    std::wstring a = SyncEditions(L"input.ini", L"NX_INPUT_INI", s.in.path);
    std::wstring b = SyncEditions(L"title.ini", L"NX_TITLE_INI", s.ti.path);
    return a.empty() ? b : b.empty() ? a : a + L" " + b;
}

void LoadAll() {
    std::wstring sync = ReadFiles();
    bool data = DirExists(DataDir(s.ed));
    for (HWND p : s.pages) {
        for (HWND c = GetWindow(p, GW_CHILD); c; c = GetWindow(c, GW_HWNDNEXT)) EnableWindow(c, data);
    }
    FillInput();
    FillTitle();
    if (!data)   // nothing to edit until the edition is compiled (the files go to its data folder)
        for (HWND p : s.pages)
            for (HWND c = GetWindow(p, GW_CHILD); c; c = GetWindow(c, GW_HWNDNEXT)) EnableWindow(c, FALSE);
    Note(data ? sync : L2(L"No edition is compiled yet: the data folder appears when you compile one.",
                          L"Todav\u00eda no hay ninguna edici\u00f3n compilada: la carpeta de datos aparece al compilar una."));
    s.dirty = false;
}

void SetupInputList() {
    s.list = GetDlgItem(Page(0), IDC_IN_LIST);
    DWORD ex = LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER | LVS_EX_LABELTIP;
    SendMessageW(s.list, LVM_SETEXTENDEDLISTVIEWSTYLE, ex, ex);
    SetWindowTheme(s.list, L"Explorer", nullptr);
    RECT rc;
    GetClientRect(s.list, &rc);
    int width = rc.right - GetSystemMetrics(SM_CXVSCROLL) - 2;
    const std::pair<std::wstring, int> cols[] = {{L2(L"Action", L"Acci\u00f3n"), 46}, {L2(L"Switch", L"Switch"), 12}, {L2(L"Assigned to", L"Asignado a"), 42}};
    for (int i = 0; i < 3; i++) {
        LVCOLUMNW c{};
        c.mask = LVCF_TEXT | LVCF_WIDTH;
        c.cx = width * cols[i].second / 100;
        c.pszText = const_cast<wchar_t*>(cols[i].first.c_str());
        SendMessageW(s.list, LVM_INSERTCOLUMNW, i, LPARAM(&c));
    }
}

// ---------------------------------------------------------------------------------------------- inner pages
INT_PTR CALLBACK InputPageProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_INITDIALOG:
        EnableThemeDialogTexture(dlg, ETDT_ENABLETAB);
        ApplyDialogText(dlg, IDD_CTL_INPUT);
        return TRUE;
    case WM_NOTIFY: {
        auto* nm = reinterpret_cast<NMHDR*>(lp);
        if (nm->hwndFrom == s.list && nm->code == LVN_ITEMCHANGED) {
            auto* ch = reinterpret_cast<NMLISTVIEW*>(lp);
            if ((ch->uChanged & LVIF_STATE) && ((ch->uNewState ^ ch->uOldState) & LVIS_SELECTED)) InputDetail();
        }
        if (nm->hwndFrom == s.list && nm->code == LVN_ITEMACTIVATE && SelectedAction() >= 0)   // double click or Enter
            PostMessageW(dlg, WM_COMMAND, IDC_IN_EDIT, 0);
        break;
    }
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case IDC_IN_KB:
        case IDC_IN_MOUSE:
            if (HIWORD(wp) == BN_CLICKED) {
                s.dev = LOWORD(wp) == IDC_IN_KB ? kKb : kMouse;
                FillInput();
            }
            return TRUE;
        case IDC_IN_EDIT: EditAction(SelectedAction()); return TRUE;
        case IDC_IN_CLEAR: ClearAction(SelectedAction()); return TRUE;
        case IDC_IN_RESET: ResetDevice(); return TRUE;
        case IDC_IN_OPEN: OpenFile(s.in.path, InputText(s.in)); return TRUE;
        }
        break;
    }
    return FALSE;
}

INT_PTR CALLBACK TitlePageProc(HWND dlg, UINT msg, WPARAM wp, LPARAM) {
    switch (msg) {
    case WM_INITDIALOG: {
        EnableThemeDialogTexture(dlg, ETDT_ENABLETAB);
        ApplyDialogText(dlg, IDD_CTL_TITLE);
        HWND v = GetDlgItem(dlg, IDC_TI_VIDEO);
        const wchar_t* videos[] = {L2(L"The game's choice", L"El que elige el juego"), L2(L"Default", L"Predeterminado"),
                                   L2(L"Arcueid route cleared", L"Ruta de Arcueid completada"), L2(L"Start of Ciel's route", L"Inicio de ruta de Ciel"),
                                   L2(L"Game completed", L"Juego completado"),
                                   L2(L"Random (another one each time)", L"Al azar (otro cada vez)"), L2(L"In turn (one after the other)", L"Por turnos (uno tras otro)")};
        for (const wchar_t* t : videos) SendMessageW(v, CB_ADDSTRING, 0, LPARAM(t));
        HWND m = GetDlgItem(dlg, IDC_TI_MUSIC);
        const wchar_t* music[] = {L2(L"The one of the video shown", L"La del video que se muestra"), L2(L"The game's own", L"La del juego"),
                                  L2(L"Always the one of videos 1-3", L"Siempre la del video 1-3"), L2(L"Always the one of video 4", L"Siempre la del video 4")};
        for (const wchar_t* t : music) SendMessageW(m, CB_ADDSTRING, 0, LPARAM(t));
        SendDlgItemMessageW(dlg, IDC_TI_FADE, EM_LIMITTEXT, 4, 0);
        SendDlgItemMessageW(dlg, IDC_TI_DELAY, EM_LIMITTEXT, 4, 0);
        SetDlgItemTextW(dlg, IDC_TI_NOTE, L2(L"Videos 1-3 (Default, Arcueid route cleared, Start of Ciel's route) share one music and video 4 (Game completed) has its own.",
                                             L"Los videos 1-3 (Predeterminado, Ruta de Arcueid completada, Inicio de ruta de Ciel) comparten una m\u00fasica y el 4 (Juego completado) tiene la suya."));
        return TRUE;
    }
    case WM_COMMAND: {
        if (s.loading) break;
        int id = LOWORD(wp), code = HIWORD(wp);
        if ((id == IDC_TI_VIDEO || id == IDC_TI_MUSIC) && code == CBN_SELCHANGE) {
            ReadTitleControls();
            SaveTitle();
            return TRUE;
        }
        if ((id >= IDC_TI_POOL1 && id <= IDC_TI_POOL4) && code == BN_CLICKED) {
            bool any = false;
            for (int i = IDC_TI_POOL1; i <= IDC_TI_POOL4; i++) any |= IsDlgButtonChecked(dlg, i) == BST_CHECKED;
            if (!any) {   // the game needs one at least
                CheckDlgButton(dlg, id, BST_CHECKED);
                Note(L2(L"At least one video has to stay ticked.", L"Tiene que quedar al menos un video marcado."));
                return TRUE;
            }
            ReadTitleControls();
            SaveTitle();
            return TRUE;
        }
        if ((id == IDC_TI_HOTKEYS || id == IDC_TI_CLICK || id == IDC_TI_UNLOCK) && code == BN_CLICKED) {
            ReadTitleControls();
            SaveTitle();
            return TRUE;
        }
        if ((id == IDC_TI_FADE || id == IDC_TI_DELAY) && code == EN_KILLFOCUS) {
            std::wstring t = Trim(GetDlgText(dlg, id));
            int v = _wtoi(t.c_str());
            if (t.empty() || v > 5000) {
                Note(L2(L"From 0 to 5000 milliseconds.", L"De 0 a 5000 milisegundos."));
                SetDlgItemTextW(dlg, id, Format(L"%d", id == IDC_TI_FADE ? s.ti.fade : s.ti.delay).c_str());
                return TRUE;
            }
            int& field = id == IDC_TI_FADE ? s.ti.fade : s.ti.delay;
            if (v == field) return TRUE;
            field = v;
            ReadTitleControls();
            SaveTitle();
            return TRUE;
        }
        if (id == IDC_TI_RESET) {
            if (Message(s.dlg, MB_ICONQUESTION | MB_YESNO | MB_DEFBUTTON2,
                        L2(L"Put the title screen back to the game's own behaviour?", L"\u00bfVolver la pantalla de t\u00edtulo a como la maneja el juego?")) != IDYES)
                return TRUE;
            TitleFile d;
            s.ti.video = d.video, s.ti.music = d.music, s.ti.hotkeys = d.hotkeys, s.ti.click = d.click, s.ti.unlock = d.unlock;
            s.ti.fade = d.fade, s.ti.delay = d.delay, s.ti.pool = d.pool;
            if (s.ti.exists) SaveTitle();
            else FillTitle();
            return TRUE;
        }
        if (id == IDC_TI_OPEN) {
            if (!s.ti.exists) {
                TitleWriteAll(s.ti);
                s.ti.exists = WriteAll(L"title.ini", L"NX_TITLE_INI", JoinLines(s.ti.lines));
                FillTitle();
            }
            OpenInShell(s.dlg, s.ti.path);
            return TRUE;
        }
        break;
    }
    }
    return FALSE;
}

void LoadOptions() {
    o.ed = g.edition;
    o.cfg = LoadConfig(o.ed);
    bool data = DirExists(DataDir(o.ed));
    if (o.cfg.exists) {   // FSR is no longer offered (it flips the picture): the lines an older launcher wrote are removed
        std::vector<std::wstring> before = o.cfg.lines;
        ConfigSet(o.cfg, L"NX_FSR", L"");
        ConfigSet(o.cfg, L"NX_FSR_SHARP", L"");
        if (o.cfg.lines != before && !WriteText(o.cfg.path, JoinLines(o.cfg.lines))) o.cfg.lines = before;
    }
    for (HWND c = GetWindow(o.page, GW_CHILD); c; c = GetWindow(c, GW_HWNDNEXT)) EnableWindow(c, TRUE);
    FillOptions();
    if (!data) {     // nothing to edit until the edition is compiled (the files go to its data folder)
        for (HWND c = GetWindow(o.page, GW_CHILD); c; c = GetWindow(c, GW_HWNDNEXT)) EnableWindow(c, FALSE);
        EnableWindow(GetDlgItem(o.page, IDC_OP_WARN), TRUE);
        SetDlgItemTextW(o.page, IDC_OP_WARN, L2(L"This edition is not compiled yet: its data folder appears when you compile it.",
                                                 L"Esta edici\u00f3n todav\u00eda no est\u00e1 compilada: su carpeta de datos aparece al compilarla."));
    }
    o.dirty = false;
}

INT_PTR CALLBACK OptionsPageProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_INITDIALOG:
        EnableThemeDialogTexture(dlg, ETDT_ENABLETAB);
        ApplyDialogText(dlg, IDD_CTL_OPTIONS);
        FillChoices(GetDlgItem(dlg, IDC_OP_RES), kRes, 5);
        FillChoices(GetDlgItem(dlg, IDC_OP_FPS), kFps, 2);
        FillChoices(GetDlgItem(dlg, IDC_OP_HUD), kHud, 6);
        SendDlgItemMessageW(dlg, IDC_OP_RES_CUSTOM, EM_LIMITTEXT, 11, 0);
        return TRUE;
    case WM_SHOWWINDOW:   // shown again (sub-tab or main tab): config.txt may have been edited by hand meanwhile
        if (wp) PostMessageW(dlg, WM_APP + 42, 0, 0);
        break;
    case WM_APP + 42:
        LoadOptions();
        return TRUE;
    case WM_CTLCOLORSTATIC:
        if (GetDlgCtrlID(HWND(lp)) == IDC_OP_WARN) return ColorStatic(dlg, wp, lp, IDC_OP_WARN, RGB(190, 0, 0));
        break;
    case WM_COMMAND: {
        if (o.loading) break;
        int id = LOWORD(wp), code = HIWORD(wp);
        if (code == CBN_SELCHANGE && id == IDC_OP_RES) {
            bool custom = SendDlgItemMessageW(dlg, IDC_OP_RES, CB_GETCURSEL, 0, 0) == 4;
            ShowWindow(GetDlgItem(dlg, IDC_OP_RES_CUSTOM), custom ? SW_SHOW : SW_HIDE);
            if (custom) {
                if (Trim(GetDlgText(dlg, IDC_OP_RES_CUSTOM)).empty()) SetDlgItemTextW(dlg, IDC_OP_RES_CUSTOM, L"1600x900");
                SetFocus(GetDlgItem(dlg, IDC_OP_RES_CUSTOM));
                SendDlgItemMessageW(dlg, IDC_OP_RES_CUSTOM, EM_SETSEL, 0, -1);
            }
            SaveOptions();
            return TRUE;
        }
        if (code == CBN_SELCHANGE && (id == IDC_OP_FPS || id == IDC_OP_GPU || id == IDC_OP_HUD)) {
            SaveOptions();
            return TRUE;
        }
        if (code == BN_CLICKED && (id == IDC_OP_FULLSCREEN || id == IDC_OP_FIFO || id == IDC_OP_SDL || id == IDC_OP_DISCORD || id == IDC_OP_MUTE)) {
            SaveOptions();
            return TRUE;
        }
        if (id == IDC_OP_RES_CUSTOM && code == EN_KILLFOCUS) {
            unsigned w = 0, h = 0;
            std::wstring t = Trim(GetDlgText(dlg, IDC_OP_RES_CUSTOM));
            if (!(swscanf(t.c_str(), L"%ux%u", &w, &h) == 2 && w >= 320 && h >= 180 && w <= 7680 && h <= 4320)) {
                OptNote(L2(L"Write the resolution as width x height, for example 1600x900.", L"Escribe la resoluci\u00f3n como ancho x alto, por ejemplo 1600x900."));
                return TRUE;
            }
            SaveOptions();
            return TRUE;
        }
        if (id == IDC_OP_RESET) {
            ResetOptions();
            return TRUE;
        }
        if (id == IDC_OP_OPEN) {
            if (FileExists(o.cfg.path) || WriteText(o.cfg.path, JoinLines(o.cfg.lines.empty() ? ConfigHeader() : o.cfg.lines))) OpenInShell(o.page, o.cfg.path);
            o.cfg = LoadConfig(o.ed);
            FillOptions();
            return TRUE;
        }
        break;
    }
    }
    return FALSE;
}

void PlaceInner() {
    RECT r;
    GetWindowRect(s.tabs, &r);
    TabCtrl_AdjustRect(s.tabs, FALSE, &r);
    MapWindowPoints(nullptr, s.dlg, reinterpret_cast<POINT*>(&r), 2);
    for (HWND p : s.pages) SetWindowPos(p, HWND_TOP, r.left, r.top, r.right - r.left, r.bottom - r.top, SWP_NOACTIVATE);
}

}  // namespace

// ---------------------------------------------------------------------------------------------- the tab
void RefreshControls() {
    if (!s.dlg) return;
    if (!IsWindowVisible(s.dlg)) {
        s.dirty = true;
        return;
    }
    if (s.dirty || s.ed != SourceEdition()) {
        LoadAll();
        return;
    }
    // Files changed by hand meanwhile are read again (they are small); the page keeps its selection.
    std::wstring sync = ReadFiles();
    FillInput();
    FillTitle();
    if (!sync.empty()) Note(sync);
}

// The Options sub-tab of Play and saves: created there, inside its inner tab control.
HWND CreateOptionsPage(HWND parent) {
    o.page = CreateDialogParamW(g.inst, MAKEINTRESOURCEW(IDD_CTL_OPTIONS), parent, OptionsPageProc, 0);
    ListGpus();
    return o.page;
}

void RefreshOptions() {
    if (!o.page) return;
    if (!IsWindowVisible(o.page)) {   // read when it is shown (WM_SHOWWINDOW)
        o.dirty = true;
        return;
    }
    LoadOptions();
}

INT_PTR CALLBACK ControlsProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_INITDIALOG: {
        s.dlg = dlg;
        s.tabs = GetDlgItem(dlg, IDC_CTL_TABS);
        EnableThemeDialogTexture(dlg, ETDT_ENABLETAB);
        ApplyDialogText(dlg, IDD_CONTROLS);
        const wchar_t* names[2] = {L2(L"Controls", L"Controles"), L2(L"Title screen", L"Pantalla de t\u00edtulo")};
        for (int i = 0; i < 2; i++) {
            TCITEMW it{};
            it.mask = TCIF_TEXT;
            it.pszText = const_cast<wchar_t*>(names[i]);
            TabCtrl_InsertItem(s.tabs, i, &it);
        }
        const int ids[2] = {IDD_CTL_INPUT, IDD_CTL_TITLE};
        DLGPROC procs[2] = {InputPageProc, TitlePageProc};
        for (int i = 0; i < 2; i++) s.pages[i] = CreateDialogParamW(g.inst, MAKEINTRESOURCEW(ids[i]), dlg, procs[i], 0);
        PlaceInner();
        SetupInputList();
        ShowInner(0);
        return TRUE;
    }
    case WM_SHOWWINDOW:
        if (wp && s.dirty) PostMessageW(dlg, WM_APP + 41, 0, 0);
        break;
    case WM_APP + 41:
        if (s.dirty) LoadAll();
        return TRUE;
    case WM_NOTIFY: {
        auto* nm = reinterpret_cast<NMHDR*>(lp);
        if (nm->hwndFrom == s.tabs && nm->code == TCN_SELCHANGE) ShowInner(TabCtrl_GetCurSel(s.tabs));
        break;
    }
    }
    return FALSE;
}
