// mods.cpp - the "Mods and updates" tab: the romfs layers of mods\<edition>\ and the .nsp of updates\.
//
// The launcher follows the port's own rules here and changes nothing they decide:
// - tools\recomp_tsukihime.ps1 (StepUpdates): every updates\*.nsp (a name starting with _ is skipped) is processed
//   once, by the recompilation of its edition, into data\<data>\updates\<name>\ (exefs, mod.json and source.txt =
//   "<file>|<size>|<mtime ticks>|<mtime ticks of mods_nsp.py>") plus the layer mods\<edition>\<name>\ (from_update.txt,
//   and requires_main.txt when the update has its own main). That folder is the switch: _<name> turns the update off,
//   deleting it removes the update at the next run. Two active updates with different mains cannot be combined.
// - tools\patches\apply_romfs_mods.py (the game): every folder of mods\<edition>\ with a romfs\ is a layer, in name
//   order (ASCII case-insensitive; the last one wins); a name starting with _ or . is skipped, and so is a layer whose
//   requires_main.txt is not data\<data>\analysis\main_source.txt (the main the exe was built from).
// - Step 8 translates the shaders of the active layers (romfs\shader); tools\internal\tsuki_shaders_offline.py tells
//   how many have no .spv yet, so the tab can say when a mod needs a recompilation for its shaders.
// Changes made here are renames (on/off), copies into mods\ or updates\, and deletions to the Recycle Bin.
#include "common.h"

#include <commctrl.h>
#include <shellapi.h>
#include <uxtheme.h>

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <cwchar>
#include <cwctype>
#include <map>

#include "../res/resource.h"
#include "pages.h"
#include "text.h"

namespace {

constexpr UINT WM_APP_SHADERS = WM_APP + 30;        // lParam = ShaderJob* (the receiver deletes it)
constexpr UINT WM_APP_COPY_PROGRESS = WM_APP + 31;
constexpr UINT WM_APP_COPY_DONE = WM_APP + 32;
constexpr UINT WM_APP_TOGGLE = WM_APP + 33;         // wParam = row of the mods list whose check box changed
constexpr ULONGLONG kTicks1601 = 504911232000000000ull;   // .NET DateTime ticks of 1601-01-01 (the FILETIME epoch)
const wchar_t* const kProgramIds[2] = {L"01001dc01486a000", L"010057a01e4d4000"};   // the japan / global program IDs

// ---------------------------------------------------------------------------------------------- small helpers
std::wstring Trim(std::wstring s) {
    size_t a = 0, b = s.size();
    while (a < b && (iswspace(s[a]) || s[a] == 0xFEFF)) a++;
    while (b > a && iswspace(s[b - 1])) b--;
    return s.substr(a, b - a);
}

std::wstring Lower(std::wstring s) {
    for (wchar_t& c : s) c = wchar_t(towlower(c));
    return s;
}

bool Same(const std::wstring& a, const std::wstring& b) { return _wcsicmp(a.c_str(), b.c_str()) == 0; }

std::wstring Leaf(const std::wstring& p) {
    size_t k = p.find_last_of(L"\\/");
    return k == std::wstring::npos ? p : p.substr(k + 1);
}

std::wstring Parent(const std::wstring& p) {
    size_t k = p.find_last_of(L"\\/");
    return k == std::wstring::npos ? L"" : p.substr(0, k);
}

std::wstring SizeText(ULONGLONG bytes) {
    if (bytes >= (1ull << 30)) return Format(L"%.1f GB", double(bytes) / double(1ull << 30));
    if (bytes >= (1ull << 20)) return Format(L"%.0f MB", double(bytes) / double(1ull << 20));
    return Format(L"%llu KB", (bytes + 1023) / 1024);
}

std::wstring FilesN(int n) { return n == 1 ? std::wstring(L2(L"1 file", L"1 archivo")) : Format(L2(L"%d files", L"%d archivos"), n); }

std::wstring ReadSmall(const std::wstring& p) { return Trim(ReadTextFile(p, 64 * 1024)); }

ULONGLONG Ticks(const FILETIME& ft) { return ((ULONGLONG(ft.dwHighDateTime) << 32) | ft.dwLowDateTime) + kTicks1601; }

std::wstring ModsDir(int ed) { return Join(g.root, std::wstring(L"mods\\") + kEditions[ed].key); }
std::wstring UpdatesDir() { return Join(g.root, L"updates"); }
std::wstring UpdData(int ed) { return Join(DataDir(ed), L"updates"); }
bool IsUpdateLayer(const std::wstring& dir) { return FileExists(Join(dir, L"from_update.txt")); }
bool GameRunning(int ed) { return IsExeRunning(ExePath(ed)); }

// Name of the update an .nsp becomes: recomp_tsukihime.ps1, GetFileNameWithoutExtension -replace '[^A-Za-z0-9._()\[\] +-]', '_'.
std::wstring UpdateName(const std::wstring& file) {
    std::wstring stem = file;
    size_t dot = stem.find_last_of(L'.');
    if (dot != std::wstring::npos && dot > 0) stem.resize(dot);
    for (wchar_t& c : stem) {
        bool ok = (c >= L'A' && c <= L'Z') || (c >= L'a' && c <= L'z') || (c >= L'0' && c <= L'9') ||
                  wcschr(L"._()[] +-", c) != nullptr;
        if (!ok || c == 0) c = L'_';
    }
    return stem;
}

// The order the game reads the layers in: the UTF-8 name with ASCII letters lowercased (apply_romfs_mods.py).
std::string OrderKey(const std::wstring& name) {
    std::string s = WideToUtf8(name);
    for (char& c : s) if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
    return s;
}

bool HasPfs0(const std::wstring& path) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    char buf[4] = {};
    DWORD got = 0;
    bool ok = ReadFile(h, buf, 4, &got, nullptr) && got == 4 && memcmp(buf, "PFS0", 4) == 0;
    CloseHandle(h);
    return ok;
}

// ---------------------------------------------------------------------------------------------- mod.json
// mod.json is written by mods_nsp.py (json.dump, ASCII). Only a few top-level fields are read.
struct ModJson {
    bool ok = false;
    std::wstring nsp, mainSha;
    std::vector<std::wstring> exefsDifferent;
    int changed = -1, added = -1, recipes = 0;
};

struct JVal {
    int type = 0;   // 0 other, 1 string, 2 number, 3 array
    std::string str;
    double num = 0;
    std::vector<std::string> items;   // the string items of an array
    size_t count = 0;                 // all its items
};

struct JCur {
    const std::string& s;
    size_t i = 0;
};

void JWs(JCur& c) {
    while (c.i < c.s.size() && (c.s[c.i] == ' ' || c.s[c.i] == '\t' || c.s[c.i] == '\r' || c.s[c.i] == '\n')) c.i++;
}

void AppendUtf8(std::string& out, unsigned cp) {
    if (cp < 0x80) out += char(cp);
    else if (cp < 0x800) { out += char(0xC0 | (cp >> 6)); out += char(0x80 | (cp & 0x3F)); }
    else if (cp < 0x10000) { out += char(0xE0 | (cp >> 12)); out += char(0x80 | ((cp >> 6) & 0x3F)); out += char(0x80 | (cp & 0x3F)); }
    else { out += char(0xF0 | (cp >> 18)); out += char(0x80 | ((cp >> 12) & 0x3F)); out += char(0x80 | ((cp >> 6) & 0x3F)); out += char(0x80 | (cp & 0x3F)); }
}

bool JHex4(JCur& c, unsigned& v) {
    if (c.i + 4 > c.s.size()) return false;
    v = 0;
    for (int k = 0; k < 4; k++) {
        char h = c.s[c.i++];
        v <<= 4;
        if (h >= '0' && h <= '9') v |= unsigned(h - '0');
        else if (h >= 'a' && h <= 'f') v |= unsigned(h - 'a' + 10);
        else if (h >= 'A' && h <= 'F') v |= unsigned(h - 'A' + 10);
        else return false;
    }
    return true;
}

bool JString(JCur& c, std::string& out) {   // at the opening quote
    out.clear();
    c.i++;
    while (c.i < c.s.size()) {
        char ch = c.s[c.i++];
        if (ch == '"') return true;
        if (ch != '\\') { out += ch; continue; }
        if (c.i >= c.s.size()) return false;
        char e = c.s[c.i++];
        switch (e) {
        case 'n': out += '\n'; break;
        case 't': out += '\t'; break;
        case 'r': out += '\r'; break;
        case 'b': out += '\b'; break;
        case 'f': out += '\f'; break;
        case 'u': {
            unsigned v = 0;
            if (!JHex4(c, v)) return false;
            if (v >= 0xD800 && v < 0xDC00 && c.i + 6 <= c.s.size() && c.s[c.i] == '\\' && c.s[c.i + 1] == 'u') {
                c.i += 2;
                unsigned lo = 0;
                if (!JHex4(c, lo)) return false;
                v = 0x10000 + ((v - 0xD800) << 10) + (lo - 0xDC00);
            }
            AppendUtf8(out, v);
            break;
        }
        default: out += e;   // \" \\ \/
        }
    }
    return false;
}

bool JValue(JCur& c, JVal& v, int depth) {
    JWs(c);
    if (c.i >= c.s.size() || depth > 32) return false;
    char ch = c.s[c.i];
    if (ch == '"') { v.type = 1; return JString(c, v.str); }
    if (ch == '[') {
        v.type = 3;
        c.i++;
        JWs(c);
        if (c.i < c.s.size() && c.s[c.i] == ']') { c.i++; return true; }
        for (;;) {
            JVal item;
            if (!JValue(c, item, depth + 1)) return false;
            v.count++;
            if (item.type == 1) v.items.push_back(item.str);
            JWs(c);
            if (c.i >= c.s.size()) return false;
            if (c.s[c.i] == ',') { c.i++; continue; }
            if (c.s[c.i] == ']') { c.i++; return true; }
            return false;
        }
    }
    if (ch == '{') {
        c.i++;
        JWs(c);
        if (c.i < c.s.size() && c.s[c.i] == '}') { c.i++; return true; }
        for (;;) {
            JWs(c);
            std::string key;
            if (c.i >= c.s.size() || c.s[c.i] != '"' || !JString(c, key)) return false;
            JWs(c);
            if (c.i >= c.s.size() || c.s[c.i] != ':') return false;
            c.i++;
            JVal item;
            if (!JValue(c, item, depth + 1)) return false;
            JWs(c);
            if (c.i >= c.s.size()) return false;
            if (c.s[c.i] == ',') { c.i++; continue; }
            if (c.s[c.i] == '}') { c.i++; return true; }
            return false;
        }
    }
    if (ch == '-' || (ch >= '0' && ch <= '9')) {
        size_t a = c.i;
        while (c.i < c.s.size() && strchr("+-.eE0123456789", c.s[c.i])) c.i++;
        v.type = 2;
        v.num = strtod(c.s.substr(a, c.i - a).c_str(), nullptr);
        return true;
    }
    for (const char* lit : {"true", "false", "null"}) {
        size_t n = strlen(lit);
        if (c.s.compare(c.i, n, lit) == 0) { c.i += n; return true; }
    }
    return false;
}

ModJson ReadModJson(const std::wstring& path) {
    ModJson m;
    std::string text = WideToUtf8(ReadTextFile(path, 16u << 20));
    JCur c{text};
    JWs(c);
    if (c.i >= text.size() || text[c.i] != '{') return m;
    c.i++;
    for (;;) {
        JWs(c);
        if (c.i < text.size() && text[c.i] == '}') break;
        std::string key;
        if (c.i >= text.size() || text[c.i] != '"' || !JString(c, key)) return m;
        JWs(c);
        if (c.i >= text.size() || text[c.i] != ':') return m;
        c.i++;
        JVal v;
        if (!JValue(c, v, 1)) return m;
        if (key == "nsp" && v.type == 1) m.nsp = Utf8ToWide(v.str);
        else if (key == "main_sha256" && v.type == 1) m.mainSha = Lower(Utf8ToWide(v.str));
        else if (key == "exefs_different" && v.type == 3) for (const std::string& it : v.items) m.exefsDifferent.push_back(Utf8ToWide(it));
        else if (key == "changed" && v.type == 2) m.changed = int(v.num);
        else if (key == "new" && v.type == 2) m.added = int(v.num);
        else if (key == "recipes" && v.type == 3) m.recipes = int(v.count);
        JWs(c);
        if (c.i < text.size() && text[c.i] == ',') { c.i++; continue; }
        break;
    }
    m.ok = true;
    return m;
}

bool HasModule(const ModJson& j, const wchar_t* name) {
    for (const std::wstring& m : j.exefsDifferent) if (Same(m, name)) return true;
    return false;
}

// ---------------------------------------------------------------------------------------------- the model
struct Layer {
    std::wstring folder;    // real folder name
    std::wstring name;      // without the _ that turns it off
    bool enabled = false;   // the name does not start with _
    bool update = false;    // from_update.txt: made by the recompilation from an .nsp
    bool romfs = false;     // has romfs\ (else the game ignores it)
    bool updData = false;   // data\<data>\updates\<name> (mod.json + source.txt) is there
    bool ownMain = false;   // the update brings its own main (requires_main.txt)
    std::wstring reqMain;  // requires_main.txt, lowercase
    ModJson json;
    std::vector<std::wstring> otherModules;   // exefs modules it changes besides main (not supported)
    int files = 0, shaders = 0, recipes = 0;
    ULONGLONG bytes = 0;
    bool loadsNow = false;    // the current exe reads it
    bool loadsNext = false;   // the next build will read it
    std::string order;
};

enum class NspState { Ignored, BadHeader, Foreign, Pending, Readd, Processed, Disabled, Reprocess, Changed };

struct NspItem {
    std::wstring file;    // name in the updates folder
    std::wstring name;    // name of the update it becomes
    ULONGLONG size = 0;
    NspState state = NspState::Pending;
    int ed = -1;          // edition it was installed in (-1 = not known yet)
};

struct Model {
    int ed = 0;
    bool built = false, running = false, busy = false, readsDump = false;
    std::wstring curFile;    // analysis\main_source.txt, lowercase ("" = none)
    std::wstring curMain;    // curFile, or the base main for a build made before updates existed
    std::wstring baseMain;   // SHA-256 of data\<data>\exefs\main
    std::wstring wantMain;   // the main the next build uses
    std::wstring wantFrom;   // the update it comes from ("" = the base one)
    std::wstring curFrom;    // the update whose main the exe was built from ("" = the base one or not known)
    bool conflict = false;
    std::vector<std::wstring> conflictNames;
    std::vector<std::wstring> unsupported;
    std::vector<Layer> layers;
    std::vector<NspItem> nsps;
    int pendingHere = 0, pendingAny = 0;   // .nsp to process by this edition / by an edition not known yet
    int badNsp = 0;
};

struct ShaderJob {
    HWND notify = nullptr;
    std::wstring key, python, script, maxwell, mainBin, spirv;
    std::vector<std::pair<std::wstring, std::wstring>> dirs;   // layer folder, its romfs\shader
    std::map<std::wstring, int> missing;                       // layer folder -> shaders without .spv (-1 = unknown)
};

struct State {
    HWND dlg = nullptr;
    HWND mods = nullptr, upd = nullptr;
    Model m;
    std::vector<std::wstring> modRows, updRows;   // what the lists show (to refill them only when it changes)
    bool filling = false;
    bool dirty = true;
    bool lastRunning = false, lastBusy = false;
    COLORREF bannerColor = 0;
    bool bannerColored = false;
    bool recompReason = false;
    // shaders of the active mods
    std::wstring shaderKey;                   // what the last finished check was about
    std::map<std::wstring, int> shaderMissing;
    bool shaderRunning = false;
    std::wstring shaderPendingKey;            // what the running check is about
    // base main hash cache
    std::wstring baseKey, baseHash;
    // things moved to their right folder (shown for a while in the banner, no message box)
    std::wstring notice;
    ULONGLONG noticeUntil = 0;
    std::vector<std::wstring> reported;   // problems already told once this session
    std::vector<std::wstring> noticeQueue;
} s;

std::wstring BaseMainHash(int ed) {
    std::wstring p = Join(DataDir(ed), L"exefs\\main");
    WIN32_FILE_ATTRIBUTE_DATA d{};
    if (!GetFileAttributesExW(p.c_str(), GetFileExInfoStandard, &d)) return L"";
    std::wstring key = p + Format(L"|%lu|%lu|%lu", d.nFileSizeLow, d.ftLastWriteTime.dwLowDateTime, d.ftLastWriteTime.dwHighDateTime);
    if (key != s.baseKey) {
        std::wstring h;
        s.baseHash = Sha256File(p, h) ? h : L"";
        s.baseKey = key;
    }
    return s.baseHash;
}

void ScanLayer(const std::wstring& dir, Layer& l) {
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(dir, L"*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        std::wstring n = fd.cFileName;
        if (n == L"." || n == L"..") continue;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) ScanLayer(Join(dir, n), l);
            continue;
        }
        l.files++;
        l.bytes += (ULONGLONG(fd.nFileSizeHigh) << 32) | fd.nFileSizeLow;
        if (n.size() > 6 && Same(n.substr(n.size() - 6), L".nxrcp")) l.recipes++;
    } while (FindNextFileW(h, &fd));
    FindClose(h);
}

int CountFiles(const std::wstring& dir) {
    int n = 0;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(dir, L"*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return 0;
    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) n++;
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return n;
}

// .nsp the recompilation of an edition skipped as "not for this edition" (seen in its output by the Install tab),
// kept in launcher.ini as nsp_not_<edition>=<file>*<size>|...: they tell which edition a pending .nsp is for.
std::wstring SkipKey(int ed) { return std::wstring(L"nsp_not_") + kEditions[ed].key; }

bool SkippedBy(int ed, const std::wstring& file, ULONGLONG size) {
    std::wstring list = L"|" + IniGet(SkipKey(ed).c_str(), L"") + L"|";
    return list.find(L"|" + file + Format(L"*%llu|", size)) != std::wstring::npos;
}

std::vector<NspItem> ScanUpdates() {
    std::vector<NspItem> out;
    std::wstring dir = UpdatesDir();
    FILETIME toolFt{};
    bool tool = GetFileTime64(Join(g.root, L"tools\\internal\\mods_nsp.py"), toolFt);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(dir, L"*.nsp").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return out;
    do {
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        NspItem it;
        it.file = fd.cFileName;
        if (it.file.size() < 5 || !Same(it.file.substr(it.file.size() - 4), L".nsp")) continue;   // 8.3 name matches
        it.size = (ULONGLONG(fd.nFileSizeHigh) << 32) | fd.nFileSizeLow;
        it.name = UpdateName(it.file);
        if (it.file[0] == L'_') { it.state = NspState::Ignored; out.push_back(it); continue; }
        // The stamp recomp writes to source.txt when it processes it (ModStamp).
        std::wstring head = Format(L"%ls|%llu|%llu|", it.file.c_str(), it.size, Ticks(fd.ftLastWriteTime));
        std::wstring stamp = head + (tool ? Format(L"%llu", Ticks(toolFt)) : L"?");
        it.state = NspState::Pending;
        for (int ed = 0; ed < 2; ed++) {
            if (IsUpdateLayer(Join(ModsDir(ed), L"_" + it.name))) { it.state = NspState::Disabled; it.ed = ed; break; }
            std::wstring d = Join(UpdData(ed), it.name);
            std::wstring src = Join(d, L"source.txt");
            if (!FileExists(src) || !FileExists(Join(d, L"mod.json"))) continue;
            it.ed = ed;
            std::wstring have = ReadSmall(src);
            if (!IsUpdateLayer(Join(ModsDir(ed), it.name))) it.state = NspState::Readd;
            else if (have == stamp) it.state = NspState::Processed;
            else if (have.compare(0, head.size(), head) == 0) it.state = NspState::Reprocess;
            else it.state = NspState::Changed;
            break;
        }
        if (it.state == NspState::Pending && !HasPfs0(Join(dir, it.file))) it.state = NspState::BadHeader;
        if (it.state == NspState::Pending) {
            bool notJ = SkippedBy(0, it.file, it.size), notG = SkippedBy(1, it.file, it.size);
            if (notJ && notG) it.state = NspState::Foreign;
            else if (notJ) it.ed = 1;
            else if (notG) it.ed = 0;
        }
        out.push_back(it);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    std::sort(out.begin(), out.end(), [](const NspItem& a, const NspItem& b) { return _wcsicmp(a.file.c_str(), b.file.c_str()) < 0; });
    return out;
}

Model BuildModel(int ed) {
    Model m;
    m.ed = ed;
    m.built = FileExists(ExePath(ed));
    m.running = GameRunning(ed);
    m.busy = RunnerBusy();
    std::wstring data = DataDir(ed);
    m.readsDump = FileExists(Join(data, L"romfs_source.bin")) && !DirExists(Join(data, L"extract\\romfs")) && !FindDumps().empty();
    m.curFile = Lower(ReadSmall(Join(data, L"analysis\\main_source.txt")));
    m.baseMain = BaseMainHash(ed);
    m.curMain = m.curFile.empty() ? m.baseMain : m.curFile;

    // layers
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(ModsDir(ed), L"*").c_str(), &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
            std::wstring n = fd.cFileName;
            if (n.empty() || n[0] == L'.') continue;   // ., .., hidden ones and a copy in progress (.adding_*)
            Layer l;
            l.folder = n;
            l.enabled = n[0] != L'_';
            l.name = l.enabled ? n : n.substr(1);
            if (l.name.empty()) continue;
            std::wstring dir = Join(ModsDir(ed), n);
            l.romfs = DirExists(Join(dir, L"romfs"));
            l.update = IsUpdateLayer(dir);
            l.reqMain = Lower(ReadSmall(Join(dir, L"requires_main.txt")));
            std::wstring ud = Join(UpdData(ed), l.name);
            l.updData = l.update && FileExists(Join(ud, L"mod.json")) && FileExists(Join(ud, L"source.txt"));
            if (l.updData) l.json = ReadModJson(Join(ud, L"mod.json"));
            l.ownMain = !l.reqMain.empty() || HasModule(l.json, L"main");
            for (const std::wstring& mod : l.json.exefsDifferent) if (!Same(mod, L"main")) l.otherModules.push_back(mod);
            ScanLayer(dir, l);
            l.shaders = l.romfs ? CountFiles(Join(dir, L"romfs\\shader")) : 0;
            l.order = OrderKey(l.name);
            m.layers.push_back(l);
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    std::sort(m.layers.begin(), m.layers.end(), [](const Layer& a, const Layer& b) {
        return a.order != b.order ? a.order < b.order : a.enabled > b.enabled;
    });

    // The main of the next build (recomp_tsukihime.ps1: the active updates, by name; more than one main = refused).
    std::vector<const Layer*> mains;
    std::vector<const Layer*> byName;
    for (const Layer& l : m.layers) if (l.enabled && l.update && l.updData) byName.push_back(&l);
    std::sort(byName.begin(), byName.end(), [](const Layer* a, const Layer* b) { return _wcsicmp(a->name.c_str(), b->name.c_str()) < 0; });
    for (const Layer* l : byName) {
        if (!l->otherModules.empty()) {
            std::wstring mods;
            for (const std::wstring& x : l->otherModules) mods += (mods.empty() ? L"" : L", ") + x;
            m.unsupported.push_back(l->name + L" (" + mods + L")");
        }
        if (HasModule(l->json, L"main")) mains.push_back(l);
    }
    std::vector<std::wstring> distinct;
    for (const Layer* l : mains) {
        std::wstring id = !l->json.mainSha.empty() ? l->json.mainSha : l->reqMain;
        if (std::find(distinct.begin(), distinct.end(), id) == distinct.end()) distinct.push_back(id);
    }
    if (distinct.size() > 1) {
        m.conflict = true;
        for (const Layer* l : mains) m.conflictNames.push_back(l->name);
    }
    if (mains.empty()) {
        m.wantMain = m.baseMain;
    } else {
        m.wantFrom = mains[0]->name;
        m.wantMain = mains[0]->reqMain;
        if (m.wantMain.empty()) {
            std::wstring hh;
            if (Sha256File(Join(Join(UpdData(ed), mains[0]->name), L"exefs\\main"), hh)) m.wantMain = hh;
        }
    }
    for (const Layer& l : m.layers)
        if (!l.reqMain.empty() && l.reqMain == m.curMain) { m.curFrom = l.name; break; }

    for (Layer& l : m.layers) {
        l.loadsNow = m.built && l.enabled && l.romfs && (l.reqMain.empty() || l.reqMain == m.curFile);
        l.loadsNext = l.enabled && l.romfs && (l.reqMain.empty() || (!m.conflict && l.reqMain == m.wantMain));
    }

    m.nsps = ScanUpdates();
    for (const NspItem& n : m.nsps) {
        bool todo = n.state == NspState::Pending || n.state == NspState::Readd || n.state == NspState::Reprocess || n.state == NspState::Changed;
        if (todo && n.ed == ed) m.pendingHere++;
        else if (todo && n.ed < 0) m.pendingAny++;
        if (n.state == NspState::BadHeader) m.badNsp++;
    }
    return m;
}

bool NeedsMain(const Model& m) {
    return m.built && !m.conflict && !m.wantMain.empty() && m.wantMain != m.curMain;
}

int ShadersMissing(const std::wstring& folder) {
    auto it = s.shaderMissing.find(folder);
    return it == s.shaderMissing.end() ? 0 : it->second;
}

// ---------------------------------------------------------------------------------------------- shaders check
// Runs a program hidden and returns its output (stdout + stderr). The child dies with the launcher (job).
bool RunCaptured(const std::wstring& cmd, const std::wstring& cwd, std::string& out, DWORD& code) {
    SECURITY_ATTRIBUTES sa{sizeof sa, nullptr, TRUE};
    HANDLE rd = nullptr, wr = nullptr;
    if (!CreatePipe(&rd, &wr, &sa, 0)) return false;
    SetHandleInformation(rd, HANDLE_FLAG_INHERIT, 0);
    STARTUPINFOW si{};
    si.cb = sizeof si;
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdOutput = wr;
    si.hStdError = wr;
    si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    PROCESS_INFORMATION pi{};
    std::wstring line = cmd;
    HANDLE job = CreateJobObjectW(nullptr, nullptr);
    if (job) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION li{};
        li.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(job, JobObjectExtendedLimitInformation, &li, sizeof li);
    }
    BOOL ok = CreateProcessW(nullptr, &line[0], nullptr, nullptr, TRUE, CREATE_NO_WINDOW | CREATE_SUSPENDED, nullptr,
                             cwd.c_str(), &si, &pi);
    CloseHandle(wr);
    if (!ok) {
        CloseHandle(rd);
        if (job) CloseHandle(job);
        return false;
    }
    if (job) AssignProcessToJobObject(job, pi.hProcess);
    ResumeThread(pi.hThread);
    char buf[4096];
    DWORD got = 0;
    while (ReadFile(rd, buf, sizeof buf, &got, nullptr) && got) {
        if (out.size() < (4u << 20)) out.append(buf, got);
    }
    WaitForSingleObject(pi.hProcess, 120000);
    code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(rd);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    if (job) CloseHandle(job);
    return true;
}

DWORD WINAPI ShaderThread(void* p) {
    ShaderJob* job = static_cast<ShaderJob*>(p);
    for (const auto& d : job->dirs) {
        std::wstring cmd = Quoted(job->python) + L" -I " + Quoted(job->script) + L" --maxwell " + Quoted(job->maxwell) +
                           L" --romfs-shader " + Quoted(d.second) + L" --main-bin " + Quoted(job->mainBin) +
                           L" --spirv " + Quoted(job->spirv);
        std::string out;
        DWORD code = 1;
        int missing = -1;
        if (RunCaptured(cmd, g.root, out, code) && code == 0) {
            // Its last line: "SUMMARY total=N romfs=N main=N with_spv=N without_spv=N" (read by recomp_tsukihime.ps1 too).
            size_t k = out.rfind("SUMMARY ");
            size_t w = k == std::string::npos ? k : out.find("without_spv=", k);
            if (w != std::string::npos) missing = atoi(out.c_str() + w + 12);
        }
        job->missing[d.first] = missing;
    }
    if (!PostMessageW(job->notify, WM_APP_SHADERS, 0, LPARAM(job))) delete job;
    return 0;
}

// Starts the check when the active mods with shaders (or the translated shaders) changed since the last one.
void MaybeCheckShaders() {
    const Model& m = s.m;
    if (g.wine || !m.built || m.conflict || NeedsMain(m)) return;   // a recompilation is needed anyway
    std::wstring python = Join(g.root, L"framework\\private\\venv\\Scripts\\python.exe");
    std::wstring script = Join(g.root, L"tools\\internal\\tsuki_shaders_offline.py");
    std::wstring maxwell = Join(g.root, L"framework\\tools\\maxwell");
    std::wstring mainBin = Join(DataDir(m.ed), L"analysis\\main.bin");
    std::wstring spirv = Join(DataDir(m.ed), L"spirv");
    if (!FileExists(python) || !FileExists(script) || !FileExists(mainBin)) return;
    auto* job = new ShaderJob;
    job->notify = s.dlg;
    job->python = python;
    job->script = script;
    job->maxwell = maxwell;
    job->mainBin = mainBin;
    job->spirv = spirv;
    std::wstring key = mainBin + L"|" + m.curFile;
    FILETIME ft{};
    if (GetFileTime64(Join(spirv, L"results.tsv"), ft)) key += Format(L"|%llu", Ticks(ft));
    for (const Layer& l : m.layers) {
        if (!(l.loadsNow && l.loadsNext && l.shaders > 0)) continue;
        std::wstring dir = Join(Join(ModsDir(m.ed), l.folder), L"romfs\\shader");
        job->dirs.push_back({l.folder, dir});
        key += L"|" + l.folder;
        WIN32_FIND_DATAW fd;
        HANDLE h = FindFirstFileW(Join(dir, L"*").c_str(), &fd);
        if (h != INVALID_HANDLE_VALUE) {
            do {
                if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
                key += Format(L"|%ls:%lu:%llu", fd.cFileName, fd.nFileSizeLow, Ticks(fd.ftLastWriteTime));
            } while (FindNextFileW(h, &fd));
            FindClose(h);
        }
    }
    if (job->dirs.empty() || key == s.shaderKey || (s.shaderRunning && key == s.shaderPendingKey)) {
        delete job;
        return;
    }
    if (s.shaderRunning) {   // another check is running: this one starts when it ends
        delete job;
        return;
    }
    job->key = key;
    s.shaderRunning = true;
    s.shaderPendingKey = key;
    HANDLE t = CreateThread(nullptr, 0, ShaderThread, job, 0, nullptr);
    if (t) CloseHandle(t);
    else { s.shaderRunning = false; delete job; }
}

// ---------------------------------------------------------------------------------------------- texts
std::wstring LayerType(const Layer& l) {
    if (!l.update) return L2(L"Mod", L"Mod");
    return l.ownMain ? L2(L"Update + main", L"Actualizaci\u00f3n + main") : L2(L"Update", L"Actualizaci\u00f3n");
}

std::wstring LayerStatus(const Model& m, const Layer& l) {
    if (!l.romfs) return L2(L"No romfs folder: ignored", L"Sin carpeta romfs: se ignora");
    if (!l.enabled) return L2(L"Off", L"Desactivado");
    if (l.updData && !l.otherModules.empty()) return L2(L"Not supported", L"No compatible");
    if (m.conflict && l.ownMain && l.updData) return L2(L"Main in conflict", L"Main en conflicto");
    if (!m.built) return L2(L"On when compiled", L"Se aplica al compilar");
    if (l.loadsNow && l.loadsNext) {
        if (l.recipes > 0 && !m.readsDump) return L2(L"On (your dump is missing)", L"Activo (falta tu dump)");
        if (l.shaders > 0 && ShadersMissing(l.folder) > 0) return L2(L"On (shaders missing)", L"Activo (faltan shaders)");
        return L2(L"On", L"Activo");
    }
    if (!l.loadsNow && l.loadsNext) return L2(L"On after recompiling", L"Se activa al recompilar");
    if (l.loadsNow) return L2(L"On until you recompile", L"Activo hasta recompilar");
    if (l.update && !l.updData) return L2(L"Not loaded (its .nsp is missing)", L"No se carga (falta su .nsp)");
    return L2(L"Not loaded (another main)", L"No se carga (otro main)");
}

std::wstring LayerDetail(const Model& m, const Layer& l) {
    std::wstring t;
    if (l.update) {
        std::wstring from = l.json.nsp.empty() ? l.name + L".nsp" : l.json.nsp;
        t = Format(L2(L"Update from %ls", L"Actualizaci\u00f3n de %ls"), from.c_str());
        if (l.json.changed >= 0)
            t += Format(L2(L": %d files changed, %d new.", L": %d archivos cambiados, %d nuevos."), l.json.changed, std::max(0, l.json.added));
        else
            t += L".";
        if (l.ownMain) t += L2(L" It brings its own main: the game is recompiled from it.", L" Trae su propio main: el juego se recompila desde \u00e9l.");
    } else {
        t = Format(L2(L"Mod added by hand: %ls (%ls).", L"Mod agregado a mano: %ls (%ls)."), FilesN(l.files).c_str(), SizeText(l.bytes).c_str());
    }
    if (!l.romfs) t += L2(L" It has no romfs folder, so the game ignores it.", L" No tiene carpeta romfs, as\u00ed que el juego lo ignora.");
    if (l.recipes > 0)
        t += m.readsDump ? Format(L2(L" %d big files are read from your dump.", L" %d archivos grandes se leen de tu dump."), l.recipes)
                         : Format(L2(L" %d big files are read from your dump, which is not available: put your .xci / .nsp back in the port folder.",
                                     L" %d archivos grandes se leen de tu dump, que no est\u00e1 disponible: vuelve a poner tu .xci / .nsp en la carpeta del port."), l.recipes);
    if (l.shaders > 0) {
        int miss = ShadersMissing(l.folder);
        t += miss > 0 ? Format(L2(L" Brings %d shaders; %d are not translated yet: recompile.", L" Trae %d shaders; %d sin traducir todav\u00eda: recompila."), l.shaders, miss)
                      : Format(L2(L" Brings %d shaders.", L" Trae %d shaders."), l.shaders);
    }
    if (l.enabled && l.romfs && !l.loadsNow && !l.loadsNext && m.built) {
        if (l.update && !l.updData)
            t += Format(L2(L" Its data (data\\%ls\\updates\\%ls) is gone: put its .nsp back in updates and recompile.",
                           L" Sus datos (data\\%ls\\updates\\%ls) ya no est\u00e1n: vuelve a poner su .nsp en updates y recompila."), kEditions[m.ed].data, l.name.c_str());
        else
            t += L2(L" It was made for another main than the one of this build.", L" Se hizo para otro main que el de esta compilaci\u00f3n.");
    }
    if (!l.enabled) t += L2(L" Tick it to turn it on.", L" M\u00e1rcalo para activarlo.");
    return t;
}

std::wstring NspStatus(const NspItem& n) {
    std::wstring ed = n.ed >= 0 ? EditionName(n.ed) : L"";
    switch (n.state) {
    case NspState::Ignored: return L2(L"Skipped (name starts with _)", L"Se salta (empieza con _)");
    case NspState::BadHeader: return L2(L"Not an NSP", L"No es un .nsp v\u00e1lido");
    case NspState::Foreign: return L2(L"Not for Tsukihime", L"No es de Tsukihime");
    case NspState::Pending:
        return n.ed >= 0 ? Format(L2(L"To install: recompile (%ls)", L"Por instalar: recompila (%ls)"), ed.c_str())
                         : std::wstring(L2(L"To install: recompile", L"Por instalar: recompila"));
    case NspState::Readd: return Format(L2(L"Installed again when recompiling (%ls)", L"Se reinstala al recompilar (%ls)"), ed.c_str());
    case NspState::Processed: return Format(L2(L"Installed (%ls)", L"Instalada (%ls)"), ed.c_str());
    case NspState::Disabled: return Format(L2(L"Installed, off (%ls)", L"Instalada, desactivada (%ls)"), ed.c_str());
    case NspState::Reprocess: return Format(L2(L"Redone when recompiling (%ls)", L"Se rehace al recompilar (%ls)"), ed.c_str());
    case NspState::Changed: return Format(L2(L"Changed: redone when recompiling (%ls)", L"Cambi\u00f3: se rehace al recompilar (%ls)"), ed.c_str());
    }
    return L"";
}

std::wstring NspDetail(const NspItem& n) {
    std::wstring ed = n.ed >= 0 ? EditionName(n.ed) : L"";
    switch (n.state) {
    case NspState::Ignored:
        return L2(L"Files whose name starts with _ are skipped. Rename it (without the _) to install it.",
                  L"Los archivos cuyo nombre empieza con _ se saltan. Ren\u00f3mbralo (sin el _) para instalarlo.");
    case NspState::BadHeader:
        return L2(L"It does not start like an NSP (PFS0): the recompilation would fail with it. Remove it.",
                  L"No empieza como un NSP (PFS0): la recompilaci\u00f3n fallar\u00eda con \u00e9l. Qu\u00edtalo.");
    case NspState::Foreign:
        return L2(L"Neither edition took it as its own (another game?). Every recompilation reads it again: remove it.",
                  L"Ninguna de las dos ediciones lo reconoci\u00f3 como suyo (\u00bfotro juego?). Cada recompilaci\u00f3n lo vuelve a leer: qu\u00edtalo.");
    case NspState::Pending:
        if (n.ed >= 0)
            return Format(L2(L"Not installed yet: it is for the %ls edition and is installed when you recompile it (Install and recompile).",
                             L"Todav\u00eda no est\u00e1 instalada: es de la edici\u00f3n %ls y se instala al recompilarla (Instalar y recompilar)."), ed.c_str());
        return L2(L"Not installed yet: it is installed when you recompile the edition it is for (Install and recompile). It needs your dump.",
                  L"Todav\u00eda no est\u00e1 instalada: se instala al recompilar la edici\u00f3n a la que pertenece (Instalar y recompilar). Necesita tu dump.");
    case NspState::Readd:
        return L2(L"Its mod was deleted, but the .nsp is still here: the next recompilation installs it again. Remove the .nsp if you do not want it.",
                  L"Su mod se borr\u00f3, pero el .nsp sigue aqu\u00ed: la pr\u00f3xima recompilaci\u00f3n la vuelve a instalar. Quita el .nsp si no la quieres.");
    case NspState::Processed:
        return Format(L2(L"Installed in the %ls edition (mods\\%ls\\%ls). The .nsp is no longer needed: you can remove it to free %ls.",
                         L"Instalada en la edici\u00f3n %ls (mods\\%ls\\%ls). El .nsp ya no hace falta: puedes quitarlo para liberar %ls."),
                      ed.c_str(), kEditions[n.ed].key, n.name.c_str(), SizeText(n.size).c_str());
    case NspState::Disabled:
        return Format(L2(L"Installed in the %ls edition, but turned off (its mod is unticked). The .nsp is no longer needed.",
                         L"Instalada en la edici\u00f3n %ls, pero desactivada (su mod est\u00e1 desmarcado). El .nsp ya no hace falta."), ed.c_str());
    case NspState::Reprocess:
        return L2(L"The port's patch tool changed since it was installed: the next recompilation redoes it (keep the .nsp until then).",
                  L"La herramienta de parches del port cambi\u00f3 desde que se instal\u00f3: la pr\u00f3xima recompilaci\u00f3n la rehace (conserva el .nsp hasta entonces).");
    case NspState::Changed:
        return L2(L"This .nsp is not the one installed (another size or date): the next recompilation installs it again.",
                  L"Este .nsp no es el que est\u00e1 instalado (otro tama\u00f1o o fecha): la pr\u00f3xima recompilaci\u00f3n lo vuelve a instalar.");
    }
    return L"";
}

// ---------------------------------------------------------------------------------------------- rendering
int ShadersMissingIn(const Model& m) {
    if (m.ed != s.m.ed || s.shaderKey.empty()) return 0;   // only known for the edition shown in the tab
    int n = 0;
    for (const Layer& l : m.layers)
        if (l.loadsNow && l.loadsNext && l.shaders > 0) n += std::max(0, ShadersMissing(l.folder));
    return n;
}

// What stops the next recompilation (red), or "" when nothing does.
std::wstring BlockingProblem(const Model& m) {
    if (m.conflict) {
        std::wstring names;
        for (const std::wstring& n : m.conflictNames) names += (names.empty() ? L"" : L", ") + n;
        return Format(L2(L"These active updates bring different mains and cannot be combined: %ls. Untick all but one.",
                         L"Estas actualizaciones activas traen mains distintos y no se pueden combinar: %ls. Desmarca todas menos una."), names.c_str());
    }
    if (!m.unsupported.empty())
        return Format(L2(L"%ls changes more than main: the port cannot use it and the recompilation would stop. Untick or remove it.",
                         L"%ls cambia m\u00e1s que el main: el port no puede usarla y la recompilaci\u00f3n se detendr\u00eda. Desm\u00e1rcala o qu\u00edtala."),
                      m.unsupported[0].c_str());
    if (m.badNsp > 0)
        return L2(L"There is a file in updates that is not an NSP: the recompilation would fail with it. Remove it.",
                  L"Hay un archivo en updates que no es un NSP: la recompilaci\u00f3n fallar\u00eda con \u00e9l. Qu\u00edtalo.");
    return L"";
}

// Why this edition has to be recompiled for the mods and updates to apply (empty = it does not).
std::vector<std::wstring> RecompileReasons(const Model& m) {
    std::vector<std::wstring> why;
    if (NeedsMain(m)) {
        why.push_back(m.wantFrom.empty() ? std::wstring(L2(L"the game goes back to its own main", L"el juego vuelve a su main original"))
                                         : Format(L2(L"the main of %ls", L"el main de %ls"), m.wantFrom.c_str()));
    }
    if (m.pendingHere + m.pendingAny > 0) {
        int n = m.pendingHere + m.pendingAny;
        why.push_back(n == 1 ? std::wstring(L2(L"1 .nsp to install", L"1 .nsp por instalar")) : Format(L2(L"%d .nsp to install", L"%d .nsp por instalar"), n));
    }
    int shaders = ShadersMissingIn(m);
    if (shaders > 0) why.push_back(Format(L2(L"%d shaders of mods to translate", L"%d shaders de mods por traducir"), shaders));
    return why;
}

std::wstring JoinReasons(const std::vector<std::wstring>& why) {
    std::wstring list;
    for (size_t i = 0; i < why.size(); i++) list += (i ? L"; " : L"") + why[i];
    return list;
}

void SetBanner(const std::wstring& text, COLORREF color, bool colored) {
    s.bannerColor = color;
    s.bannerColored = colored;
    SetDlgItemTextW(s.dlg, IDC_MODS_BANNER, text.c_str());
    InvalidateRect(GetDlgItem(s.dlg, IDC_MODS_BANNER), nullptr, TRUE);
}

void RenderBanner() {
    const Model& m = s.m;
    const COLORREF red = RGB(190, 0, 0), orange = RGB(170, 85, 0), green = RGB(0, 110, 0), blue = RGB(0, 70, 160);
    s.recompReason = false;
    if (!s.notice.empty() && GetTickCount64() < s.noticeUntil) {
        SetBanner(s.notice, blue, true);
        s.recompReason = !RecompileReasons(m).empty() || !m.built;   // the button still works meanwhile
        return;
    }
    s.notice.clear();
    std::wstring problem = BlockingProblem(m);
    if (!problem.empty()) {
        SetBanner(problem, red, true);
        return;
    }
    std::vector<std::wstring> why = RecompileReasons(m);
    if (!m.built) {
        SetBanner(L2(L"This edition is not compiled yet: the mods and updates are applied when you compile it.",
                     L"Esta edici\u00f3n todav\u00eda no est\u00e1 compilada: los mods y actualizaciones se aplican al compilarla."), orange, true);
        s.recompReason = true;
        return;
    }
    if (!why.empty()) {
        std::wstring t = Format(L2(L"Recompile this edition to apply the changes (%ls).", L"Recompila esta edici\u00f3n para aplicar los cambios (%ls)."), JoinReasons(why).c_str());
        if (m.pendingAny > 0 && m.pendingHere == 0 && why.size() == 1)
            t = L2(L"There are .nsp to install in updates: recompile the edition they are for.",
                   L"Hay .nsp por instalar en updates: recompila la edici\u00f3n a la que pertenecen.");
        SetBanner(t, orange, true);
        s.recompReason = true;
        return;
    }
    for (const Layer& l : m.layers) {
        if (l.loadsNow && l.recipes > 0 && !m.readsDump) {
            SetBanner(Format(L2(L"%ls reads big files from your dump: put your .xci / .nsp back in the port folder.",
                                L"%ls lee archivos grandes de tu dump: vuelve a poner tu .xci / .nsp en la carpeta del port."), l.name.c_str()), orange, true);
            return;
        }
    }
    int on = 0;
    for (const Layer& l : m.layers) if (l.loadsNow) on++;
    if (m.layers.empty() && m.nsps.empty()) {
        SetBanner(L2(L"No mods yet. Add a mod (a folder with romfs) or an update / translation patch (.nsp).",
                     L"Todav\u00eda no hay mods. Agrega un mod (una carpeta con romfs) o una actualizaci\u00f3n / parche de traducci\u00f3n (.nsp)."), 0, false);
        return;
    }
    if (s.shaderRunning) {
        SetBanner(L2(L"Checking the shaders of the mods...", L"Comprobando los shaders de los mods..."), 0, false);
        return;
    }
    SetBanner(on == 1 ? std::wstring(L2(L"Everything is up to date: 1 mod on.", L"Todo al d\u00eda: 1 mod activo."))
                      : Format(L2(L"Everything is up to date: %d mods on.", L"Todo al d\u00eda: %d mods activos."), on), green, true);
}

int SelectedRow(HWND list) { return int(SendMessageW(list, LVM_GETNEXTITEM, WPARAM(-1), LVNI_SELECTED)); }

void SetCell(HWND list, int row, int col, const std::wstring& text) {
    LVITEMW it{};
    it.iSubItem = col;
    it.pszText = const_cast<wchar_t*>(text.c_str());
    SendMessageW(list, LVM_SETITEMTEXTW, row, LPARAM(&it));
}

void RenderDetails() {
    int r = SelectedRow(s.mods);
    std::wstring t;
    if (r >= 0 && r < int(s.m.layers.size())) t = LayerDetail(s.m, s.m.layers[size_t(r)]);
    else t = L2(L"Tick or untick a mod to turn it on or off. When two mods have the same file, the one lower in the list wins.",
                L"Marca o desmarca un mod para activarlo o desactivarlo. Si dos mods tienen el mismo archivo, gana el que est\u00e1 m\u00e1s abajo.");
    SetDlgItemTextW(s.dlg, IDC_MODS_DETAIL, t.c_str());
    r = SelectedRow(s.upd);
    if (r >= 0 && r < int(s.m.nsps.size())) t = NspDetail(s.m.nsps[size_t(r)]);
    else t = L2(L"Put updates and translation patches (.nsp) here; they are installed when you recompile. Afterwards the .nsp can be removed.",
                L"Pon aqu\u00ed las actualizaciones y parches de traducci\u00f3n (.nsp); se instalan al recompilar. Despu\u00e9s el .nsp se puede quitar.");
    SetDlgItemTextW(s.dlg, IDC_UPD_DETAIL, t.c_str());
}

void RenderButtons() {
    const Model& m = s.m;
    bool running = GameRunning(m.ed), busy = RunnerBusy();
    EnableWindow(GetDlgItem(s.dlg, IDC_MODS_ADD), !running && !busy);
    EnableWindow(GetDlgItem(s.dlg, IDC_MODS_REMOVE), !running && !busy && SelectedRow(s.mods) >= 0);
    EnableWindow(GetDlgItem(s.dlg, IDC_UPD_ADD), !busy);
    EnableWindow(GetDlgItem(s.dlg, IDC_UPD_REMOVE), !busy && SelectedRow(s.upd) >= 0);
    EnableWindow(GetDlgItem(s.dlg, IDC_MODS_RECOMP), s.recompReason && !busy);
    EnableWindow(GetDlgItem(s.dlg, IDC_MODS_EDITION), !busy);
    s.lastRunning = running;
    s.lastBusy = busy;
}

// Refills a list only when its rows changed (keeps the selection and the scroll otherwise).
void FillMods() {
    std::vector<std::wstring> rows;
    for (const Layer& l : s.m.layers)
        rows.push_back(l.folder + L"\t" + LayerType(l) + L"\t" + LayerStatus(s.m, l) + L"\t" + SizeText(l.bytes) + (l.enabled ? L"\t1" : L"\t0"));
    if (rows == s.modRows) return;
    std::wstring keep;
    int sel = SelectedRow(s.mods);
    if (sel >= 0 && sel < int(s.modRows.size())) keep = s.modRows[size_t(sel)].substr(0, s.modRows[size_t(sel)].find(L'\t'));
    int top = int(SendMessageW(s.mods, LVM_GETTOPINDEX, 0, 0));
    s.filling = true;
    SendMessageW(s.mods, WM_SETREDRAW, FALSE, 0);
    SendMessageW(s.mods, LVM_DELETEALLITEMS, 0, 0);
    for (size_t i = 0; i < s.m.layers.size(); i++) {
        const Layer& l = s.m.layers[i];
        LVITEMW it{};
        it.mask = LVIF_TEXT;
        it.iItem = int(i);
        it.pszText = const_cast<wchar_t*>(l.name.c_str());
        int row = int(SendMessageW(s.mods, LVM_INSERTITEMW, 0, LPARAM(&it)));
        SetCell(s.mods, row, 1, LayerType(l));
        SetCell(s.mods, row, 2, LayerStatus(s.m, l));
        SetCell(s.mods, row, 3, SizeText(l.bytes));
        ListView_SetCheckState(s.mods, row, l.enabled);
        if (!keep.empty() && (Same(keep, l.folder) || Same(keep.substr(keep[0] == L'_' ? 1 : 0), l.name)))
            ListView_SetItemState(s.mods, row, LVIS_SELECTED | LVIS_FOCUSED, LVIS_SELECTED | LVIS_FOCUSED);
    }
    if (top > 0) ListView_EnsureVisible(s.mods, std::min(top + ListView_GetCountPerPage(s.mods) - 1, int(s.m.layers.size()) - 1), FALSE);
    SendMessageW(s.mods, WM_SETREDRAW, TRUE, 0);
    InvalidateRect(s.mods, nullptr, TRUE);
    s.filling = false;
    s.modRows = rows;
}

void FillUpdates() {
    std::vector<std::wstring> rows;
    for (const NspItem& n : s.m.nsps) rows.push_back(n.file + L"\t" + NspStatus(n) + L"\t" + SizeText(n.size));
    if (rows == s.updRows) return;
    std::wstring keep;
    int sel = SelectedRow(s.upd);
    if (sel >= 0 && sel < int(s.updRows.size())) keep = s.updRows[size_t(sel)].substr(0, s.updRows[size_t(sel)].find(L'\t'));
    s.filling = true;
    SendMessageW(s.upd, WM_SETREDRAW, FALSE, 0);
    SendMessageW(s.upd, LVM_DELETEALLITEMS, 0, 0);
    for (size_t i = 0; i < s.m.nsps.size(); i++) {
        const NspItem& n = s.m.nsps[i];
        LVITEMW it{};
        it.mask = LVIF_TEXT;
        it.iItem = int(i);
        it.pszText = const_cast<wchar_t*>(n.file.c_str());
        int row = int(SendMessageW(s.upd, LVM_INSERTITEMW, 0, LPARAM(&it)));
        SetCell(s.upd, row, 1, NspStatus(n));
        SetCell(s.upd, row, 2, SizeText(n.size));
        if (!keep.empty() && Same(keep, n.file)) ListView_SetItemState(s.upd, row, LVIS_SELECTED | LVIS_FOCUSED, LVIS_SELECTED | LVIS_FOCUSED);
    }
    SendMessageW(s.upd, WM_SETREDRAW, TRUE, 0);
    InvalidateRect(s.upd, nullptr, TRUE);
    s.filling = false;
    s.updRows = rows;
}

void Render() {
    SendDlgItemMessageW(s.dlg, IDC_MODS_EDITION, CB_SETCURSEL, s.m.ed, 0);
    SetDlgItemTextW(s.dlg, IDC_MODS_GROUP, Format(L2(L"Mods, custom RomFS", L"Mods, RomFS personalizado"), kEditions[s.m.ed].key).c_str());
    RenderBanner();
    FillMods();
    FillUpdates();
    RenderDetails();
    RenderButtons();
}

constexpr UINT_PTR kTimerNotice = 2, kTimerRetry = 3;

struct Moved {
    std::vector<std::wstring> nsps;                   // .nsp moved from mods to updates
    std::vector<std::pair<std::wstring, int>> mods;   // mods moved from updates to mods\<edition> (name, edition)
    std::vector<std::wstring> problems;               // told once per session
    bool empty() const { return nsps.empty() && mods.empty() && problems.empty(); }
};
bool FixMisplaced(int ed, Moved& moved);

// One short text for the banner (two lines at most).
std::wstring MovedText(const Moved& mv) {
    std::wstring t;
    if (mv.nsps.size() == 1)
        t = Format(L2(L"%ls was in mods: it was moved to updates.", L"%ls estaba en mods: se movi\u00f3 a updates."), mv.nsps[0].c_str());
    else if (mv.nsps.size() > 1)
        t = Format(L2(L"%d .nsp were in mods: they were moved to updates.", L"%d .nsp estaban en mods: se movieron a updates."), int(mv.nsps.size()));
    if (!t.empty() && !mv.mods.empty()) t += L" ";
    if (mv.mods.size() == 1)
        t += Format(L2(L"%ls was in updates: it was moved to mods\\%ls.", L"%ls estaba en updates: se movi\u00f3 a mods\\%ls."),
                    mv.mods[0].first.c_str(), kEditions[mv.mods[0].second].key);
    else if (mv.mods.size() > 1)
        t += Format(L2(L"%d mods were in updates: they were moved to mods.", L"%d mods estaban en updates: se movieron a mods."), int(mv.mods.size()));
    return t;
}

// The moves first; the problems (other game, same name) after them, one notice each (the banner has two lines).
void QueueNotices(const Moved& mv) {
    std::wstring t = MovedText(mv);
    if (!t.empty()) s.noticeQueue.push_back(t);
    for (const std::wstring& p : mv.problems) s.noticeQueue.push_back(p);
}

bool NextNotice() {   // true when a new notice is shown
    if (s.noticeQueue.empty()) return false;
    s.notice = s.noticeQueue.front();
    s.noticeQueue.erase(s.noticeQueue.begin());
    s.noticeUntil = GetTickCount64() + 12000;
    if (s.dlg) SetTimer(s.dlg, kTimerNotice, 12000, nullptr);
    return true;
}
void FullRefresh() {
    if (!s.dlg) return;
    if (s.m.ed != g.edition) {   // another edition: forget what the lists show and the shaders check
        s.modRows.assign(1, L"\x01");   // never a real row: the lists are refilled
        s.updRows.assign(1, L"\x01");
        s.shaderKey.clear();
        s.shaderMissing.clear();
    }
    Moved moved;
    if (FixMisplaced(g.edition, moved)) SetTimer(s.dlg, kTimerRetry, 5000, nullptr);   // something still being copied
    if (!moved.empty()) {
        QueueNotices(moved);
        if (s.notice.empty() || GetTickCount64() >= s.noticeUntil) NextNotice();
    }
    s.m = BuildModel(g.edition);
    s.dirty = false;
    MaybeCheckShaders();
    Render();
}

// ---------------------------------------------------------------------------------------------- copy with progress
struct CopyTask {
    std::wstring text;                                          // shown in the window
    std::vector<std::wstring> dirs;                             // created first, in order
    std::vector<std::pair<std::wstring, std::wstring>> files;   // source -> destination
    ULONGLONG total = 0;
    volatile ULONGLONG done = 0;      // bytes of the files already copied
    volatile ULONGLONG current = 0;   // bytes of the file being copied
    BOOL cancel = FALSE;
    bool move = false;   // move the files instead of copying them (a rename on the same drive)
    size_t completed = 0;   // files already copied / moved
    DWORD error = 0;
    bool ok = false;
    HWND dlg = nullptr;
    ULONGLONG lastPost = 0;
};

bool PlanTree(const std::wstring& from, const std::wstring& to, CopyTask& t) {
    t.dirs.push_back(to);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(from, L"*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return GetLastError() == ERROR_FILE_NOT_FOUND;
    do {
        std::wstring n = fd.cFileName;
        if (n == L"." || n == L"..") continue;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) PlanTree(Join(from, n), Join(to, n), t);
            continue;
        }
        t.files.push_back({Join(from, n), Join(to, n)});
        t.total += (ULONGLONG(fd.nFileSizeHigh) << 32) | fd.nFileSizeLow;
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return true;
}

DWORD CALLBACK CopyProgress(LARGE_INTEGER, LARGE_INTEGER transferred, LARGE_INTEGER, LARGE_INTEGER, DWORD, DWORD, HANDLE, HANDLE, LPVOID data) {
    auto* t = static_cast<CopyTask*>(data);
    t->current = ULONGLONG(transferred.QuadPart);
    ULONGLONG now = GetTickCount64();
    if (now - t->lastPost >= 100) {
        t->lastPost = now;
        PostMessageW(t->dlg, WM_APP_COPY_PROGRESS, 0, 0);
    }
    return t->cancel ? PROGRESS_CANCEL : PROGRESS_CONTINUE;
}

DWORD WINAPI CopyThread(void* p) {
    auto* t = static_cast<CopyTask*>(p);
    t->ok = true;
    for (const std::wstring& d : t->dirs) {
        if (!CreateDirectoryW(d.c_str(), nullptr) && GetLastError() != ERROR_ALREADY_EXISTS) { t->error = GetLastError(); t->ok = false; break; }
    }
    for (size_t i = 0; t->ok && i < t->files.size(); i++) {
        if (t->cancel) { t->ok = false; t->error = ERROR_CANCELLED; break; }
        t->current = 0;
        BOOL done = t->move ? MoveFileWithProgressW(t->files[i].first.c_str(), t->files[i].second.c_str(), CopyProgress, t, MOVEFILE_COPY_ALLOWED)
                            : CopyFileExW(t->files[i].first.c_str(), t->files[i].second.c_str(), CopyProgress, t, &t->cancel, COPY_FILE_FAIL_IF_EXISTS);
        if (!done) {
            t->error = GetLastError();
            t->ok = false;
            break;
        }
        WIN32_FILE_ATTRIBUTE_DATA a{};
        if (GetFileAttributesExW((t->move ? t->files[i].second : t->files[i].first).c_str(), GetFileExInfoStandard, &a))
            t->done = t->done + ((ULONGLONG(a.nFileSizeHigh) << 32) | a.nFileSizeLow);
        t->completed = i + 1;
        t->current = 0;
    }
    PostMessageW(t->dlg, WM_APP_COPY_DONE, 0, 0);
    return 0;
}

INT_PTR CALLBACK CopyProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp) {
    auto* t = reinterpret_cast<CopyTask*>(GetWindowLongPtrW(dlg, DWLP_USER));
    switch (msg) {
    case WM_INITDIALOG: {
        t = reinterpret_cast<CopyTask*>(lp);
        SetWindowLongPtrW(dlg, DWLP_USER, LONG_PTR(t));
        t->dlg = dlg;
        ApplyDialogText(dlg, IDD_COPY);
        SetDlgItemTextW(dlg, IDC_COPY_TEXT, t->text.c_str());
        SendDlgItemMessageW(dlg, IDC_COPY_PROGRESS, PBM_SETRANGE32, 0, 1000);
        SetDlgItemTextW(dlg, IDC_COPY_SIZE, Format(L2(L"0 of %ls", L"0 de %ls"), SizeText(t->total).c_str()).c_str());
        HANDLE th = CreateThread(nullptr, 0, CopyThread, t, 0, nullptr);
        if (!th) { t->ok = false; t->error = GetLastError(); EndDialog(dlg, IDCANCEL); return TRUE; }
        CloseHandle(th);
        return TRUE;
    }
    case WM_APP_COPY_PROGRESS: {
        ULONGLONG now = t->done + t->current;
        int pos = t->total ? int(now * 1000 / t->total) : 0;
        SendDlgItemMessageW(dlg, IDC_COPY_PROGRESS, PBM_SETPOS, pos, 0);
        SetDlgItemTextW(dlg, IDC_COPY_SIZE, Format(L2(L"%ls of %ls", L"%ls de %ls"), SizeText(now).c_str(), SizeText(t->total).c_str()).c_str());
        return TRUE;
    }
    case WM_APP_COPY_DONE:
        EndDialog(dlg, t->ok ? IDOK : IDCANCEL);
        return TRUE;
    case WM_COMMAND:
        if (LOWORD(wp) == IDCANCEL && t && !t->cancel) {
            t->cancel = TRUE;
            EnableWindow(GetDlgItem(dlg, IDCANCEL), FALSE);
            SetDlgItemTextW(dlg, IDC_COPY_SIZE, L2(L"Cancelling...", L"Cancelando..."));
        }
        return TRUE;
    case WM_CLOSE:
        return TRUE;   // only Cancel stops it
    }
    return FALSE;
}

bool RunCopy(HWND owner, CopyTask& t) {
    DialogBoxParamW(g.inst, MAKEINTRESOURCEW(IDD_COPY), owner, CopyProc, LPARAM(&t));
    return t.ok;
}

std::wstring ErrorText(DWORD e) {
    if (e == ERROR_CANCELLED || e == ERROR_REQUEST_ABORTED) return L2(L"cancelled", L"cancelado");
    if (e == ERROR_DISK_FULL || e == ERROR_HANDLE_DISK_FULL) return L2(L"the disk is full", L"el disco est\u00e1 lleno");
    if (e == ERROR_SHARING_VIOLATION || e == ERROR_LOCK_VIOLATION) return L2(L"a file is in use", L"un archivo est\u00e1 en uso");
    if (e == ERROR_ACCESS_DENIED) return L2(L"access denied", L"acceso denegado");
    return Format(L2(L"error %lu", L"error %lu"), e);
}

// ---------------------------------------------------------------------------------------------- name of a new mod
struct NameAsk {
    int ed = 0;
    std::wstring text, name, error;
};

// Names the game would skip or that would clash with the recompilation's own folders are refused.
std::wstring NameProblem(int ed, const std::wstring& name) {
    if (name.empty()) return L2(L"Write a name.", L"Escribe un nombre.");
    if (name[0] == L'_' || name[0] == L'.') return L2(L"It cannot start with _ or . (the game would skip it).", L"No puede empezar con _ ni . (el juego lo saltar\u00eda).");
    if (name.find_first_of(L"\\/:*?\"<>|") != std::wstring::npos) return L2(L"It cannot have \\ / : * ? \" < > |", L"No puede tener \\ / : * ? \" < > |");
    for (wchar_t c : name) if (c < 32) return L2(L"It has an invalid character.", L"Tiene un car\u00e1cter no v\u00e1lido.");
    if (name.back() == L' ' || name.back() == L'.') return L2(L"It cannot end with a space or a dot.", L"No puede terminar en espacio ni en punto.");
    if (name.size() > 80) return L2(L"Too long (80 characters at most).", L"Demasiado largo (m\u00e1ximo 80 caracteres).");
    std::wstring dir = ModsDir(ed);
    if (GetFileAttributesW(Join(dir, name).c_str()) != INVALID_FILE_ATTRIBUTES || GetFileAttributesW(Join(dir, L"_" + name).c_str()) != INVALID_FILE_ATTRIBUTES)
        return L2(L"There is already a mod with that name.", L"Ya hay un mod con ese nombre.");
    for (const NspItem& n : s.m.nsps)
        if (Same(n.name, name)) return L2(L"That name belongs to an update in updates: pick another one.", L"Ese nombre es de una actualizaci\u00f3n de updates: elige otro.");
    if (DirExists(Join(UpdData(ed), name))) return L2(L"That name belongs to an installed update: pick another one.", L"Ese nombre es de una actualizaci\u00f3n instalada: elige otro.");
    return L"";
}

INT_PTR CALLBACK NameProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp) {
    auto* a = reinterpret_cast<NameAsk*>(GetWindowLongPtrW(dlg, DWLP_USER));
    switch (msg) {
    case WM_INITDIALOG:
        a = reinterpret_cast<NameAsk*>(lp);
        SetWindowLongPtrW(dlg, DWLP_USER, LONG_PTR(a));
        ApplyDialogText(dlg, IDD_MODNAME);
        SetDlgItemTextW(dlg, IDC_MODNAME_TEXT, a->text.c_str());
        SetDlgItemTextW(dlg, IDC_MODNAME_EDIT, a->name.c_str());
        SendDlgItemMessageW(dlg, IDC_MODNAME_EDIT, EM_LIMITTEXT, 80, 0);
        SetFocus(GetDlgItem(dlg, IDC_MODNAME_EDIT));
        SendDlgItemMessageW(dlg, IDC_MODNAME_EDIT, EM_SETSEL, 0, -1);
        return FALSE;   // focus set here
    case WM_CTLCOLORSTATIC:
        if (GetDlgCtrlID(HWND(lp)) == IDC_MODNAME_ERR) {
            SetTextColor(HDC(wp), RGB(190, 0, 0));
            SetBkMode(HDC(wp), TRANSPARENT);
            return INT_PTR(GetSysColorBrush(COLOR_BTNFACE));
        }
        break;
    case WM_COMMAND:
        if (LOWORD(wp) == IDOK) {
            std::wstring n = Trim(GetDlgText(dlg, IDC_MODNAME_EDIT));
            std::wstring err = NameProblem(a->ed, n);
            SetDlgItemTextW(dlg, IDC_MODNAME_ERR, err.c_str());
            if (err.empty()) {
                a->name = n;
                EndDialog(dlg, IDOK);
            }
            return TRUE;
        }
        if (LOWORD(wp) == IDCANCEL) EndDialog(dlg, IDCANCEL);
        return TRUE;
    }
    return FALSE;
}

// ---------------------------------------------------------------------------------------------- actions
bool Blocked(HWND owner, bool gameMatters) {
    if (RunnerBusy()) {
        Message(owner, MB_ICONINFORMATION, L2(L"A compilation is running: wait until it finishes.", L"Hay una compilaci\u00f3n en curso: espera a que termine."));
        return true;
    }
    if (gameMatters && GameRunning(g.edition)) {
        Message(owner, MB_ICONINFORMATION, L2(L"Close the game first: it reads the mods when it starts.", L"Cierra el juego primero: lee los mods al iniciar."));
        return true;
    }
    return false;
}

void Toggle(int row) {
    if (row < 0 || row >= int(s.m.layers.size())) return;
    const Layer l = s.m.layers[size_t(row)];
    bool want = ListView_GetCheckState(s.mods, row) != 0;
    if (want == l.enabled) return;
    s.modRows.assign(1, L"\x01");   // the list is redrawn from the folders whatever happens
    if (Blocked(s.dlg, true)) { FullRefresh(); return; }
    std::wstring dir = ModsDir(s.m.ed);
    std::wstring from = Join(dir, l.folder), to = Join(dir, want ? l.name : L"_" + l.name);
    if (GetFileAttributesW(to.c_str()) != INVALID_FILE_ATTRIBUTES) {
        Message(s.dlg, MB_ICONWARNING, Format(L2(L"There is already a folder called %ls in mods\\%ls: rename or remove one of the two.",
                                                 L"Ya hay una carpeta llamada %ls en mods\\%ls: renombra o quita una de las dos."),
                                              Leaf(to).c_str(), kEditions[s.m.ed].key));
    } else if (!MoveFileW(from.c_str(), to.c_str())) {
        DWORD e = GetLastError();
        Message(s.dlg, MB_ICONWARNING, Format(L2(L"The folder could not be renamed (%ls). Is a file of it open in another program?",
                                                 L"No se pudo renombrar la carpeta (%ls). \u00bfHay un archivo suyo abierto en otro programa?"), ErrorText(e).c_str()));
    }
    FullRefresh();
}

// Where the romfs of a mod is inside the folder the user picked.
struct ModSource {
    std::wstring romfs;      // the folder copied as <layer>\romfs
    std::wstring name;       // suggested name
    std::wstring titleId;    // when the romfs is inside a <title ID> folder (Atmosphere / emulator layout)
    int ed = -1;             // edition of that title ID (-1 = none or another game)
    bool code = false;       // the mod also patches code (exefs, .ips): the port cannot use that part
    std::wstring error;
};

bool IsTitleId(const std::wstring& n) {
    if (n.size() != 16) return false;
    for (wchar_t c : n) if (!iswxdigit(c)) return false;
    return true;
}

void FindRomfs(const std::wstring& dir, int depth, std::vector<std::wstring>& out) {
    if (depth > 4 || out.size() > 8) return;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(dir, L"*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        std::wstring n = fd.cFileName;
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || n == L"." || n == L".." || (fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) continue;
        if (Same(n, L"romfs")) out.push_back(Join(dir, n));
        else FindRomfs(Join(dir, n), depth + 1, out);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
}

bool HasCode(const std::wstring& modRoot) {
    if (DirExists(Join(modRoot, L"exefs")) || DirExists(Join(modRoot, L"exefs_patches")) || DirExists(Join(modRoot, L"cheats"))) return true;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(modRoot, L"*.ips").c_str(), &fd);
    if (h != INVALID_HANDLE_VALUE) { FindClose(h); return true; }
    return false;
}

std::wstring NiceName(const std::wstring& picked) {
    std::wstring n = Leaf(picked), p = picked;
    // Folders of the layouts (romfs, <title ID>, contents, atmosphere) say nothing of the mod: go up.
    for (int i = 0; i < 4 && (Same(n, L"romfs") || IsTitleId(n) || Same(n, L"contents") || Same(n, L"atmosphere")); i++) {
        p = Parent(p);
        n = Leaf(p);
    }
    while (!n.empty() && (n[0] == L'_' || n[0] == L'.')) n.erase(0, 1);
    for (wchar_t& c : n) if (wcschr(L"\\/:*?\"<>|", c) || c < 32) c = L'_';
    while (!n.empty() && (n.back() == L' ' || n.back() == L'.')) n.pop_back();
    if (n.size() > 80) n.resize(80);
    return n.empty() ? std::wstring(L"mod") : n;
}

ModSource ResolveMod(const std::wstring& picked) {
    ModSource m;
    std::vector<std::wstring> found;
    if (Same(Leaf(picked), L"romfs")) found.push_back(picked);
    else if (DirExists(Join(picked, L"romfs"))) found.push_back(Join(picked, L"romfs"));
    else FindRomfs(picked, 0, found);
    if (found.size() > 1) {   // several games or editions in one pack: keep the ones of a Tsukihime edition
        std::vector<std::wstring> ours;
        for (const std::wstring& f : found) {
            std::wstring id = Lower(Leaf(Parent(f)));
            if (id == kProgramIds[0] || id == kProgramIds[1]) ours.push_back(f);
        }
        if (ours.size() == 1) found = ours;
    }
    if (found.empty()) {
        m.error = L2(L"There is no romfs folder in it. A mod for this port is a folder with a romfs folder inside (the files of the game it replaces, with the same paths). Mods that only patch code (exefs, .ips) cannot be used.",
                     L"No tiene una carpeta romfs. Un mod para este port es una carpeta con una carpeta romfs dentro (los archivos del juego que reemplaza, con las mismas rutas). Los mods que solo parchean c\u00f3digo (exefs, .ips) no se pueden usar.");
        return m;
    }
    if (found.size() > 1) {
        m.error = L2(L"There are several romfs folders in it: pick the folder of just one mod.", L"Tiene varias carpetas romfs: elige la carpeta de un solo mod.");
        return m;
    }
    m.romfs = found[0];
    std::wstring root = Parent(m.romfs);
    std::wstring id = Leaf(root);
    if (IsTitleId(id)) {
        m.titleId = id;
        for (int ed = 0; ed < 2; ed++) if (Lower(id) == kProgramIds[ed]) m.ed = ed;
    }
    m.code = HasCode(root);
    m.name = NiceName(Same(Leaf(picked), L"romfs") ? Parent(picked) : picked);
    return m;
}

// ---------------------------------------------------------------------------------------------- misplaced files
// An .nsp put in mods\ goes to updates\, and a mod (a folder with romfs) put in updates\ goes to mods\<edition>\. Only
// what has been still for a few seconds is moved (not a copy in progress); a rename on the same drive, nothing is
// copied. Returns true when something was left for later because it is still changing.
ULONGLONG NewestTouch(const std::wstring& path) {   // newest creation / write time of a file or of a whole folder
    WIN32_FILE_ATTRIBUTE_DATA a{};
    if (!GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &a)) return 0;
    ULONGLONG t = std::max(Ticks(a.ftCreationTime), Ticks(a.ftLastWriteTime));
    if (!(a.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) return t;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(path, L"*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return t;
    do {
        std::wstring n = fd.cFileName;
        if (n == L"." || n == L"..") continue;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) t = std::max(t, NewestTouch(Join(path, n)));
        else t = std::max(t, std::max(Ticks(fd.ftCreationTime), Ticks(fd.ftLastWriteTime)));
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return t;
}

bool StillChanging(const std::wstring& path) {
    FILETIME now;
    GetSystemTimeAsFileTime(&now);
    return Ticks(now) < NewestTouch(path) + 10ull * 10000000ull;   // touched in the last 10 seconds
}

bool HasAnyFile(const std::wstring& dir) {
    int files = 0;
    ULONGLONG bytes = 0;
    FILETIME newest{};
    CountTree(dir, files, bytes, newest);
    return files > 0;
}

void ReportOnce(Moved& mv, const std::wstring& text) {
    if (std::find(s.reported.begin(), s.reported.end(), text) != s.reported.end()) return;
    s.reported.push_back(text);
    mv.problems.push_back(text);
}

// The .nsp directly in dir: moved to updates\. Returns true if one is still being copied.
bool MoveNspsFrom(const std::wstring& dir, Moved& mv) {
    bool later = false;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(dir, L"*.nsp").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return false;
    std::vector<std::wstring> files;
    do {
        std::wstring n = fd.cFileName;
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && n.size() > 4 && Same(n.substr(n.size() - 4), L".nsp")) files.push_back(n);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    for (const std::wstring& n : files) {
        std::wstring from = Join(dir, n), to = Join(UpdatesDir(), n);
        if (StillChanging(from)) { later = true; continue; }
        if (!HasPfs0(from)) continue;
        if (GetFileAttributesW(to.c_str()) != INVALID_FILE_ATTRIBUTES) {
            ReportOnce(mv, Format(L2(L"%ls is in %ls and updates already has one with that name: delete one of the two.",
                                        L"%ls est\u00e1 en %ls y updates ya tiene uno con ese nombre: borra uno de los dos."), n.c_str(), RelPath(dir).c_str()));
            continue;
        }
        EnsureDir(UpdatesDir());
        if (MoveFileW(from.c_str(), to.c_str()))
            mv.nsps.push_back(n);
        else
            later = true;   // in use: next time
    }
    return later;
}

bool FixMisplaced(int ed, Moved& mv) {
    if (RunnerBusy()) return false;   // the recompilation reads both folders
    bool later = false;
    // 1) .nsp in mods\, mods\<edition>\ or a folder of mods\<edition>\ (a folder left empty by it goes away)
    std::wstring mods = Join(g.root, L"mods");
    if (DirExists(mods)) {
        later |= MoveNspsFrom(mods, mv);
        for (int e = 0; e < 2; e++) {
            std::wstring dir = ModsDir(e);
            if (!DirExists(dir)) continue;
            later |= MoveNspsFrom(dir, mv);
            WIN32_FIND_DATAW fd;
            HANDLE h = FindFirstFileW(Join(dir, L"*").c_str(), &fd);
            std::vector<std::wstring> sub;
            if (h != INVALID_HANDLE_VALUE) {
                do {
                    std::wstring n = fd.cFileName;
                    if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && !n.empty() && n[0] != L'.') sub.push_back(n);
                } while (FindNextFileW(h, &fd));
                FindClose(h);
            }
            for (const std::wstring& n : sub) {
                std::wstring d = Join(dir, n);
                if (IsUpdateLayer(d)) continue;   // made by the recompilation
                size_t before = mv.nsps.size();
                later |= MoveNspsFrom(d, mv);
                if (mv.nsps.size() > before && !HasAnyFile(d)) DeleteTree(d);
            }
        }
    }
    // 2) mods (a folder with romfs, or a romfs folder) in updates\: moved to mods\<edition>\<name>
    std::wstring upd = UpdatesDir();
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(Join(upd, L"*").c_str(), &fd);
    std::vector<std::wstring> dirs;
    if (h != INVALID_HANDLE_VALUE) {
        do {
            std::wstring n = fd.cFileName;
            if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && !n.empty() && n[0] != L'.') dirs.push_back(n);
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    for (const std::wstring& n : dirs) {
        std::wstring picked = Join(upd, n);
        ModSource src = ResolveMod(picked);
        if (src.romfs.empty()) continue;   // no romfs in it (or several): not something to move
        if (!src.titleId.empty() && src.ed < 0) {
            ReportOnce(mv, Format(L2(L"updates\\%ls is a mod for another game (title ID %ls): it was left there.",
                                        L"updates\\%ls es un mod de otro juego (ID %ls): se dej\u00f3 ah\u00ed."), n.c_str(), src.titleId.c_str()));
            continue;
        }
        if (StillChanging(picked)) { later = true; continue; }
        if (!HasAnyFile(src.romfs)) continue;
        int to = src.ed >= 0 ? src.ed : ed;
        std::wstring base = Same(n, L"romfs") ? std::wstring(L"mod") : src.name;
        std::wstring name = base;
        for (int i = 2; !NameProblem(to, name).empty() && i < 100; i++) name = base + Format(L" (%d)", i);
        if (!NameProblem(to, name).empty()) continue;
        std::wstring dst = Join(ModsDir(to), name);
        EnsureDir(ModsDir(to));
        bool whole = Same(src.romfs, Join(picked, L"romfs"));   // <folder>\romfs: the folder is the mod
        bool ok;
        if (whole) {
            ok = MoveFileW(picked.c_str(), dst.c_str()) != 0;
        } else {
            ok = CreateDirectoryW(dst.c_str(), nullptr) && MoveFileW(src.romfs.c_str(), Join(dst, L"romfs").c_str());
            if (!ok) {
                RemoveDirectoryW(dst.c_str());
            } else if (!Same(n, L"romfs")) {
                // The rest of the pack (code patches the port does not use) goes along, inside the mod (the game only reads romfs).
                if (HasAnyFile(picked)) MoveFileW(picked.c_str(), Join(dst, L"not_used_by_the_port").c_str());
                else DeleteTree(picked);
            }
        }
        if (!ok) { later = true; continue; }
        mv.mods.push_back({name, to});
    }
    return later;
}

bool SameVolume(const std::wstring& a, const std::wstring& b) {
    wchar_t va[MAX_PATH] = {}, vb[MAX_PATH] = {};
    if (!GetVolumePathNameW(a.c_str(), va, MAX_PATH) || !GetVolumePathNameW(b.c_str(), vb, MAX_PATH)) return true;   // try the rename
    return _wcsicmp(va, vb) == 0;
}

void AddMod() {
    if (Blocked(s.dlg, true)) return;
    std::wstring picked = IniGet(L"mods_add_source", L"");   // tests only: skips the folder picker
    if (picked.empty()) picked = PickPath(s.dlg, L2(L"Pick the folder of the mod to move to mods (the one with romfs inside)", L"Elige la carpeta del mod a mover a mods (la que tiene romfs dentro)"), true);
    if (picked.empty()) return;
    std::wstring modsRoot = Join(g.root, L"mods");
    if (picked.size() >= modsRoot.size() && _wcsnicmp(picked.c_str(), modsRoot.c_str(), modsRoot.size()) == 0) {
        Message(s.dlg, MB_ICONINFORMATION, L2(L"That folder is already inside the mods folder.", L"Esa carpeta ya est\u00e1 dentro de la carpeta de mods."));
        return;
    }
    ModSource src = ResolveMod(picked);
    if (!src.error.empty()) {
        Message(s.dlg, MB_ICONWARNING, src.error);
        return;
    }
    int ed = g.edition;
    if (!src.titleId.empty() && src.ed < 0) {
        Message(s.dlg, MB_ICONWARNING, Format(L2(L"This mod is for another game (title ID %ls). Tsukihime is %ls (Japanese) and %ls (Global).",
                                                 L"Este mod es para otro juego (ID %ls). Tsukihime es %ls (japonesa) y %ls (global)."),
                                              src.titleId.c_str(), L"01001DC01486A000", L"010057A01E4D4000"));
        return;
    }
    if (src.ed >= 0 && src.ed != ed) {
        int r = Message(s.dlg, MB_ICONQUESTION | MB_YESNOCANCEL,
                        Format(L2(L"This mod is for the %ls edition (title ID %ls), and the %ls edition is selected.\n\nAdd it to the %ls edition?",
                                  L"Este mod es para la edici\u00f3n %ls (ID %ls), y est\u00e1 elegida la edici\u00f3n %ls.\n\n\u00bfAgregarlo a la edici\u00f3n %ls?"),
                               EditionName(src.ed).c_str(), src.titleId.c_str(), EditionName(ed).c_str(), EditionName(src.ed).c_str()));
        if (r == IDCANCEL) return;
        if (r == IDYES) {
            ed = src.ed;
            if (GameRunning(ed)) {
                Message(s.dlg, MB_ICONINFORMATION, L2(L"Close the game first: it reads the mods when it starts.", L"Cierra el juego primero: lee los mods al iniciar."));
                return;
            }
        }
    }
    int files = 0;
    ULONGLONG bytes = 0;
    FILETIME newest{};
    CountTree(src.romfs, files, bytes, newest);
    if (files == 0) {
        Message(s.dlg, MB_ICONWARNING, Format(L2(L"%ls is empty.", L"%ls est\u00e1 vac\u00eda."), src.romfs.c_str()));
        return;
    }
    NameAsk ask;
    ask.ed = ed;
    ask.name = src.name;
    for (int i = 2; !NameProblem(ed, ask.name).empty() && i < 100; i++) ask.name = src.name + Format(L" (%d)", i);
    std::wstring count = FilesN(files);
    ask.text = Format(L2(L"This folder is moved as a mod of the %ls edition (%ls, %ls):\n%ls%ls\n\nName (when two mods have the same file, the last one in name order wins):",
                         L"Esta carpeta se mueve como mod de la edici\u00f3n %ls (%ls, %ls):\n%ls%ls\n\nNombre (si dos mods traen el mismo archivo, gana el \u00faltimo en orden alfab\u00e9tico):"),
                      EditionName(ed).c_str(), count.c_str(), SizeText(bytes).c_str(), src.romfs.c_str(),
                      src.code ? L2(L"\nIt also patches code (exefs / .ips): that part cannot be used.", L"\nTambi\u00e9n parchea c\u00f3digo (exefs / .ips): esa parte no se puede usar.") : L"");
    if (DialogBoxParamW(g.inst, MAKEINTRESOURCEW(IDD_MODNAME), s.dlg, NameProc, LPARAM(&ask)) != IDOK) return;

    std::wstring dir = ModsDir(ed);
    if (!EnsureDir(dir)) {
        Message(s.dlg, MB_ICONERROR, Format(L2(L"%ls could not be created.", L"No se pudo crear %ls."), RelPath(dir).c_str()));
        return;
    }
    // Moved, not copied. <folder>\romfs: the whole folder becomes the mod (its readme and the like go along);
    // otherwise (romfs deeper, Atmosphere / emulator layout) only the romfs folder is moved.
    std::wstring tmp = Join(dir, L".adding_" + ask.name);   // a name the game skips until the move is complete
    if (DirExists(tmp)) DeleteTree(tmp);
    bool whole = Same(src.romfs, Join(picked, L"romfs"));
    std::wstring from = whole ? picked : src.romfs;
    std::wstring to = whole ? tmp : Join(tmp, L"romfs");
    if (!whole && !CreateDirectoryW(tmp.c_str(), nullptr)) {
        Message(s.dlg, MB_ICONERROR, Format(L2(L"%ls could not be created.", L"No se pudo crear %ls."), RelPath(tmp).c_str()));
        return;
    }
    DWORD e = 0;
    bool ok = false;
    if (SameVolume(from, dir)) {
        ok = MoveFileW(from.c_str(), to.c_str()) != 0;   // same drive: a rename
        if (!ok) e = GetLastError();
    }
    if (!ok && (e == 0 || e == ERROR_NOT_SAME_DEVICE)) {
        // Another drive: file by file with progress (each one copied and deleted); a failure or Cancel puts them back.
        CopyTask t;
        t.move = true;
        t.text = Format(L2(L"Moving to mods\\%ls\\%ls", L"Moviendo a mods\\%ls\\%ls"), kEditions[ed].key, ask.name.c_str());
        PlanTree(from, to, t);
        ok = RunCopy(s.dlg, t);
        if (ok) {
            if (!HasAnyFile(from)) DeleteTree(from);   // the empty folders left behind
        } else {
            e = t.error;
            for (size_t i = 0; i < t.completed && i < t.files.size(); i++)
                MoveFileExW(t.files[i].second.c_str(), t.files[i].first.c_str(), MOVEFILE_COPY_ALLOWED);
        }
    }
    if (ok && !MoveFileW(tmp.c_str(), Join(dir, ask.name).c_str())) {
        e = GetLastError();
        // Put it back where it was (same drive) before giving up.
        if (MoveFileW(to.c_str(), from.c_str())) ok = false;
        else ok = false, e = GetLastError();
    }
    if (!ok) {
        if (DirExists(tmp) && HasAnyFile(tmp)) {   // could not be put back: say where it is (never deleted)
            Message(s.dlg, MB_ICONERROR, Format(L2(L"The mod could not be finished (%ls). Its files are in %ls: rename that folder or move them back.",
                                                   L"No se pudo terminar de mover el mod (%ls). Sus archivos est\u00e1n en %ls: renombra esa carpeta o devu\u00e9lvelos."),
                                                ErrorText(e).c_str(), RelPath(tmp).c_str()));
            FullRefresh();
            return;
        }
        if (DirExists(tmp)) DeleteTree(tmp);   // empty
        if (e != ERROR_CANCELLED && e != ERROR_REQUEST_ABORTED)
            Message(s.dlg, MB_ICONERROR, Format(L2(L"The mod could not be moved (%ls). Nothing was changed.", L"No se pudo mover el mod (%ls). No se cambi\u00f3 nada."), ErrorText(e).c_str()));
        FullRefresh();
        return;
    }
    if (ed != g.edition) {
        g.edition = ed;
        IniSet(L"edition", kEditions[ed].key);
    }
    s.modRows.assign(1, L"\x01");
    RefreshAll();
    for (size_t i = 0; i < s.m.layers.size(); i++)
        if (Same(s.m.layers[i].folder, ask.name)) {
            ListView_SetItemState(s.mods, int(i), LVIS_SELECTED | LVIS_FOCUSED, LVIS_SELECTED | LVIS_FOCUSED);
            ListView_EnsureVisible(s.mods, int(i), FALSE);
        }
    RenderDetails();
    RenderButtons();
}

void AddNsp() {
    if (Blocked(s.dlg, false)) return;
    std::wstring picked = IniGet(L"updates_add_source", L"");   // tests only: skips the file picker
    if (picked.empty()) picked = PickPath(s.dlg, L2(L"Pick the update or patch (.nsp) to move to updates", L"Elige la actualizaci\u00f3n o parche (.nsp) a mover a updates"), false,
                                          L2(L"Updates and patches (*.nsp)", L"Actualizaciones y parches (*.nsp)"), L"*.nsp");
    if (picked.empty()) return;
    std::wstring file = Leaf(picked);
    if (file.size() < 5 || !Same(file.substr(file.size() - 4), L".nsp") || !HasPfs0(picked)) {
        Message(s.dlg, MB_ICONWARNING, L2(L"That is not an .nsp. Updates and translation patches come as .nsp.",
                                          L"Eso no es un .nsp. Las actualizaciones y parches de traducci\u00f3n vienen como .nsp"));
        return;
    }
    std::wstring dir = UpdatesDir();
    if (Same(Parent(picked), dir)) {
        Message(s.dlg, MB_ICONINFORMATION, L2(L"That file is already in updates.", L"Ese archivo ya est\u00e1 en updates."));
        return;
    }
    while (!file.empty() && file[0] == L'_') file.erase(0, 1);   // a leading _ would make the recompilation skip it
    std::wstring name = UpdateName(file);
    for (int ed = 0; ed < 2; ed++) {
        std::wstring layer = Join(ModsDir(ed), name);
        if ((DirExists(layer) && !IsUpdateLayer(layer)) || (DirExists(Join(ModsDir(ed), L"_" + name)) && !IsUpdateLayer(Join(ModsDir(ed), L"_" + name)))) {
            Message(s.dlg, MB_ICONWARNING, Format(L2(L"There is a mod added by hand called %ls in mods\\%ls, the name this update would get: rename that mod first.",
                                                     L"Hay un mod agregado a mano llamado %ls en mods\\%ls, el nombre que tendr\u00eda esta actualizaci\u00f3n: renombra antes ese mod."),
                                                  name.c_str(), kEditions[ed].key));
            return;
        }
    }
    std::wstring dst = Join(dir, file);
    WIN32_FILE_ATTRIBUTE_DATA a{}, b{};
    if (GetFileAttributesExW(dst.c_str(), GetFileExInfoStandard, &b)) {
        GetFileAttributesExW(picked.c_str(), GetFileExInfoStandard, &a);
        if (a.nFileSizeLow == b.nFileSizeLow && a.nFileSizeHigh == b.nFileSizeHigh && CompareFileTime(&a.ftLastWriteTime, &b.ftLastWriteTime) == 0) {
            Message(s.dlg, MB_ICONINFORMATION, L2(L"That .nsp is already in updates.", L"Ese .nsp ya est\u00e1 en updates."));
            return;
        }
        if (Message(s.dlg, MB_ICONQUESTION | MB_YESNO | MB_DEFBUTTON2,
                    Format(L2(L"updates already has a different %ls. Replace it?", L"updates ya tiene otro %ls. \u00bfReemplazarlo?"), file.c_str())) != IDYES)
            return;
    }
    if (!EnsureDir(dir)) {
        Message(s.dlg, MB_ICONERROR, L2(L"The updates folder could not be created.", L"No se pudo crear la carpeta updates."));
        return;
    }
    // Moved (not copied): on the same drive it is a rename; on another one Windows copies it and deletes the original.
    std::wstring part = dst + L".partial";   // the recompilation only reads *.nsp: a half move is never taken
    DeleteFileW(part.c_str());
    CopyTask t;
    t.move = true;
    t.text = Format(L2(L"Moving to updates\\%ls", L"Moviendo a updates\\%ls"), file.c_str());
    t.files.push_back({picked, part});
    WIN32_FILE_ATTRIBUTE_DATA info{};
    if (GetFileAttributesExW(picked.c_str(), GetFileExInfoStandard, &info)) t.total = (ULONGLONG(info.nFileSizeHigh) << 32) | info.nFileSizeLow;
    bool moved = RunCopy(s.dlg, t);
    bool keptOriginal = false;
    if (!moved && t.error != ERROR_CANCELLED && t.error != ERROR_REQUEST_ABORTED && FileExists(picked)) {
        // Copied but the original could not be deleted (read-only file or drive): keep the copy.
        WIN32_FILE_ATTRIBUTE_DATA c{};
        if (GetFileAttributesExW(part.c_str(), GetFileExInfoStandard, &c) && ((ULONGLONG(c.nFileSizeHigh) << 32) | c.nFileSizeLow) == t.total)
            moved = keptOriginal = true;
    }
    if (moved && !MoveFileExW(part.c_str(), dst.c_str(), MOVEFILE_REPLACE_EXISTING)) {
        DWORD e = GetLastError();
        if (!keptOriginal) MoveFileExW(part.c_str(), picked.c_str(), MOVEFILE_COPY_ALLOWED);   // put it back where it was
        DeleteFileW(part.c_str());
        Message(s.dlg, MB_ICONERROR, Format(L2(L"The .nsp could not be moved (%ls).", L"No se pudo mover el .nsp (%ls)."), ErrorText(e).c_str()));
        FullRefresh();
        return;
    }
    if (!moved) {
        DeleteFileW(part.c_str());   // a cancelled move between drives leaves the original where it was
        if (t.error != ERROR_CANCELLED && t.error != ERROR_REQUEST_ABORTED)
            Message(s.dlg, MB_ICONERROR, Format(L2(L"The .nsp could not be moved (%ls).", L"No se pudo mover el .nsp (%ls)."), ErrorText(t.error).c_str()));
        FullRefresh();
        return;
    }
    FullRefresh();
    for (size_t i = 0; i < s.m.nsps.size(); i++)
        if (Same(s.m.nsps[i].file, file)) {
            ListView_SetItemState(s.upd, int(i), LVIS_SELECTED | LVIS_FOCUSED, LVIS_SELECTED | LVIS_FOCUSED);
            ListView_EnsureVisible(s.upd, int(i), FALSE);
        }
    RenderDetails();
    RenderButtons();
    if (Message(s.dlg, MB_ICONQUESTION | MB_YESNO,
                std::wstring(keptOriginal ? L2(L"Copied to updates (the original could not be deleted). ", L"Copiado a updates (no se pudo borrar el original). ")
                                          : L2(L"Moved to updates. ", L"Movido a updates. ")) +
                L2(L"It is installed when you recompile the edition it is for (it takes a few minutes).\n\nGo to Install and recompile now?",
                   L"Se instala al recompilar la edici\u00f3n a la que pertenece (tarda unos minutos).\n\n\u00bfIr a Instalar y recompilar ahora?")) == IDYES) {
        PreselectInstall(g.edition);
        ShowPage(kPageInstall);
    }
}

void RemoveMod() {
    int row = SelectedRow(s.mods);
    if (row < 0 || row >= int(s.m.layers.size()) || Blocked(s.dlg, true)) return;
    const Layer l = s.m.layers[size_t(row)];
    int ed = s.m.ed;
    std::vector<std::wstring> nsps;   // the .nsp it came from, still in updates (it would be installed again)
    if (l.update)
        for (const NspItem& n : s.m.nsps) if (Same(n.name, l.name)) nsps.push_back(Join(UpdatesDir(), n.file));
    std::wstring t;
    if (l.update) {
        t = Format(L2(L"Remove the update %ls?\n\nIts mod folder goes to the Recycle Bin and its data (data\\%ls\\updates\\%ls) is deleted.",
                      L"\u00bfQuitar la actualizaci\u00f3n %ls?\n\nSu carpeta de mod va a la papelera y se borran sus datos (data\\%ls\\updates\\%ls)."),
                   l.name.c_str(), kEditions[ed].data, l.name.c_str());
        for (const std::wstring& n : nsps)
            t += Format(L2(L"\nupdates\\%ls also goes to the Recycle Bin (otherwise the next recompilation would install it again).",
                           L"\nTambi\u00e9n va a la papelera updates\\%ls (si no, la pr\u00f3xima recompilaci\u00f3n la volver\u00eda a instalar)."), Leaf(n).c_str());
        bool keeps = false;   // another active update brings the same main: the exe stays as it is
        for (const Layer& o : s.m.layers)
            if (&o != &s.m.layers[size_t(row)] && o.enabled && o.update && o.updData && o.reqMain == l.reqMain) keeps = true;
        if (s.m.built && !l.reqMain.empty() && l.reqMain == s.m.curMain && !keeps)
            t += L2(L"\n\nThe game is compiled with the main of this update: recompile afterwards.", L"\n\nEl juego est\u00e1 compilado con el main de esta actualizaci\u00f3n: recompila despu\u00e9s.");
    } else {
        t = Format(L2(L"Remove the mod %ls?\n\nIts folder (%ls) goes to the Recycle Bin.", L"\u00bfQuitar el mod %ls?\n\nSu carpeta (%ls) va a la papelera."),
                   l.name.c_str(), SizeText(l.bytes).c_str());
    }
    t += L2(L"\n\nTo only turn it off, untick it instead.", L"\n\nPara solo desactivarlo, desm\u00e1rcalo en vez de quitarlo.");
    if (Message(s.dlg, MB_ICONWARNING | MB_YESNO | MB_DEFBUTTON2, t) != IDYES) return;
    std::wstring folder = Join(ModsDir(ed), l.folder);
    if (!ToRecycleBin(s.dlg, folder)) {
        Message(s.dlg, MB_ICONWARNING, L2(L"The folder could not be removed (is a file of it open?). Nothing else was changed.",
                                          L"No se pudo quitar la carpeta (\u00bfhay un archivo suyo abierto?). No se cambi\u00f3 nada m\u00e1s."));
        FullRefresh();
        return;
    }
    if (l.update) {
        bool all = true;
        for (const std::wstring& n : nsps) all = ToRecycleBin(s.dlg, n) && all;
        DeleteTree(Join(UpdData(ed), l.name));   // the recompilation would delete it too ("its folder is gone")
        if (!all)
            Message(s.dlg, MB_ICONWARNING, L2(L"The update was removed, but its .nsp could not be moved to the Recycle Bin: remove it from updates, or the next recompilation installs it again.",
                                              L"La actualizaci\u00f3n se quit\u00f3, pero su .nsp no se pudo mover a la papelera: qu\u00edtalo de updates o la pr\u00f3xima recompilaci\u00f3n la vuelve a instalar."));
    }
    FullRefresh();
}

void RemoveNsp() {
    int row = SelectedRow(s.upd);
    if (row < 0 || row >= int(s.m.nsps.size()) || Blocked(s.dlg, false)) return;
    const NspItem n = s.m.nsps[size_t(row)];
    std::wstring t = Format(L2(L"Move updates\\%ls (%ls) to the Recycle Bin?", L"\u00bfMover updates\\%ls (%ls) a la papelera?"), n.file.c_str(), SizeText(n.size).c_str());
    if (n.state == NspState::Processed || n.state == NspState::Disabled)
        t += L2(L"\n\nIt is already installed: the update stays (remove it from the list above to uninstall it).",
                L"\n\nYa est\u00e1 instalada: la actualizaci\u00f3n se queda (qu\u00edtala de la lista de arriba para desinstalarla).");
    else if (n.state == NspState::Pending)
        t += L2(L"\n\nIt is not installed yet, so it will not be.", L"\n\nTodav\u00eda no est\u00e1 instalada, as\u00ed que ya no se instalar\u00e1.");
    else if (n.state == NspState::Reprocess || n.state == NspState::Changed)
        t += L2(L"\n\nThe installed version stays as it is.", L"\n\nLa versi\u00f3n instalada se queda como est\u00e1.");
    if (Message(s.dlg, MB_ICONQUESTION | MB_YESNO | MB_DEFBUTTON2, t) != IDYES) return;
    if (!ToRecycleBin(s.dlg, Join(UpdatesDir(), n.file)))
        Message(s.dlg, MB_ICONWARNING, L2(L"It could not be removed (is it open in another program?).", L"No se pudo quitar (\u00bfest\u00e1 abierto en otro programa?)."));
    FullRefresh();
}

void OpenSelectedMod() {
    int row = SelectedRow(s.mods);
    std::wstring dir = ModsDir(g.edition);
    if (row >= 0 && row < int(s.m.layers.size())) {
        OpenInShell(s.dlg, Join(dir, s.m.layers[size_t(row)].folder));
        return;
    }
    EnsureDir(dir);
    OpenInShell(s.dlg, dir);
}

void OpenSelectedNsp() {
    int row = SelectedRow(s.upd);
    if (row >= 0 && row < int(s.m.nsps.size())) {
        SelectInExplorer(Join(UpdatesDir(), s.m.nsps[size_t(row)].file));
        return;
    }
    EnsureDir(UpdatesDir());
    OpenInShell(s.dlg, UpdatesDir());
}

void SetupList(HWND list, const std::vector<std::pair<std::wstring, int>>& cols, bool checks) {
    DWORD ex = LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER | LVS_EX_LABELTIP | (checks ? LVS_EX_CHECKBOXES : 0);
    SendMessageW(list, LVM_SETEXTENDEDLISTVIEWSTYLE, ex, ex);
    SetWindowTheme(list, L"Explorer", nullptr);
    RECT rc;
    GetClientRect(list, &rc);
    int width = rc.right - GetSystemMetrics(SM_CXVSCROLL) - 2;
    for (size_t i = 0; i < cols.size(); i++) {
        LVCOLUMNW c{};
        c.mask = LVCF_TEXT | LVCF_WIDTH | LVCF_FMT;
        c.fmt = (i + 1 == cols.size() && i > 0) ? LVCFMT_RIGHT : LVCFMT_LEFT;
        c.cx = width * cols[i].second / 100;
        c.pszText = const_cast<wchar_t*>(cols[i].first.c_str());
        SendMessageW(list, LVM_INSERTCOLUMNW, i, LPARAM(&c));
    }
}

}  // namespace

// ---------------------------------------------------------------------------------------------- the tab
void RefreshMods() {
    if (!s.dlg) return;
    if (!IsWindowVisible(s.dlg)) {   // done when the tab is shown
        s.dirty = true;
        return;
    }
    FullRefresh();
}

void NoteRecompLine(const std::wstring& t) {
    for (int ed = 0; ed < 2; ed++) {
        std::wstring tail = Format(L": it is not for the %ls edition (skipped)", kEditions[ed].key);   // recomp_tsukihime.ps1, StepUpdates
        if (t.size() <= tail.size() || t.compare(t.size() - tail.size(), tail.size(), tail) != 0) continue;
        std::wstring file = t.substr(0, t.size() - tail.size());
        WIN32_FILE_ATTRIBUTE_DATA a{};
        if (!GetFileAttributesExW(Join(UpdatesDir(), file).c_str(), GetFileExInfoStandard, &a)) return;
        ULONGLONG size = (ULONGLONG(a.nFileSizeHigh) << 32) | a.nFileSizeLow;
        if (SkippedBy(ed, file, size)) return;
        // Keep the entries whose .nsp is still there, plus this one.
        std::wstring out, list = IniGet(SkipKey(ed).c_str(), L"");
        size_t p = 0;
        while (p < list.size()) {
            size_t q = list.find(L'|', p);
            std::wstring item = list.substr(p, q == std::wstring::npos ? std::wstring::npos : q - p);
            p = q == std::wstring::npos ? list.size() : q + 1;
            size_t star = item.rfind(L'*');
            if (star != std::wstring::npos && FileExists(Join(UpdatesDir(), item.substr(0, star)))) out += (out.empty() ? L"" : L"|") + item;
        }
        out += (out.empty() ? L"" : L"|") + file + Format(L"*%llu", size);
        IniSet(SkipKey(ed).c_str(), out);
        return;
    }
}

bool ModsAllowPlay(HWND owner) {
    int ed = g.edition;
    Moved moved;
    FixMisplaced(ed, moved);   // a .nsp in mods or a mod in updates is put in its place first
    if (!moved.empty()) {
        QueueNotices(moved);   // shown in the Mods tab
        if (s.notice.empty() || GetTickCount64() >= s.noticeUntil) NextNotice();
        s.dirty = true;
    }
    Model m = BuildModel(ed);
    if (!m.built) return true;
    std::wstring problem = BlockingProblem(m);
    std::vector<std::wstring> why = RecompileReasons(m);
    if (problem.empty() && why.empty()) return true;
    std::wstring t;
    if (!problem.empty())
        t = problem + L2(L"\n\nIf you play now, the game runs as it was last compiled and the mods may look wrong.\n\n"
                         L"Yes: go to the Mods and updates tab.  No: play anyway.",
                         L"\n\nSi juegas ahora, el juego corre como se compil\u00f3 la \u00faltima vez y los mods pueden verse mal.\n\n"
                         L"S\u00ed: ir a la pesta\u00f1a Mods y actualizaciones.  No: jugar igual.");
    else
        t = Format(L2(L"The mods and updates changed since this edition was compiled: it needs a recompilation (%ls).\n\n"
                      L"If you play now, those changes are not applied and the game may look wrong.\n\nYes: go to recompile.  No: play anyway.",
                      L"Los mods y actualizaciones cambiaron desde que se compil\u00f3 esta edici\u00f3n: hay que recompilarla (%ls).\n\n"
                      L"Si juegas ahora, esos cambios no se aplican y el juego puede verse mal.\n\nS\u00ed: ir a recompilar.  No: jugar igual."),
                   JoinReasons(why).c_str());
    int r = Message(owner, MB_ICONWARNING | MB_YESNOCANCEL, t);
    if (r == IDNO) return true;
    if (r == IDYES) {
        if (!problem.empty()) {
            ShowPage(kPageMods);
            RefreshMods();
        } else {
            PreselectInstall(ed);
            ShowPage(kPageInstall);
        }
    }
    return false;
}

void RefreshModsButtons() {
    if (!s.dlg || !IsWindowVisible(s.dlg)) return;
    bool running = GameRunning(g.edition), busy = RunnerBusy();
    if (running != s.lastRunning || busy != s.lastBusy) FullRefresh();   // statuses depend on it too
}

INT_PTR CALLBACK ModsProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_INITDIALOG: {
        s.dlg = dlg;
        s.mods = GetDlgItem(dlg, IDC_MODS_LIST);
        s.upd = GetDlgItem(dlg, IDC_UPD_LIST);
        EnableThemeDialogTexture(dlg, ETDT_ENABLETAB);
        ApplyDialogText(dlg, IDD_MODS);
        HWND combo = GetDlgItem(dlg, IDC_MODS_EDITION);
        for (int ed = 0; ed < 2; ed++) SendMessageW(combo, CB_ADDSTRING, 0, LPARAM(EditionName(ed).c_str()));
        SendMessageW(combo, CB_SETCURSEL, g.edition, 0);
        SetupList(s.mods, {{L2(L"Name", L"Nombre"), 34}, {L2(L"Type", L"Tipo"), 24}, {L2(L"Status", L"Estado"), 30}, {L2(L"Size", L"Tama\u00f1o"), 12}}, true);
        SetupList(s.upd, {{L2(L"File", L"Archivo"), 46}, {L2(L"Status", L"Estado"), 42}, {L2(L"Size", L"Tama\u00f1o"), 12}}, false);
        s.m.ed = g.edition;
        return TRUE;
    }
    case WM_SHOWWINDOW:
        if (wp && s.dirty) PostMessageW(dlg, WM_APP_TOGGLE, WPARAM(-1), 0);   // refresh once it is visible
        break;
    case WM_CTLCOLORSTATIC:
        if (GetDlgCtrlID(HWND(lp)) == IDC_MODS_BANNER && s.bannerColored) return ColorStatic(dlg, wp, lp, IDC_MODS_BANNER, s.bannerColor);
        break;
    case WM_NOTIFY: {
        auto* nm = reinterpret_cast<NMHDR*>(lp);
        if (nm->hwndFrom == s.mods && nm->code == LVN_ITEMCHANGED) {
            auto* ch = reinterpret_cast<NMLISTVIEW*>(lp);
            if (s.filling) break;
            if ((ch->uChanged & LVIF_STATE) && ((ch->uNewState ^ ch->uOldState) & LVIS_STATEIMAGEMASK) && ch->iItem >= 0 && (ch->uOldState & LVIS_STATEIMAGEMASK))
                PostMessageW(dlg, WM_APP_TOGGLE, WPARAM(ch->iItem), 0);   // after the list finished its own work
            if ((ch->uChanged & LVIF_STATE) && ((ch->uNewState ^ ch->uOldState) & LVIS_SELECTED)) {
                RenderDetails();
                RenderButtons();
            }
        }
        if (nm->hwndFrom == s.upd && nm->code == LVN_ITEMCHANGED && !s.filling) {
            auto* ch = reinterpret_cast<NMLISTVIEW*>(lp);
            if ((ch->uChanged & LVIF_STATE) && ((ch->uNewState ^ ch->uOldState) & LVIS_SELECTED)) {
                RenderDetails();
                RenderButtons();
            }
        }
        if (nm->hwndFrom == s.mods && nm->code == NM_DBLCLK) {
            auto* ia = reinterpret_cast<NMITEMACTIVATE*>(lp);
            LVHITTESTINFO hit{};
            hit.pt = ia->ptAction;
            SendMessageW(s.mods, LVM_SUBITEMHITTEST, 0, LPARAM(&hit));
            if (ia->iItem >= 0 && !(hit.flags & LVHT_ONITEMSTATEICON)) OpenSelectedMod();
        }
        if (nm->hwndFrom == s.upd && nm->code == NM_DBLCLK && reinterpret_cast<NMITEMACTIVATE*>(lp)->iItem >= 0) OpenSelectedNsp();
        break;
    }
    case WM_TIMER:
        if (wp == kTimerNotice || wp == kTimerRetry) {
            KillTimer(dlg, wp);
            if (wp == kTimerNotice) {
                s.notice.clear();
                NextNotice();
            }
            if (IsWindowVisible(dlg)) FullRefresh();
            else s.dirty = true;
            return TRUE;
        }
        break;
    case WM_APP_TOGGLE:
        if (int(wp) < 0) { if (s.dirty) FullRefresh(); }
        else Toggle(int(wp));
        return TRUE;
    case WM_APP_SHADERS: {
        auto* job = reinterpret_cast<ShaderJob*>(lp);
        s.shaderRunning = false;
        if (job->key.find(Join(DataDir(g.edition), L"analysis\\main.bin")) == 0) {   // still the same edition
            s.shaderKey = job->key;
            s.shaderMissing = job->missing;
        }
        delete job;
        s.modRows.assign(1, L"\x01");
        if (IsWindowVisible(dlg)) FullRefresh();
        else s.dirty = true;
        return TRUE;
    }
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case IDC_MODS_EDITION:
            if (HIWORD(wp) == CBN_SELCHANGE) {
                int ed = int(SendDlgItemMessageW(dlg, IDC_MODS_EDITION, CB_GETCURSEL, 0, 0));
                if (ed >= 0 && ed != g.edition) {
                    g.edition = ed;
                    IniSet(L"edition", kEditions[ed].key);
                    RefreshAll();
                }
            }
            return TRUE;
        case IDC_MODS_RECOMP:
            PreselectInstall(g.edition);
            ShowPage(kPageInstall);
            return TRUE;
        case IDC_MODS_ADD: AddMod(); return TRUE;
        case IDC_MODS_REMOVE: RemoveMod(); return TRUE;
        case IDC_MODS_OPEN: OpenSelectedMod(); return TRUE;
        case IDC_UPD_ADD: AddNsp(); return TRUE;
        case IDC_UPD_REMOVE: RemoveNsp(); return TRUE;
        case IDC_MODS_UPDATES: OpenSelectedNsp(); return TRUE;
        }
        break;
    }
    return FALSE;
}
