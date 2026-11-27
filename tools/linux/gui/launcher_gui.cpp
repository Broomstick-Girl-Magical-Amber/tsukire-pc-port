// launcher_gui.cpp - Graphical launcher of the native Linux build: Dear ImGui over SDL3 (SDL_Renderer, so it also runs
// without a Vulkan driver: it is the window that tells you what is missing). It is a thin front end of
// tools/linux/launcher.sh: every action is one command of that script, and what is shown comes from its --tsv output.
// It contains no code or data of the game.
//
//   framework/build/launcher/tsukihime_launcher [--root <folder>] [--shot <folder> [--run "<launcher.sh arguments>"] [--lang auto|en|es]]
//   --shot renders every tab to <folder>/<tab>.bmp and exits (tools/tests/test_linux_gui.sh); with --run it first runs that
//   command in the background runner and writes its log to <folder>/log.txt.
#include <SDL3/SDL.h>

#include <atomic>
#include <cfloat>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include "imgui.h"
#include "imgui_impl_sdl3.h"
#include "imgui_impl_sdlrenderer3.h"

// ---------------------------------------------------------------------------------------------------------------------
// Icon and language
// ---------------------------------------------------------------------------------------------------------------------
extern const int kIconW, kIconH;                // icon_data.cpp: the port's icon (tools/linux/make_gui_icon.py)
extern const unsigned char kIconRGBA[];
struct EsPair { const char* en; const char* es; };
extern const EsPair kEsTable[];                 // strings_es.cpp
extern const int kEsCount;

static bool g_es = false;                       // Spanish on
static std::string g_lang_setting = "auto";     // what launcher.ini says: auto, en or es

// T("English text") is the text in the language of the window; a text that is not in the table stays in English.
static const char* T(const char* en) {
    static std::unordered_map<std::string, const char*> map;
    if (map.empty()) for (int i = 0; i < kEsCount; i++) map[kEsTable[i].en] = kEsTable[i].es;
    if (!g_es) return en;
    auto it = map.find(en);
    return it == map.end() ? en : it->second;
}

// "auto" follows the system language (LC_ALL, LC_MESSAGES, LANGUAGE, LANG); "es" and "en" are fixed.
static void set_language(const std::string& setting) {
    g_lang_setting = setting == "es" || setting == "en" ? setting : "auto";
    if (g_lang_setting != "auto") { g_es = g_lang_setting == "es"; return; }
    const char* v = nullptr;
    for (const char* name : {"LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"})
        if (const char* e = std::getenv(name); e && *e) { v = e; break; }
    g_es = v && std::strncmp(v, "es", 2) == 0;
}

// ---------------------------------------------------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------------------------------------------------
static std::string g_root;     // repository folder
static std::string g_script;   // <root>/tools/linux/launcher.sh

static bool file_exists(const std::string& p) {
    struct stat st;
    return stat(p.c_str(), &st) == 0;
}

static std::string parent_of(const std::string& p) {
    size_t i = p.find_last_of('/');
    return i == std::string::npos || i == 0 ? std::string("/") : p.substr(0, i);
}

// The root is the first folder, going up from the executable and then from the current folder, with tools/linux/launcher.sh.
static std::string find_root(const std::string& given) {
    if (!given.empty()) return file_exists(given + "/tools/linux/launcher.sh") ? given : std::string();
    if (const char* e = std::getenv("LAUNCHER_ROOT"); e && *e && file_exists(std::string(e) + "/tools/linux/launcher.sh")) return e;
    char buf[4096];
    std::vector<std::string> starts;
    ssize_t n = readlink("/proc/self/exe", buf, sizeof buf - 1);
    if (n > 0) { buf[n] = 0; starts.push_back(parent_of(buf)); }
    if (getcwd(buf, sizeof buf)) starts.push_back(buf);
    for (std::string d : starts)
        for (int i = 0; i < 8 && d.size() > 1; i++, d = parent_of(d))
            if (file_exists(d + "/tools/linux/launcher.sh")) return d;
    return std::string();
}

// ---------------------------------------------------------------------------------------------------------------------
// Commands: launcher.sh <args...>, either captured (what the screens show) or run in the background (what the buttons do)
// ---------------------------------------------------------------------------------------------------------------------
static std::string run_capture(const std::vector<std::string>& args) {
    int fd[2];
    if (pipe(fd) != 0) return {};
    pid_t pid = fork();
    if (pid < 0) { close(fd[0]); close(fd[1]); return {}; }
    if (pid == 0) {
        dup2(fd[1], 1);
        int nul = open("/dev/null", O_WRONLY);
        if (nul >= 0) dup2(nul, 2);
        close(fd[0]); close(fd[1]);
        std::vector<char*> av;
        av.push_back(const_cast<char*>("bash"));
        av.push_back(const_cast<char*>(g_script.c_str()));
        for (auto& a : args) av.push_back(const_cast<char*>(a.c_str()));
        av.push_back(nullptr);
        execvp("bash", av.data());
        _exit(127);
    }
    close(fd[1]);
    std::string out;
    char b[4096];
    ssize_t n;
    while ((n = read(fd[0], b, sizeof b)) > 0) out.append(b, size_t(n));
    close(fd[0]);
    int st;
    waitpid(pid, &st, 0);
    return out;
}

static std::vector<std::string> split(const std::string& s, char sep) {
    std::vector<std::string> v;
    std::string cur;
    for (char c : s) {
        if (c == sep) { v.push_back(cur); cur.clear(); } else cur += c;
    }
    v.push_back(cur);
    return v;
}

// One background command with its output in a log. "detached" (the game): it keeps running if this window closes.
struct Runner {
    std::mutex m;
    std::string log;
    std::atomic<bool> running{false};
    std::atomic<int> rc{0};
    std::atomic<int> generation{0};   // +1 when a command ends: the screens refresh
    pid_t pid = 0;
    bool detached = false;
    std::thread th;

    void append(const std::string& s) {
        std::lock_guard<std::mutex> lk(m);
        log += s;
        if (log.size() > 400000) log.erase(0, log.size() / 2);
    }
    std::string text() { std::lock_guard<std::mutex> lk(m); return log; }
    void clear() { std::lock_guard<std::mutex> lk(m); log.clear(); }

    bool start(const std::vector<std::string>& args, bool is_game, const std::vector<std::pair<std::string, std::string>>& env = {}) {
        if (running) return false;
        if (th.joinable()) th.join();
        int fd[2];
        if (pipe(fd) != 0) return false;
        std::string shown = "$ launcher.sh";
        for (auto& a : args) shown += " " + a;
        append(shown + "\n");
        pid_t p = fork();
        if (p < 0) { close(fd[0]); close(fd[1]); return false; }
        if (p == 0) {
            setsid();
            signal(SIGPIPE, SIG_IGN);   // the game keeps running if this window closes
            dup2(fd[1], 1); dup2(fd[1], 2);
            close(fd[0]); close(fd[1]);
            int nul = open("/dev/null", O_RDONLY);
            if (nul >= 0) dup2(nul, 0);   // no terminal: the script must not ask anything
            for (auto& e : env) setenv(e.first.c_str(), e.second.c_str(), 1);
            std::vector<char*> av;
            av.push_back(const_cast<char*>("bash"));
            av.push_back(const_cast<char*>(g_script.c_str()));
            for (auto& a : args) av.push_back(const_cast<char*>(a.c_str()));
            av.push_back(nullptr);
            execvp("bash", av.data());
            _exit(127);
        }
        close(fd[1]);
        pid = p;
        detached = is_game;
        running = true;
        int rfd = fd[0];
        th = std::thread([this, rfd, p] {
            char b[4096];
            ssize_t n;
            while ((n = read(rfd, b, sizeof b)) > 0) append(std::string(b, size_t(n)));
            close(rfd);
            int st = 0;
            waitpid(p, &st, 0);
            rc = WIFEXITED(st) ? WEXITSTATUS(st) : 128 + (WIFSIGNALED(st) ? WTERMSIG(st) : 0);
            append("[ended with code " + std::to_string(rc.load()) + "]\n");
            running = false;
            generation++;
        });
        return true;
    }
    void stop() { if (running && pid > 0) kill(-pid, SIGTERM); }
    void finish(bool kill_it) {
        if (running && kill_it && !detached) stop();
        if (th.joinable()) { if (running && detached) th.detach(); else th.join(); }
    }
} g_run;

// Opens a folder with the desktop (xdg-open); a terminal with a command for the things that need the password.
static void spawn_detached(const std::vector<std::string>& args) {
    pid_t p = fork();
    if (p != 0) { if (p > 0) { int st; waitpid(p, &st, WNOHANG); } return; }
    setsid();
    if (fork() != 0) _exit(0);
    int nul = open("/dev/null", O_RDWR);
    if (nul >= 0) { dup2(nul, 0); dup2(nul, 1); dup2(nul, 2); }
    std::vector<char*> av;
    for (auto& a : args) av.push_back(const_cast<char*>(a.c_str()));
    av.push_back(nullptr);
    execvp(av[0], av.data());
    _exit(127);
}

static bool in_path(const char* prog) {
    const char* path = std::getenv("PATH");
    if (!path) return false;
    for (auto& d : split(path, ':'))
        if (!d.empty() && access((d + "/" + prog).c_str(), X_OK) == 0) return true;
    return false;
}

static void open_folder(const std::string& p) {
    if (in_path("xdg-open")) spawn_detached({"xdg-open", p});
}

// Runs "launcher.sh <args>" in a terminal and waits for Enter at the end. Returns false if there is no terminal program.
static bool run_in_terminal(const std::vector<std::string>& args) {
    std::vector<std::string> cmd = {"bash", "-c", "bash \"$0\" \"$@\"; echo; read -r -p 'Press Enter to close this window... ' _", g_script};
    for (auto& a : args) cmd.push_back(a);
    struct Term { const char* prog; std::vector<std::string> pre; };
    const Term terms[] = {
        {"x-terminal-emulator", {"-e"}}, {"gnome-terminal", {"--"}}, {"konsole", {"-e"}}, {"xfce4-terminal", {"-x"}},
        {"kitty", {}}, {"alacritty", {"-e"}}, {"foot", {}}, {"tilix", {"-e"}}, {"mate-terminal", {"-x"}}, {"xterm", {"-e"}},
    };
    for (auto& t : terms) {
        if (!in_path(t.prog)) continue;
        std::vector<std::string> full = {t.prog};
        for (auto& x : t.pre) full.push_back(x);
        for (auto& x : cmd) full.push_back(x);
        spawn_detached(full);
        return true;
    }
    return false;
}

// ---------------------------------------------------------------------------------------------------------------------
// What the screens show (parsed from the --tsv output of the script)
// ---------------------------------------------------------------------------------------------------------------------
struct EditionInfo { std::string state = "?", text; int saves = -1; };

// The script's own text is English; in Spanish the state picks the text.
static std::string ed_text(const EditionInfo& e) {
    if (!g_es) return e.text;
    if (e.state == "ready") return T("ready");
    if (e.state == "running") return T("running");
    if (e.state == "nodata") return T("built, but the data folder has no romfs_source.bin: recompile it");
    if (e.state == "winonly") return T("only the Windows .exe is built: recompile with -Target linux");
    if (e.state == "notbuilt") return T("not built: use the Install tab");
    return e.text;
}
struct Layer { std::string name, state, note; };
struct Nsp { std::string name, state; long long size = 0; };
struct Check { std::string level, msg; };
struct Action { std::string id, desc, sw; };
struct TitleOpt { std::string key, value, state, help; };
struct Option { std::string name, value, state, var, help, accepts; };

struct Model {
    std::string keys = "none", vulkan = "unknown", vulkan_cmd, vulkan_note;
    std::vector<std::string> dumps;
    bool tools = false;
    EditionInfo ed[2];                    // japan, global
    std::string save_count, save_last;    // of the selected edition
    std::vector<std::string> copies;      // newest first
    std::vector<Layer> layers;
    std::vector<Nsp> nsps;
    std::vector<Check> checks;
    // Controls tab (controls.py through launcher.sh controls | title | options --tsv)
    std::vector<Action> actions;
    std::vector<std::string> binds[2][32];       // [keyboard, mouse][action]: the keys / buttons
    bool dev_custom[2] = {false, false};         // the section is in input.ini (it replaces the defaults)
    std::string input_file, input_exists = "0";
    std::vector<std::string> input_notes;
    std::vector<TitleOpt> title;
    std::string title_file;
    std::vector<Option> options;
    std::string options_file, options_warn;
};

static const char* const kEd[2] = {"japan", "global"};
static const char* const kEdName[2] = {"Japanese", "Global"};

static std::vector<std::string> g_key_names, g_mouse_names, g_gpus;   // loaded once (they do not change while it runs)
static bool g_gpus_loaded = false;

static void refresh_controls(Model& n, int e) {
    if (g_key_names.empty()) {
        for (auto& line : split(run_capture({"controls", "keys", "--tsv"}), '\n')) {
            auto f = split(line, '\t');
            if (f.size() >= 2 && f[0] == "key") g_key_names.push_back(f[1]);
            else if (f.size() >= 2 && f[0] == "mouse") g_mouse_names.push_back(f[1]);
        }
    }
    if (!g_gpus_loaded) {
        g_gpus_loaded = true;
        for (auto& line : split(run_capture({"options", "gpus", "--tsv"}), '\n')) {
            auto f = split(line, '\t');
            if (f.size() >= 2 && f[0] == "gpu") g_gpus.push_back(f[1]);
        }
    }
    for (auto& line : split(run_capture({"controls", "--tsv", "-e", kEd[e]}), '\n')) {
        auto f = split(line, '\t');
        if (f.size() < 2) continue;
        if (f[0] == "file" && f.size() >= 4) { n.input_file = f[2]; n.input_exists = f[3]; }
        else if (f[0] == "note") n.input_notes.push_back(f[1]);
        else if (f[0] == "action" && f.size() >= 4 && n.actions.size() < 32) n.actions.push_back({f[1], f[2], f[3]});
        else if (f[0] == "device" && f.size() >= 3) n.dev_custom[f[1] == "mouse" ? 1 : 0] = f[2] == "custom";
        else if (f[0] == "bind" && f.size() >= 4) {
            int d = f[1] == "mouse" ? 1 : 0;
            for (size_t i = 0; i < n.actions.size(); i++)
                if (n.actions[i].id == f[2]) { for (auto& k : split(f[3], ',')) { size_t a = k.find_first_not_of(' '); if (a != std::string::npos) n.binds[d][i].push_back(k.substr(a)); } }
        }
    }
    for (auto& line : split(run_capture({"title", "--tsv", "-e", kEd[e]}), '\n')) {
        auto f = split(line, '\t');
        if (f[0] == "title" && f.size() >= 5) n.title.push_back({f[1], f[2], f[3], f[4]});
        else if (f[0] == "file" && f.size() >= 3) n.title_file = f[2];
    }
    for (auto& line : split(run_capture({"options", "--tsv", "-e", kEd[e]}), '\n')) {
        auto f = split(line, '\t');
        if (f[0] == "option" && f.size() >= 7) n.options.push_back({f[1], f[2], f[3], f[4], f[5], f[6]});
        else if (f[0] == "file" && f.size() >= 3) n.options_file = f[2];
        else if (f[0] == "warn" && f.size() >= 2) n.options_warn = f[1];
    }
}

static void refresh(Model& m, int e, bool with_controls) {
    Model n;
    for (auto& line : split(run_capture({"status", "--tsv", "-e", "both"}), '\n')) {
        auto f = split(line, '\t');
        if (f.size() < 2) continue;
        if (f[0] == "keys") n.keys = f[1];
        else if (f[0] == "dump" && !f[1].empty()) n.dumps.push_back(f[1]);
        else if (f[0] == "tools") n.tools = f[1] == "yes";
        else if (f[0] == "vulkan") n.vulkan = f[1];
        else if (f[0] == "edition" && f.size() >= 4) {
            for (int i = 0; i < 2; i++) if (f[1] == kEd[i]) { n.ed[i].state = f[2]; n.ed[i].text = f[3]; }
        } else if (f[0] == "saves" && f.size() >= 3) {
            for (int i = 0; i < 2; i++) if (f[1] == kEd[i]) n.ed[i].saves = std::atoi(f[2].c_str());
        }
    }
    if (n.vulkan == "no")
        for (auto& line : split(run_capture({"vulkan", "show", "--tsv"}), '\n')) {
            auto f = split(line, '\t');
            if (f.size() >= 3 && f[0] == "vulkan_cmd") { n.vulkan_cmd = f[1]; n.vulkan_note = f[2]; }
        }
    for (auto& line : split(run_capture({"saves", "list", "--tsv", "-e", kEd[e]}), '\n')) {
        auto f = split(line, '\t');
        if (f[0] == "save" && f.size() >= 3) { n.save_count = f[1]; n.save_last = f[2]; }
        else if (f[0] == "copy" && f.size() >= 3) n.copies.push_back(f[2]);
    }
    for (auto& line : split(run_capture({"mods", "list", "--tsv", "-e", kEd[e]}), '\n')) {
        auto f = split(line, '\t');
        if (f[0] == "layer" && f.size() >= 4) n.layers.push_back({f[1], f[2], f[3]});
        else if (f[0] == "nsp" && f.size() >= 4) n.nsps.push_back({f[1], f[2], std::atoll(f[3].c_str())});
        else if (f[0] == "check" && f.size() >= 3) n.checks.push_back({f[1], f[2]});
    }
    if (with_controls) refresh_controls(n, e);
    else { n.actions = m.actions; for (int d = 0; d < 2; d++) { for (int i = 0; i < 32; i++) n.binds[d][i] = m.binds[d][i]; n.dev_custom[d] = m.dev_custom[d]; }
           n.input_file = m.input_file; n.input_exists = m.input_exists; n.input_notes = m.input_notes; n.title = m.title; n.title_file = m.title_file;
           n.options = m.options; n.options_file = m.options_file; n.options_warn = m.options_warn; }
    m = std::move(n);
}

// ---------------------------------------------------------------------------------------------------------------------
// File dialogs (the desktop's, through SDL; the callback may run on another thread: the result waits in a mailbox)
// ---------------------------------------------------------------------------------------------------------------------
static std::mutex g_pick_m;
static std::string g_picked;
static int g_pick_target = -1;   // which text box asked for it

static void SDLCALL pick_cb(void*, const char* const* list, int) {
    std::lock_guard<std::mutex> lk(g_pick_m);
    if (list && list[0]) g_picked = list[0];
}

// ---------------------------------------------------------------------------------------------------------------------
// The window
// ---------------------------------------------------------------------------------------------------------------------
struct Ui {
    int edition = 0;
    int force_tab = -1;                  // --shot: tab to select
    char gpu_hang[16] = "";
    char import_path[512] = "";
    char mod_path[512] = "";
    char nsp_path[512] = "";
    int sel_copy = -1, sel_layer = -1, sel_nsp = -1;
    std::string popup_title, popup_text;
    std::vector<std::string> popup_cmd;
    std::vector<std::pair<std::string, std::string>> popup_env;
    bool popup_open = false;
    SDL_Window* window = nullptr;
    // Controls tab
    int cur_tab = 0, cur_sub = 0, force_sub = -1, cur_psub = 0, force_psub = -1;
    bool edit_open = false, capturing = false;
    int edit_dev = 0, edit_action = -1, edit_stage = 0;          // stage 0 = choosing, 1 = a key is also used elsewhere
    std::vector<std::string> edit_items;
    std::string edit_clash;
    char key_pick[32] = "";
    char win_size[24] = "", render_size[24] = "", gpu_text[96] = "";
    bool opts_synced = false;
} g_ui;

static void ask(const std::string& title, const std::string& text, std::vector<std::string> cmd,
                std::vector<std::pair<std::string, std::string>> env = {}) {
    g_ui.popup_title = title; g_ui.popup_text = text; g_ui.popup_cmd = std::move(cmd); g_ui.popup_env = std::move(env);
    g_ui.popup_open = true;
}

static void run_cmd(const std::vector<std::string>& a, bool game = false, std::vector<std::pair<std::string, std::string>> env = {}) {
    g_run.clear();
    g_run.start(a, game, env);
}

static std::vector<std::string> with_ed(std::vector<std::string> a) {
    a.push_back("-e"); a.push_back(kEd[g_ui.edition]);
    return a;
}

// the colours of the Windows launcher (launcher/src/mods.cpp): readable on the light background
static const ImVec4 kGreen(0.0f, 0.431f, 0.0f, 1), kOrange(0.667f, 0.333f, 0.0f, 1), kRed(0.745f, 0.0f, 0.0f, 1), kGrey(0.38f, 0.38f, 0.38f, 1);

static void line(const char* label, const std::string& value, ImVec4 col) {
    ImGui::TextUnformatted(label);
    ImGui::SameLine(150);
    ImGui::TextColored(col, "%s", value.c_str());
}

static void path_row(const char* id, char* buf, size_t n, int target, bool folder, const char* hint) {
    ImGui::PushID(id);
    ImGui::SetNextItemWidth(ImGui::GetContentRegionAvail().x - 110);
    ImGui::InputTextWithHint("##p", hint, buf, n);
    ImGui::SameLine();
    if (ImGui::Button(T("Browse..."), ImVec2(100, 0))) {
        g_pick_target = target;
        if (folder) SDL_ShowOpenFolderDialog(pick_cb, nullptr, g_ui.window, nullptr, false);
        else SDL_ShowOpenFileDialog(pick_cb, nullptr, g_ui.window, nullptr, 0, nullptr, false);
    }
    ImGui::PopID();
}

// Shown in the Play and Install tabs when there is no Vulkan loader or driver: the exact command of this distribution.
static void vulkan_help(const Model& m) {
    if (m.vulkan != "no") return;
    ImGui::Spacing();
    ImGui::TextColored(kRed, "%s", T("Vulkan is missing"));
    if (m.vulkan_cmd.empty()) {
        ImGui::TextWrapped("%s", T("This distribution is not known: install the Vulkan loader and the driver of your graphics card with your package manager."));
        return;
    }
    ImGui::TextWrapped("%s", T("The game needs the Vulkan loader and a driver for your graphics card. This is the command for your system:"));
    {   // the note comes from the script in English: the known pieces are translated here
        std::string note = m.vulkan_note;
        if (g_es) {
            auto sub = [&](const char* a, const char* b) { size_t i = note.find(a); if (i != std::string::npos) note.replace(i, std::strlen(a), b); };
            sub("no GPU found, virtual machine?: the CPU driver llvmpipe is installed", "no se encontró GPU, ¿máquina virtual?: se instala el driver por CPU llvmpipe");
            sub("graphics card:", "tarjeta gráfica:");
            sub("; NVIDIA proprietary: its own driver", "; NVIDIA propietario: su propio driver");
        }
        ImGui::PushStyleColor(ImGuiCol_Text, kGrey);
        ImGui::TextWrapped("%s", note.c_str());
        ImGui::PopStyleColor();
    }
    static char cmd[512];
    std::snprintf(cmd, sizeof cmd, "%s", m.vulkan_cmd.c_str());
    ImGui::SetNextItemWidth(-FLT_MIN);
    ImGui::InputText("##vkcmd", cmd, sizeof cmd, ImGuiInputTextFlags_ReadOnly);
    static Uint64 copied_at = 0;
    if (ImGui::Button(T("Copy the command"))) { SDL_SetClipboardText(m.vulkan_cmd.c_str()); copied_at = SDL_GetTicks(); }
    ImGui::SameLine();
    ImGui::BeginDisabled(g_run.running);
    if (ImGui::Button(T("Run it in a terminal"))) {
        if (!run_in_terminal({"vulkan", "install"}))
            g_run.append(std::string(T("No terminal program found. Run in a terminal: ")) + "bash tools/linux/launcher.sh vulkan install\n");
    }
    ImGui::EndDisabled();
    if (copied_at && SDL_GetTicks() - copied_at < 2500) { ImGui::SameLine(); ImGui::TextColored(kGreen, "%s", T("Copied.")); }
    ImGui::PushStyleColor(ImGuiCol_Text, kGrey);
    ImGui::TextWrapped("%s", T("It asks for your password. On NVIDIA with the proprietary driver, install that driver instead; on a virtual machine without a GPU the CPU driver works but is slow."));
    ImGui::PopStyleColor();
}

static bool g_shot_mode = false;     // --shot: the pictures walk through the tabs with force_* held
static bool g_refresh_req = false;   // set by widgets that need new data (the main loop reads it)

// "Edition" drop-down, the same one at the top of every page that depends on the edition (like the Windows launcher).
static void edition_combo() {
    ImGui::AlignTextToFramePadding();
    ImGui::TextUnformatted(T("Edition"));
    ImGui::SameLine(90 * ImGui::GetStyle().FontScaleMain);
    ImGui::SetNextItemWidth(240);
    if (ImGui::BeginCombo("##edition", T(kEdName[g_ui.edition]))) {
        for (int i = 0; i < 2; i++)
            if (ImGui::Selectable(T(kEdName[i]), g_ui.edition == i) && g_ui.edition != i) {
                g_ui.edition = i; g_ui.sel_copy = g_ui.sel_layer = g_ui.sel_nsp = -1; g_ui.opts_synced = false; g_refresh_req = true;
            }
        ImGui::EndCombo();
    }
}

// A framed box with a title, like the group boxes of the Windows launcher.
static void group_begin(const char* id, const char* title) {
    ImGui::Spacing();
    ImGui::TextColored(ImVec4(0.0f, 0.2f, 0.6f, 1), "%s", title);
    ImGui::BeginChild(id, ImVec2(0, 0), ImGuiChildFlags_Borders | ImGuiChildFlags_AutoResizeY);
}
static void group_end() { ImGui::EndChild(); }

static void game_group(const Model& m) {
    const EditionInfo& e = m.ed[g_ui.edition];
    edition_combo();
    ImGui::Spacing();
    line("prod.keys", m.keys == "data" ? T("found in data/prod.keys") : m.keys == "root" ? T("found in the root, the recompile moves it") : T("MISSING: put it in data/ or in the root"),
         m.keys == "none" ? kRed : kGreen);
    line(T("Dump"), m.dumps.empty() ? T("none in the root: put your .xci or .nsp there") : m.dumps[0] + (m.dumps.size() > 1 ? ", +" + std::to_string(m.dumps.size() - 1) : ""),
         m.dumps.empty() ? kOrange : kGreen);
    line(T("Tools"), m.tools ? T("installed") : T("not installed or incomplete: use the Install tab"), m.tools ? kGreen : kOrange);
    line("Vulkan", m.vulkan == "yes" ? T("loader and driver found") : m.vulkan == "no" ? T("no loader or no driver: the game cannot start, Install offers to install it") : "unknown",
         m.vulkan == "yes" ? kGreen : m.vulkan == "no" ? kRed : kGrey);
    line(T(kEdName[g_ui.edition]), ed_text(e), e.state == "ready" || e.state == "running" ? kGreen : kOrange);
    vulkan_help(m);
    ImGui::Spacing();
    ImGui::TextUnformatted(T("GPU hang limit in ms, empty is the default. In a virtual machine without a GPU try 120000:"));
    ImGui::SetNextItemWidth(140);
    ImGui::InputText("##gh", g_ui.gpu_hang, sizeof g_ui.gpu_hang, ImGuiInputTextFlags_CharsDecimal);
    ImGui::Spacing();
    if (ImGui::Button(T("Show the game log"))) run_cmd({"log"});
    ImGui::SameLine();
    if (ImGui::Button(T("Open data folder"))) open_folder(g_root + "/data/" + (g_ui.edition == 0 ? "tsuki" : "tsuki_global"));
    ImGui::SameLine();
    if (ImGui::Button(T("Install / recompile..."))) g_ui.force_tab = 1;
    ImGui::Spacing();
}

// The Play button of the footer.
static void play_button(const Model& m) {
    const EditionInfo& e = m.ed[g_ui.edition];
    ImGui::BeginDisabled(!(e.state == "ready" && !g_run.running));
    if (ImGui::Button(T("Play"), ImVec2(96, 0))) {
        auto a = with_ed({"play"});
        if (g_ui.gpu_hang[0]) { a.push_back("--gpu-hang"); a.push_back(g_ui.gpu_hang); }
        run_cmd(a, true);
    }
    ImGui::EndDisabled();
}

static void tab_saves(const Model& m) {
    ImGui::Spacing();
    if (m.save_count.empty() || m.save_count == "0") ImGui::TextUnformatted(T("No saves yet."));
    else ImGui::Text(T("%s files, last change %s"), m.save_count.c_str(), m.save_last.c_str());
    ImGui::Spacing();
    ImGui::TextUnformatted(T("Copies, newest first:"));
    if (ImGui::BeginListBox("##copies", ImVec2(-FLT_MIN, 110))) {
        for (int i = 0; i < int(m.copies.size()); i++)
            if (ImGui::Selectable(m.copies[i].c_str(), g_ui.sel_copy == i)) g_ui.sel_copy = i;
        ImGui::EndListBox();
    }
    bool idle = !g_run.running;
    ImGui::BeginDisabled(!idle || m.save_count.empty() || m.save_count == "0");
    if (ImGui::Button(T("Back up now"))) run_cmd(with_ed({"saves", "backup"}));
    ImGui::EndDisabled();
    ImGui::SameLine();
    ImGui::BeginDisabled(!idle || g_ui.sel_copy < 0 || g_ui.sel_copy >= int(m.copies.size()));
    if (ImGui::Button(T("Restore the selected copy...")) && g_ui.sel_copy >= 0 && g_ui.sel_copy < int(m.copies.size()))
        ask(T("Restore saves"), T("The current saves are copied first to <date>_before_restore and then replaced by:\n") + m.copies[g_ui.sel_copy],
            with_ed({"saves", "restore", std::to_string(g_ui.sel_copy + 1)}));
    ImGui::EndDisabled();
    ImGui::SameLine();
    if (ImGui::Button(T("Open saves folder"))) open_folder(g_root + "/data/" + (g_ui.edition == 0 ? "tsuki" : "tsuki_global") + "/user/save");
    ImGui::SameLine();
    if (ImGui::Button(T("Open copies folder"))) open_folder(g_root + "/save_backups/" + kEd[g_ui.edition]);
    ImGui::Spacing();
    ImGui::Separator();
    ImGui::Spacing();
    ImGui::TextUnformatted(T("Import ONE save file. It replaces the matching one, and all the saves are copied first:"));
    path_row("imp", g_ui.import_path, sizeof g_ui.import_path, 0, false, T("path of the save file"));
    ImGui::BeginDisabled(!idle || !g_ui.import_path[0]);
    if (ImGui::Button(T("Import this file...")) && g_ui.import_path[0])
        ask(T("Import a save file"), std::string(T("The matching save is replaced by:\n")) + g_ui.import_path + T("\nAll the current saves are copied first to <date>_before_import."),
            with_ed({"saves", "import", g_ui.import_path}), {{"LAUNCHER_ASSUME_YES", "1"}});
    ImGui::EndDisabled();
}

// ---------------------------------------------------------------------------------------------------------------------
// Controls tab: keyboard and mouse (input.ini), title screen (title.ini), game options (config.txt). Each change is one
// command of launcher.sh (controls | title | options) that writes the files and prints what it saved.
// ---------------------------------------------------------------------------------------------------------------------
// The name the game uses for a key pressed in this window ("" = a key the game does not know, or F11, which is fullscreen).
static std::string sdl_key_name(SDL_Scancode sc) {
    if (sc >= SDL_SCANCODE_A && sc <= SDL_SCANCODE_Z) return std::string(1, char('A' + (sc - SDL_SCANCODE_A)));
    if (sc >= SDL_SCANCODE_1 && sc <= SDL_SCANCODE_9) return std::string(1, char('1' + (sc - SDL_SCANCODE_1)));
    if (sc == SDL_SCANCODE_0) return "0";
    if (sc >= SDL_SCANCODE_F1 && sc <= SDL_SCANCODE_F12) return sc == SDL_SCANCODE_F11 ? "" : "F" + std::to_string(1 + sc - SDL_SCANCODE_F1);
    if (sc >= SDL_SCANCODE_F13 && sc <= SDL_SCANCODE_F24) return "F" + std::to_string(13 + sc - SDL_SCANCODE_F13);
    if (sc >= SDL_SCANCODE_KP_1 && sc <= SDL_SCANCODE_KP_9) return "Num" + std::to_string(1 + sc - SDL_SCANCODE_KP_1);
    if (sc == SDL_SCANCODE_KP_0) return "Num0";
    switch (sc) {
        case SDL_SCANCODE_SPACE: return "Space";
        case SDL_SCANCODE_RETURN: case SDL_SCANCODE_KP_ENTER: return "Enter";
        case SDL_SCANCODE_ESCAPE: return "Escape";
        case SDL_SCANCODE_TAB: return "Tab";
        case SDL_SCANCODE_BACKSPACE: return "Backspace";
        case SDL_SCANCODE_LSHIFT: case SDL_SCANCODE_RSHIFT: return "Shift";
        case SDL_SCANCODE_LCTRL: case SDL_SCANCODE_RCTRL: return "Ctrl";
        case SDL_SCANCODE_LALT: case SDL_SCANCODE_RALT: return "Alt";
        case SDL_SCANCODE_UP: return "Up";
        case SDL_SCANCODE_DOWN: return "Down";
        case SDL_SCANCODE_LEFT: return "Left";
        case SDL_SCANCODE_RIGHT: return "Right";
        case SDL_SCANCODE_PAGEUP: return "PageUp";
        case SDL_SCANCODE_PAGEDOWN: return "PageDown";
        case SDL_SCANCODE_HOME: return "Home";
        case SDL_SCANCODE_END: return "End";
        case SDL_SCANCODE_INSERT: return "Insert";
        case SDL_SCANCODE_DELETE: return "Delete";
        case SDL_SCANCODE_COMMA: return "Comma";
        case SDL_SCANCODE_PERIOD: return "Period";
        default: return "";
    }
}

static bool has_item(const std::vector<std::string>& v, const std::string& x) {
    for (auto& i : v) if (i == x) return true;
    return false;
}

static std::string join_items(const std::vector<std::string>& v) {
    std::string r;
    for (size_t i = 0; i < v.size(); i++) r += (i ? "," : "") + v[i];
    return r;
}

static void open_edit(int dev, int action, const Model& m) {
    g_ui.edit_dev = dev; g_ui.edit_action = action; g_ui.edit_stage = 0; g_ui.edit_clash.clear();
    g_ui.edit_items = m.binds[dev][action]; g_ui.capturing = false; g_ui.key_pick[0] = 0;
    g_ui.edit_open = true;
}

// Called from the main loop with every key press while the "press a key" box is waiting.
static void capture_key(SDL_Scancode sc) {
    if (!g_ui.capturing) return;
    std::string n = sdl_key_name(sc);
    if (n.empty()) return;   // unknown key or F11: keep waiting
    if (!has_item(g_ui.edit_items, n)) g_ui.edit_items.push_back(n);
    g_ui.capturing = false;
}

static void edit_popup(const Model& m) {
    if (g_ui.edit_open) { ImGui::OpenPopup(T("Assign")); g_ui.edit_open = false; }
    ImGui::SetNextWindowSizeConstraints(ImVec2(460, 0), ImVec2(FLT_MAX, FLT_MAX));
    if (!ImGui::BeginPopupModal(T("Assign"), nullptr, ImGuiWindowFlags_AlwaysAutoResize)) return;
    int d = g_ui.edit_dev, a = g_ui.edit_action;
    if (a < 0 || a >= int(m.actions.size())) { ImGui::CloseCurrentPopup(); ImGui::EndPopup(); return; }
    const Action& act = m.actions[a];
    ImGui::Text(T("%s: %s, %s on a Switch"), d == 0 ? T("Keyboard") : T("Mouse"), T(act.desc.c_str()), act.sw.c_str());
    ImGui::Separator();
    if (g_ui.edit_stage == 1) {
        ImGui::TextWrapped("%s", g_ui.edit_clash.c_str());
        ImGui::Spacing();
        std::string base = join_items(g_ui.edit_items);
        const char* dev = d == 0 ? "keyboard" : "mouse";
        if (ImGui::Button(T("Take it from there"), ImVec2(150, 0))) { run_cmd({"controls", "bind", dev, act.id, base, "--move"}); ImGui::CloseCurrentPopup(); }
        ImGui::SameLine();
        if (ImGui::Button(T("Use it in both"), ImVec2(150, 0))) { run_cmd({"controls", "bind", dev, act.id, base, "--share"}); ImGui::CloseCurrentPopup(); }
        ImGui::SameLine();
        if (ImGui::Button(T("Back"), ImVec2(100, 0))) g_ui.edit_stage = 0;
        ImGui::EndPopup();
        return;
    }
    ImGui::TextUnformatted(d == 0 ? T("Keys:") : T("Buttons:"));
    int remove = -1;
    for (int i = 0; i < int(g_ui.edit_items.size()); i++) {
        ImGui::PushID(i);
        if (ImGui::Button((g_ui.edit_items[i] + "  x").c_str())) remove = i;
        ImGui::PopID();
        ImGui::SameLine();
    }
    if (g_ui.edit_items.empty()) ImGui::TextDisabled("%s", T("none"));
    else ImGui::NewLine();
    if (remove >= 0) g_ui.edit_items.erase(g_ui.edit_items.begin() + remove);
    ImGui::Spacing();
    const std::vector<std::string>& names = d == 0 ? g_key_names : g_mouse_names;
    if (d == 0) {
        if (g_ui.capturing) {
            ImGui::TextColored(kOrange, "%s", T("Press the key now. F11 is the fullscreen key and cannot be used..."));
            if (ImGui::Button(T("Cancel capture"))) g_ui.capturing = false;
        } else if (ImGui::Button(T("Press a key..."))) g_ui.capturing = true;
        ImGui::SameLine();
    }
    ImGui::SetNextItemWidth(160);
    if (ImGui::BeginCombo("##add", g_ui.key_pick[0] ? g_ui.key_pick : (d == 0 ? T("or pick one") : T("pick a button")))) {
        for (auto& n : names)
            if (ImGui::Selectable(n.c_str())) {
                if (!has_item(g_ui.edit_items, n)) g_ui.edit_items.push_back(n);
                g_ui.key_pick[0] = 0;
            }
        ImGui::EndCombo();
    }
    ImGui::Spacing();
    ImGui::Separator();
    if (ImGui::Button(T("Save"), ImVec2(120, 0))) {
        // a key already used by another action asks what to do (same question as the Windows launcher)
        std::string clash;
        for (int o = 0; o < int(m.actions.size()) && clash.empty(); o++) {
            if (o == a) continue;
            for (auto& k : g_ui.edit_items)
                if (has_item(m.binds[d][o], k)) { clash = k + T(" is also used by \"") + std::string(T(m.actions[o].desc.c_str())) + "\", " + T("button") + " " + m.actions[o].id + "."; break; }
        }
        if (!clash.empty()) { g_ui.edit_clash = clash + T("\nTake it from there, or use it in both?"); g_ui.edit_stage = 1; }
        else {
            const char* dev = d == 0 ? "keyboard" : "mouse";
            if (g_ui.edit_items.empty()) run_cmd({"controls", "unbind", dev, act.id});
            else run_cmd({"controls", "bind", dev, act.id, join_items(g_ui.edit_items)});
            ImGui::CloseCurrentPopup();
        }
    }
    ImGui::SameLine();
    if (ImGui::Button(T("Cancel"), ImVec2(120, 0))) { g_ui.capturing = false; ImGui::CloseCurrentPopup(); }
    ImGui::EndPopup();
}

static void sub_bindings(const Model& m, int dev) {
    ImGui::Spacing();
    if (m.actions.empty()) { ImGui::TextDisabled("%s", T("No data folder yet: install first, in the Install tab.")); return; }
    ImGui::TextWrapped(T("File: %s%s"), m.input_file.c_str(), m.input_exists == "1" ? "" : T(", not created yet: the port's defaults apply"));
    for (auto& n : m.input_notes) ImGui::TextColored(kOrange, "%s", n.c_str());
    ImGui::TextDisabled("%s", T("Each change is saved at once, in both editions; the game reads the file when it starts."));
    bool idle = !g_run.running;
    if (ImGui::BeginTable("##binds", 4, ImGuiTableFlags_RowBg | ImGuiTableFlags_Borders | ImGuiTableFlags_ScrollY, ImVec2(0, ImGui::GetContentRegionAvail().y - 46))) {
        ImGui::TableSetupColumn(T("Button"), ImGuiTableColumnFlags_WidthFixed, 70);
        ImGui::TableSetupColumn(T("What it does in Tsukihime"), ImGuiTableColumnFlags_WidthStretch, 1.4f);
        ImGui::TableSetupColumn(dev == 0 ? T("Keys") : T("Mouse"), ImGuiTableColumnFlags_WidthStretch, 1.0f);
        ImGui::TableSetupColumn("##b", ImGuiTableColumnFlags_WidthFixed, 150);
        ImGui::TableSetupScrollFreeze(0, 1);
        ImGui::TableHeadersRow();
        for (int i = 0; i < int(m.actions.size()); i++) {
            const Action& a = m.actions[i];
            ImGui::TableNextRow();
            ImGui::TableSetColumnIndex(0); ImGui::TextUnformatted(a.id.c_str());
            ImGui::TableSetColumnIndex(1); ImGui::TextUnformatted(T(a.desc.c_str()));
            ImGui::TableSetColumnIndex(2);
            if (m.binds[dev][i].empty()) ImGui::TextDisabled("-");
            else { std::string j; for (size_t k = 0; k < m.binds[dev][i].size(); k++) j += (k ? ", " : "") + m.binds[dev][i][k]; ImGui::TextUnformatted(j.c_str()); }
            ImGui::TableSetColumnIndex(3);
            ImGui::PushID(i);
            ImGui::BeginDisabled(!idle);
            if (ImGui::SmallButton(T("Change..."))) open_edit(dev, i, m);
            ImGui::SameLine();
            if (ImGui::SmallButton(T("Clear"))) run_cmd({"controls", "unbind", dev == 0 ? "keyboard" : "mouse", a.id});
            ImGui::EndDisabled();
            ImGui::PopID();
        }
        ImGui::EndTable();
    }
    ImGui::BeginDisabled(!idle);
    if (ImGui::Button(T("Back to the port's defaults...")))
        ask(dev == 0 ? T("Keyboard") : T("Mouse"), dev == 0 ? T("Go back to the port's default keyboard? Its section of input.ini is removed, in both editions.") : T("Go back to the port's default mouse? Its section of input.ini is removed, in both editions."),
            {"controls", "reset", dev == 0 ? "keyboard" : "mouse"});
    ImGui::SameLine();
    if (ImGui::Button(T("Remove every assignment...")))
        ask(dev == 0 ? T("Keyboard") : T("Mouse"), dev == 0 ? T("Leave the keyboard with no assignments at all, in both editions?") : T("Leave the mouse with no assignments at all, in both editions?"),
            {"controls", "clear", dev == 0 ? "keyboard" : "mouse"});
    ImGui::EndDisabled();
    ImGui::SameLine();
    if (ImGui::Button(T("Open folder"))) open_folder(g_root + "/data");
    edit_popup(m);
}

static const TitleOpt* title_opt(const Model& m, const char* key) {
    for (auto& t : m.title) if (t.key == key) return &t;
    return nullptr;
}

static void sub_title(const Model& m) {
    ImGui::Spacing();
    if (m.title.empty()) { ImGui::TextDisabled("%s", T("No data folder yet: install first, in the Install tab.")); return; }
    ImGui::TextWrapped(T("File: %s"), m.title_file.c_str());
    for (auto& n : m.input_notes) if (n.find("title.ini") != std::string::npos) ImGui::TextColored(kOrange, "%s", n.c_str());
    ImGui::TextDisabled("%s", T("Saved at once, in both editions; the game reads it when it starts."));
    ImGui::Spacing();
    bool idle = !g_run.running;
    ImGui::BeginDisabled(!idle);
    static const char* const kVideoVal[] = {"auto", "1", "2", "3", "4", "random", "cycle"};
    const char* const kVideoTxt[] = {T("The game's own choice"), T("Video 1, the default"), T("Video 2, Arcueid's route cleared"),
                                            T("Video 3, Ciel's route started"), T("Video 4, game cleared"), T("A random one each time"), T("One after another")};
    const TitleOpt* v = title_opt(m, "video");
    int vi = 0;
    for (int i = 0; v && i < 7; i++) if (v->value == kVideoVal[i]) vi = i;
    ImGui::SetNextItemWidth(320);
    if (ImGui::BeginCombo(T("Background video"), kVideoTxt[vi])) {
        for (int i = 0; i < 7; i++)
            if (ImGui::Selectable(kVideoTxt[i], i == vi) && i != vi) run_cmd({"title", "set", "video", kVideoVal[i]});
        ImGui::EndCombo();
    }
    const TitleOpt* pool = title_opt(m, "pool");
    if (pool) {
        ImGui::TextUnformatted(T("Videos used by random and one-after-another:"));
        for (int n = 1; n <= 4; n++) {
            ImGui::SameLine();
            bool on = pool->value.find(char('0' + n)) != std::string::npos;
            bool was = on;
            if (ImGui::Checkbox(std::to_string(n).c_str(), &on) && on != was) {
                std::string out;
                for (int k = 1; k <= 4; k++) {
                    bool use = k == n ? on : pool->value.find(char('0' + k)) != std::string::npos;
                    if (use) out += (out.empty() ? "" : ",") + std::to_string(k);
                }
                if (!out.empty()) run_cmd({"title", "set", "pool", out});
            }
        }
    }
    static const char* const kMusicVal[] = {"follow", "game", "1", "2", "3", "4"};
    const char* const kMusicTxt[] = {T("The video's own music"), T("The game's own music"), T("Music of video 1"), T("Music of video 2"), T("Music of video 3"), T("Music of video 4")};
    const TitleOpt* mu = title_opt(m, "music");
    int mi = 0;
    for (int i = 0; mu && i < 6; i++) if (mu->value == kMusicVal[i]) mi = i;
    ImGui::SetNextItemWidth(320);
    if (ImGui::BeginCombo(T("Title music"), kMusicTxt[mi])) {
        for (int i = 0; i < 6; i++)
            if (ImGui::Selectable(kMusicTxt[i], i == mi) && i != mi) run_cmd({"title", "set", "music", kMusicVal[i]});
        ImGui::EndCombo();
    }
    struct Sw { const char* key; const char* text; };
    const Sw sws[] = {{"hotkeys", T("F5 / F6 change the video on the title screen")}, {"click", T("A click on the logo changes the video")},
                             {"unlock", T("Only the videos your save has unlocked")}};
    for (const Sw& sw : sws) {
        const TitleOpt* o = title_opt(m, sw.key);
        if (!o) continue;
        bool on = o->value == "on", was = on;
        if (ImGui::Checkbox(sw.text, &on) && on != was) run_cmd({"title", "set", sw.key, on ? "on" : "off"});
    }
    static int fade = 500, delay = 450;
    const TitleOpt* f = title_opt(m, "fade");
    const TitleOpt* dl = title_opt(m, "swap_delay");
    if (!ImGui::IsAnyItemActive()) {   // follow the file unless one of the boxes is being typed in
        if (f) fade = std::atoi(f->value.c_str());
        if (dl) delay = std::atoi(dl->value.c_str());
    }
    ImGui::SetNextItemWidth(120);
    ImGui::InputInt(T("Fade in ms"), &fade, 50, 100);
    if (ImGui::IsItemDeactivatedAfterEdit()) { if (fade < 0) fade = 0; if (fade > 5000) fade = 5000; run_cmd({"title", "set", "fade", std::to_string(fade)}); }
    ImGui::SetNextItemWidth(120);
    ImGui::InputInt(T("Wait after the swap in ms"), &delay, 50, 100);
    if (ImGui::IsItemDeactivatedAfterEdit()) { if (delay < 0) delay = 0; if (delay > 5000) delay = 5000; run_cmd({"title", "set", "swap_delay", std::to_string(delay)}); }
    ImGui::Spacing();
    if (ImGui::Button(T("Back to the game's own behaviour..."))) ask(T("Title screen"), T("Put the title screen back to the game's own behaviour, in both editions?"), {"title", "reset"});
    ImGui::EndDisabled();
    ImGui::SameLine();
    if (ImGui::Button(T("Open folder"))) open_folder(g_root + "/data");
}

static const Option* opt_of(const Model& m, const char* name) {
    for (auto& o : m.options) if (o.name == name) return &o;
    return nullptr;
}

static void sub_options(const Model& m) {
    ImGui::Spacing();
    edition_combo();
    ImGui::Spacing();
    if (m.options.empty()) { ImGui::TextDisabled("%s", T("No data folder yet: install first, in the Install tab.")); return; }
    ImGui::Text(T("Options of the %s edition. File: %s"), T(kEdName[g_ui.edition]), m.options_file.c_str());
    if (!m.options_warn.empty()) ImGui::TextColored(kOrange, "%s", m.options_warn.c_str());
    ImGui::PushStyleColor(ImGuiCol_Text, kGrey); ImGui::TextWrapped("%s", T("Saved at once; the game reads them when it starts. A setting made in the terminal or in the launch options wins over this file.")); ImGui::PopStyleColor();
    ImGui::Spacing();
    bool idle = !g_run.running;
    ImGui::BeginDisabled(!idle);
    struct Sw { const char* name; const char* text; };
    const Sw sws[] = {{"fullscreen", T("Start in fullscreen")}, {"strict-vsync", T("Strict vertical sync")},
                             {"sdl-pad", T("Extra support for PlayStation and Switch controllers")}, {"discord", T("Discord Rich Presence")}, {"mute", T("Start without sound")}};
    for (const Sw& sw : sws) {
        const Option* o = opt_of(m, sw.name);
        if (!o) continue;
        bool on = o->value == "on", was = on;
        if (ImGui::Checkbox(sw.text, &on) && on != was) run_cmd(with_ed({"options", "set", sw.name, on ? "on" : "off"}));
    }
    ImGui::Spacing();
    const Option* fps = opt_of(m, "fps");
    if (fps) {
        ImGui::SetNextItemWidth(200);
        if (ImGui::BeginCombo(T("Frame rate"), fps->value == "30" ? T("30 FPS") : T("60 FPS"))) {
            if (ImGui::Selectable(T("60 FPS"), fps->value != "30") && fps->value == "30") run_cmd(with_ed({"options", "set", "fps", "60"}));
            if (ImGui::Selectable(T("30 FPS"), fps->value == "30") && fps->value != "30") run_cmd(with_ed({"options", "set", "fps", "30"}));
            ImGui::EndCombo();
        }
    }
    const Option* hud = opt_of(m, "button-icons");
    if (hud) {
        static const char* const val[] = {"auto", "xbox", "ps", "switch", "keyboard", "off"};
        const char* const txt[] = {T("Automatic"), T("Xbox"), T("PlayStation"), T("Switch"), T("Keyboard"), T("Off")};
        int hi = 0;
        for (int i = 0; i < 6; i++) if (hud->value == val[i]) hi = i;
        ImGui::SetNextItemWidth(200);
        if (ImGui::BeginCombo(T("Button icons"), txt[hi])) {
            for (int i = 0; i < 6; i++) if (ImGui::Selectable(txt[i], i == hi) && i != hi) run_cmd(with_ed({"options", "set", "button-icons", val[i]}));
            ImGui::EndCombo();
        }
    }
    const Option* gpu = opt_of(m, "gpu");
    if (gpu) {
        std::string shown = gpu->value == "auto" ? T("Automatic") : gpu->value;
        ImGui::SetNextItemWidth(380);
        if (ImGui::BeginCombo(T("Graphics card"), shown.c_str())) {
            if (ImGui::Selectable(T("Automatic"), gpu->value == "auto") && gpu->value != "auto") run_cmd(with_ed({"options", "set", "gpu", "auto"}));
            for (auto& n : g_gpus) if (ImGui::Selectable(n.c_str(), n == gpu->value) && n != gpu->value) run_cmd(with_ed({"options", "set", "gpu", n}));
            ImGui::EndCombo();
        }
        if (g_gpus.empty()) ImGui::PushStyleColor(ImGuiCol_Text, kGrey); ImGui::TextWrapped("%s", T("vulkaninfo is not available, so the cards are not listed: type a part of the name below, discrete or integrated.")); ImGui::PopStyleColor();
        if (!g_ui.opts_synced) std::snprintf(g_ui.gpu_text, sizeof g_ui.gpu_text, "%s", gpu->value == "auto" ? "" : gpu->value.c_str());
        ImGui::SetNextItemWidth(380);
        ImGui::InputTextWithHint("##gpu", T("or type a part of its name: discrete, integrated, 0, 1..."), g_ui.gpu_text, sizeof g_ui.gpu_text);
        ImGui::SameLine();
        if (ImGui::Button(T("Use this##gpu"))) run_cmd(with_ed({"options", "set", "gpu", g_ui.gpu_text[0] ? g_ui.gpu_text : "auto"}));
    }
    const Option* rr = opt_of(m, "render-res");
    const Option* ws = opt_of(m, "window-size");
    if (!g_ui.opts_synced) {
        std::snprintf(g_ui.render_size, sizeof g_ui.render_size, "%s", rr && rr->value != "default" ? rr->value.c_str() : "");
        std::snprintf(g_ui.win_size, sizeof g_ui.win_size, "%s", ws && ws->value != "default" ? ws->value.c_str() : "");
        g_ui.opts_synced = true;
    }
    if (rr) {
        ImGui::SetNextItemWidth(160);
        ImGui::InputTextWithHint(T("Internal resolution"), T("default 1920x1080"), g_ui.render_size, sizeof g_ui.render_size);
        ImGui::SameLine();
        if (ImGui::Button(T("Use this##rr"))) run_cmd(with_ed({"options", "set", "render-res", g_ui.render_size[0] ? g_ui.render_size : "default"}));
    }
    if (ws) {
        ImGui::SetNextItemWidth(160);
        ImGui::InputTextWithHint(T("Window size"), T("default 1280x720"), g_ui.win_size, sizeof g_ui.win_size);
        ImGui::SameLine();
        if (ImGui::Button(T("Use this##ws"))) run_cmd(with_ed({"options", "set", "window-size", g_ui.win_size[0] ? g_ui.win_size : "default"}));
    }
    ImGui::Spacing();
    if (ImGui::Button(T("Back to the game's defaults..."))) { ask(T("Options"), g_ui.edition == 0 ? T("Put every option of the Japanese edition back to the game's default? The other lines of config.txt stay.") : T("Put every option of the Global edition back to the game's default? The other lines of config.txt stay."), with_ed({"options", "reset"})); g_ui.opts_synced = false; }
    ImGui::EndDisabled();
    ImGui::SameLine();
    if (ImGui::Button(T("Open folder"))) open_folder(g_root + "/data/" + (g_ui.edition == 0 ? "tsuki" : "tsuki_global"));
}

static void tab_controls(const Model& m) {
    if (ImGui::BeginTabBar("##ctl")) {
        const char* names[3] = {T("Keyboard"), T("Mouse"), T("Title screen")};
        for (int t = 0; t < 3; t++) {
            ImGuiTabItemFlags fl = g_ui.force_sub == t ? ImGuiTabItemFlags_SetSelected : 0;
            if (ImGui::BeginTabItem(names[t], nullptr, fl)) {
                g_ui.cur_sub = t;
                if (t == 0) sub_bindings(m, 0); else if (t == 1) sub_bindings(m, 1); else sub_title(m);
                ImGui::EndTabItem();
            }
        }
        ImGui::EndTabBar();
    }
}

static void tab_install(const Model& m) {
    ImGui::Spacing();
    edition_combo();
    ImGui::Spacing();
    ImGui::TextWrapped("%s%s", T("Install gets everything ready: the system packages with sudo, the tools and the libraries. Then it builds the native game of this edition. "),
                       T("It asks for your password once, so it opens in a terminal window. Recompile only rebuilds the game, with no password, and is what you need after adding a mod or an update."));
    ImGui::Spacing();
    line(T("Tools"), m.tools ? T("installed") : T("not installed or incomplete"), m.tools ? kGreen : kOrange);
    line("Vulkan", m.vulkan == "yes" ? T("loader and driver found") : T("missing: the installer shows the command to install it and asks"), m.vulkan == "yes" ? kGreen : kOrange);
    line(T(kEdName[g_ui.edition]), ed_text(m.ed[g_ui.edition]), m.ed[g_ui.edition].state == "ready" ? kGreen : kOrange);
    ImGui::Spacing();
    vulkan_help(m);
    ImGui::Spacing();
    ImGui::BeginDisabled(g_run.running);
    if (ImGui::Button(T("Install and build in a terminal"), ImVec2(260, 34))) {
        if (!run_in_terminal(with_ed({"install"}))) g_run.append(T("No terminal program found. Run in a terminal: bash tools/linux/launcher.sh install -e ") + std::string(kEd[g_ui.edition]) + "\n");
    }
    ImGui::SameLine();
    ImGui::BeginDisabled(!m.tools);
    if (ImGui::Button(T("Recompile this edition"), ImVec2(220, 34))) run_cmd(with_ed({"recomp"}));
    ImGui::EndDisabled();
    ImGui::EndDisabled();
    ImGui::Spacing();
    ImGui::Separator();
    ImGui::Spacing();
    ImGui::TextUnformatted(T("Applications menu"));
    ImGui::BeginDisabled(g_run.running);
    if (ImGui::Button(T("Add the launcher and the game to the applications menu, with the port's icon"))) run_cmd({"desktop", "add"});
    ImGui::SameLine();
    if (ImGui::Button(T("Remove them from the menu"))) run_cmd({"desktop", "remove"});
    ImGui::EndDisabled();
}

static const ImVec4& level_color(const std::string& l) { return l == "error" ? kRed : l == "warn" ? kOrange : l == "ok" ? kGreen : kGrey; }

static void tab_mods(const Model& m) {
    ImGui::Spacing();
    edition_combo();
    ImGui::Spacing();
    for (auto& c : m.checks) ImGui::TextColored(level_color(c.level), "%s", c.msg.c_str());
    bool idle = !g_run.running;
    ImGui::BeginDisabled(!idle || !m.tools);
    if (ImGui::Button(T("Recompile this edition"))) run_cmd(with_ed({"recomp"}));
    ImGui::EndDisabled();
    ImGui::Spacing();
    ImGui::Separator();
    ImGui::TextUnformatted(T("Mods. The game reads them in this order and the last one wins. Untick to disable:"));
    if (ImGui::BeginListBox("##layers", ImVec2(-FLT_MIN, 100))) {
        for (int i = 0; i < int(m.layers.size()); i++) {
            const Layer& l = m.layers[i];
            ImGui::PushID(i);
            bool on = l.state == "on";
            if (ImGui::Checkbox("##on", &on) && idle) run_cmd({"mods", on ? "enable" : "disable", l.name, "-e", kEd[g_ui.edition]});
            ImGui::SameLine();
            if (ImGui::Selectable((l.name + "   [" + l.note + "]").c_str(), g_ui.sel_layer == i)) g_ui.sel_layer = i;
            ImGui::PopID();
        }
        if (m.layers.empty()) ImGui::TextDisabled(T("No mods in mods/%s"), kEd[g_ui.edition]);
        ImGui::EndListBox();
    }
    path_row("mod", g_ui.mod_path, sizeof g_ui.mod_path, 1, true, T("folder of the mod to move into mods/"));
    ImGui::BeginDisabled(!idle);
    ImGui::BeginDisabled(!g_ui.mod_path[0]);
    if (ImGui::Button(T("Add this mod folder"))) run_cmd({"mods", "add", g_ui.mod_path, "-e", kEd[g_ui.edition]});
    ImGui::EndDisabled();
    ImGui::SameLine();
    ImGui::BeginDisabled(g_ui.sel_layer < 0 || g_ui.sel_layer >= int(m.layers.size()));
    if (ImGui::Button(T("Remove the selected mod...")) && g_ui.sel_layer >= 0 && g_ui.sel_layer < int(m.layers.size()))
        ask(T("Remove mod"), T("Move this layer to the trash?\n") + m.layers[g_ui.sel_layer].name, {"mods", "remove", m.layers[g_ui.sel_layer].name, "-e", kEd[g_ui.edition]});
    ImGui::EndDisabled();
    ImGui::EndDisabled();
    ImGui::SameLine();
    if (ImGui::Button(T("Open mods folder"))) open_folder(g_root + "/mods/" + kEd[g_ui.edition]);
    ImGui::Spacing();
    ImGui::Separator();
    ImGui::TextUnformatted(T("Updates and patches: the .nsp files in updates/, which the recompile of the right edition processes:"));
    if (ImGui::BeginListBox("##nsps", ImVec2(-FLT_MIN, 80))) {
        for (int i = 0; i < int(m.nsps.size()); i++) {
            const Nsp& n = m.nsps[i];
            char sz[32];
            snprintf(sz, sizeof sz, "%.1f MB", double(n.size) / 1048576.0);
            if (ImGui::Selectable((n.name + "   [" + n.state + ", " + sz + "]").c_str(), g_ui.sel_nsp == i)) g_ui.sel_nsp = i;
        }
        if (m.nsps.empty()) ImGui::TextDisabled("%s", T("No .nsp in updates/"));
        ImGui::EndListBox();
    }
    path_row("nsp", g_ui.nsp_path, sizeof g_ui.nsp_path, 2, false, T("the .nsp file to move into updates/"));
    ImGui::BeginDisabled(!idle);
    ImGui::BeginDisabled(!g_ui.nsp_path[0]);
    if (ImGui::Button(T("Add this .nsp"))) run_cmd({"updates", "add", g_ui.nsp_path});
    ImGui::EndDisabled();
    ImGui::SameLine();
    ImGui::BeginDisabled(g_ui.sel_nsp < 0 || g_ui.sel_nsp >= int(m.nsps.size()));
    if (ImGui::Button(T("Remove the selected .nsp...")) && g_ui.sel_nsp >= 0 && g_ui.sel_nsp < int(m.nsps.size()))
        ask(T("Remove update"), T("Move this file to the trash?\n") + m.nsps[g_ui.sel_nsp].name, {"updates", "remove", m.nsps[g_ui.sel_nsp].name});
    ImGui::EndDisabled();
    ImGui::EndDisabled();
    ImGui::SameLine();
    if (ImGui::Button(T("Open updates folder"))) open_folder(g_root + "/updates");
}

static void draw(Model& m, bool& want_refresh) {
    ImGuiIO& io = ImGui::GetIO();
    ImGui::SetNextWindowPos(ImVec2(0, 0));
    ImGui::SetNextWindowSize(io.DisplaySize);
    ImGui::Begin("##main", nullptr, ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoSavedSettings);
    const ImGuiStyle& st = ImGui::GetStyle();
    const float footer_h = ImGui::GetFrameHeight() + st.ItemSpacing.y + 4;
    const std::string txt = g_run.text();
    const bool show_log = g_run.running || !txt.empty();            // the output panel appears when a command has something to say
    const float log_h = show_log ? io.DisplaySize.y * 0.26f : 0.0f;

    ImGui::BeginChild("##pages", ImVec2(0, -(footer_h + log_h + (show_log ? 6.0f : 0.0f))), ImGuiChildFlags_None);
    if (ImGui::BeginTabBar("##tabs")) {
        const char* names[4] = {T("Play and saves"), T("Install and recompile"), T("Mods and updates"), T("Controls and title screen")};
        for (int t = 0; t < 4; t++) {
            ImGuiTabItemFlags fl = g_ui.force_tab == t ? ImGuiTabItemFlags_SetSelected : 0;
            if (ImGui::BeginTabItem(names[t], nullptr, fl)) {
                g_ui.cur_tab = t;
                ImGui::BeginChild("##body", ImVec2(0, 0), ImGuiChildFlags_Borders);
                if (t == 0) {
                    if (ImGui::BeginTabBar("##play")) {
                        const char* pn[2] = {T("Game and saves"), T("Options")};
                        for (int u = 0; u < 2; u++) {
                            ImGuiTabItemFlags pf = g_ui.force_psub == u ? ImGuiTabItemFlags_SetSelected : 0;
                            if (ImGui::BeginTabItem(pn[u], nullptr, pf)) {
                                g_ui.cur_psub = u;
                                ImGui::BeginChild("##pbody", ImVec2(0, 0), ImGuiChildFlags_None);
                                if (u == 0) {
                                    group_begin("##gamebox", T("Game")); game_group(m); group_end();
                                    group_begin("##savebox", T("Saved games")); tab_saves(m); group_end();
                                } else sub_options(m);
                                ImGui::EndChild();
                                ImGui::EndTabItem();
                            }
                        }
                        ImGui::EndTabBar();
                    }
                } else if (t == 1) tab_install(m); else if (t == 2) tab_mods(m); else tab_controls(m);
                ImGui::EndChild();
                ImGui::EndTabItem();
            }
        }
        ImGui::EndTabBar();
    }
    ImGui::EndChild();
    if (!g_shot_mode) g_ui.force_tab = -1;   // a button's request to change page applies once

    if (show_log) {
        if (ImGui::Button(T("Clear"))) g_run.clear();
        ImGui::SameLine();
        ImGui::BeginDisabled(!g_run.running);
        if (ImGui::Button(T("Stop"))) g_run.stop();
        ImGui::EndDisabled();
        ImGui::SameLine();
        ImGui::TextDisabled("%s", g_run.running ? T("running...") : T("idle"));
        ImGui::BeginChild("##log", ImVec2(0, -footer_h), ImGuiChildFlags_Borders);
        static size_t last_len = 0;
        ImGui::TextUnformatted(txt.c_str());
        if (txt.size() != last_len) { ImGui::SetScrollHereY(1.0f); last_len = txt.size(); }
        ImGui::EndChild();
    }

    // footer: the port's folder, the language, Play and Close (like the Windows launcher)
    {
        const char* const lv[] = {"auto", "en", "es"};
        const char* const lt[] = {T("Automatic"), "English", "Espa\xc3\xb1ol"};
        int li = g_lang_setting == "en" ? 1 : g_lang_setting == "es" ? 2 : 0;
        const float bw = 96.0f, lw = 170.0f, gap = st.ItemSpacing.x;
        ImGui::AlignTextToFramePadding();
        ImGui::Text("%s %s", T("Port folder:"), g_root.c_str());
        ImGui::SameLine(io.DisplaySize.x - (2 * bw + lw + 3 * gap + 12));
        ImGui::SetNextItemWidth(lw);
        if (ImGui::BeginCombo("##lang", lt[li])) {
            for (int i = 0; i < 3; i++)
                if (ImGui::Selectable(lt[i], i == li) && i != li) { set_language(lv[i]); run_cmd({"language", lv[i]}); }
            ImGui::EndCombo();
        }
        ImGui::SameLine();
        play_button(m);
        ImGui::SameLine();
        if (ImGui::Button(T("Close"), ImVec2(bw, 0))) { SDL_Event q{}; q.type = SDL_EVENT_QUIT; SDL_PushEvent(&q); }
    }

    if (g_ui.popup_open) { ImGui::OpenPopup(T("Confirm")); g_ui.popup_open = false; }
    if (ImGui::BeginPopupModal(T("Confirm"), nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
        ImGui::TextUnformatted(g_ui.popup_title.c_str());
        ImGui::Separator();
        ImGui::TextWrapped("%s", g_ui.popup_text.c_str());
        ImGui::Spacing();
        if (ImGui::Button(T("Yes"), ImVec2(120, 0))) { run_cmd(g_ui.popup_cmd, false, g_ui.popup_env); ImGui::CloseCurrentPopup(); }
        ImGui::SameLine();
        if (ImGui::Button(T("Cancel"), ImVec2(120, 0))) ImGui::CloseCurrentPopup();
        ImGui::EndPopup();
    }
    ImGui::End();
}

// Looks like the Windows launcher (native Win32 dialogs): light grey face, white edit boxes and lists, square 1 px borders,
// blue selection, compact controls.
static void apply_style(float scale) {
    ImGui::StyleColorsLight();
    ImGuiStyle& st = ImGui::GetStyle();
    st.WindowRounding = st.FrameRounding = st.TabRounding = st.ChildRounding = st.PopupRounding = st.GrabRounding = st.ScrollbarRounding = 0;
    st.FrameBorderSize = 1; st.ChildBorderSize = 1; st.PopupBorderSize = 1; st.TabBorderSize = 1; st.WindowBorderSize = 0;
    st.FramePadding = ImVec2(8, 3); st.ItemSpacing = ImVec2(8, 6); st.TabBarBorderSize = 1;
    auto c = [&](ImGuiCol i, int r, int g, int b, float a = 1.0f) { st.Colors[i] = ImVec4(r / 255.0f, g / 255.0f, b / 255.0f, a); };
    c(ImGuiCol_Text, 0, 0, 0); c(ImGuiCol_TextDisabled, 109, 109, 109);
    c(ImGuiCol_WindowBg, 240, 240, 240); c(ImGuiCol_ChildBg, 255, 255, 255); c(ImGuiCol_PopupBg, 240, 240, 240);
    c(ImGuiCol_Border, 173, 173, 173); c(ImGuiCol_BorderShadow, 0, 0, 0, 0);
    c(ImGuiCol_FrameBg, 255, 255, 255); c(ImGuiCol_FrameBgHovered, 229, 241, 251); c(ImGuiCol_FrameBgActive, 204, 228, 247);
    c(ImGuiCol_Button, 225, 225, 225); c(ImGuiCol_ButtonHovered, 229, 241, 251); c(ImGuiCol_ButtonActive, 204, 228, 247);
    c(ImGuiCol_Header, 0, 120, 215, 0.25f); c(ImGuiCol_HeaderHovered, 0, 120, 215, 0.15f); c(ImGuiCol_HeaderActive, 0, 120, 215, 0.35f);
    c(ImGuiCol_Tab, 240, 240, 240); c(ImGuiCol_TabHovered, 229, 241, 251); c(ImGuiCol_TabSelected, 255, 255, 255);
    c(ImGuiCol_TabSelectedOverline, 0, 120, 215); c(ImGuiCol_TabDimmed, 240, 240, 240); c(ImGuiCol_TabDimmedSelected, 255, 255, 255);
    c(ImGuiCol_CheckMark, 0, 0, 0); c(ImGuiCol_SliderGrab, 0, 120, 215); c(ImGuiCol_SliderGrabActive, 0, 90, 158);
    c(ImGuiCol_Separator, 173, 173, 173); c(ImGuiCol_TableHeaderBg, 240, 240, 240); c(ImGuiCol_TableBorderStrong, 173, 173, 173);
    c(ImGuiCol_TableBorderLight, 220, 220, 220); c(ImGuiCol_TableRowBg, 255, 255, 255); c(ImGuiCol_TableRowBgAlt, 255, 255, 255);
    c(ImGuiCol_ScrollbarBg, 240, 240, 240); c(ImGuiCol_ScrollbarGrab, 205, 205, 205); c(ImGuiCol_ScrollbarGrabHovered, 166, 166, 166);
    c(ImGuiCol_ScrollbarGrabActive, 96, 96, 96); c(ImGuiCol_TextSelectedBg, 0, 120, 215, 0.35f); c(ImGuiCol_ModalWindowDimBg, 0, 0, 0, 0.3f);
    c(ImGuiCol_TitleBg, 255, 255, 255); c(ImGuiCol_TitleBgActive, 255, 255, 255);
    st.ScaleAllSizes(scale);
    ImGuiIO& io = ImGui::GetIO();
    const char* fonts[] = {"/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
                           "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf", "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
                           "/usr/share/fonts/noto/NotoSans-Regular.ttf", "/usr/share/fonts/google-noto/NotoSans-Regular.ttf"};
    bool loaded = false;
    for (const char* f : fonts)
        if (file_exists(f) && io.Fonts->AddFontFromFileTTF(f, 15.0f)) { loaded = true; break; }
    if (!loaded) io.Fonts->AddFontDefault();
    st.FontScaleMain = scale;
}

int main(int argc, char** argv) {
    std::string root_arg, shot_dir, run_arg, lang_arg;
    for (int i = 1; i < argc; i++) {
        if (!std::strcmp(argv[i], "--root") && i + 1 < argc) root_arg = argv[++i];
        else if (!std::strcmp(argv[i], "--shot") && i + 1 < argc) shot_dir = argv[++i];
        else if (!std::strcmp(argv[i], "--run") && i + 1 < argc) run_arg = argv[++i];
        else if (!std::strcmp(argv[i], "--lang") && i + 1 < argc) lang_arg = argv[++i];   // auto, en or es, instead of launcher.ini
        else if (!std::strcmp(argv[i], "--help")) { std::puts("tsukihime_launcher [--root <folder of the port>]"); return 0; }
    }
    g_root = find_root(root_arg);
    if (g_root.empty()) {
        std::fprintf(stderr, "tsukihime_launcher: cannot find tools/linux/launcher.sh. Run it from the port folder or give --root <folder>\n");
        return 1;
    }
    g_script = g_root + "/tools/linux/launcher.sh";
    {   // language: --lang, else the key "language" of launcher.ini (the Windows launcher uses the same one), else the system's
        std::string lang = lang_arg;
        if (lang.empty()) {
            std::ifstream f(g_root + "/launcher.ini");
            std::string l;
            while (std::getline(f, l)) if (l.rfind("language=", 0) == 0) lang = l.substr(9);
            while (!lang.empty() && (lang.back() == '\r' || lang.back() == ' ')) lang.pop_back();
        }
        set_language(lang);
    }

    SDL_SetHint(SDL_HINT_APP_ID, "tsukihime_launcher");
    if (!SDL_Init(SDL_INIT_VIDEO)) {
        std::fprintf(stderr, "tsukihime_launcher: SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    SDL_Window* win = SDL_CreateWindow("Tsukihime PC port", 760, 720, SDL_WINDOW_RESIZABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY);
    if (!win) { std::fprintf(stderr, "tsukihime_launcher: window: %s\n", SDL_GetError()); return 1; }
    g_ui.window = win;
    SDL_SetWindowMinimumSize(win, 760, 560);
    if (SDL_Surface* ic = SDL_CreateSurfaceFrom(kIconW, kIconH, SDL_PIXELFORMAT_RGBA32, const_cast<unsigned char*>(kIconRGBA), kIconW * 4)) {
        SDL_SetWindowIcon(win, ic);   // the port's own icon (the window manager keeps its own copy)
        SDL_DestroySurface(ic);
    }
    SDL_Renderer* ren = SDL_CreateRenderer(win, nullptr);
    if (!ren) ren = SDL_CreateRenderer(win, SDL_SOFTWARE_RENDERER);
    if (!ren) { std::fprintf(stderr, "tsukihime_launcher: renderer: %s\n", SDL_GetError()); return 1; }
    SDL_SetRenderVSync(ren, 1);

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    float scale = SDL_GetWindowDisplayScale(win);
    if (scale < 1.0f) scale = 1.0f;
    apply_style(scale);
    ImGui_ImplSDL3_InitForSDLRenderer(win, ren);
    ImGui_ImplSDLRenderer3_Init(ren);

    Model model;
    {
        std::string ini;
        std::ifstream f(g_root + "/launcher.ini");
        std::string l;
        while (std::getline(f, l)) if (l.rfind("edition=", 0) == 0) ini = l.substr(8);
        while (!ini.empty() && (ini.back() == '\r' || ini.back() == ' ')) ini.pop_back();
        g_ui.edition = ini == "global" ? 1 : 0;
    }
    bool want_refresh = true, done = false;
    Uint64 last_refresh = 0;
    int last_gen = 0, shot_step = 0, shot_wait = 0;
    struct Shot { const char* name; int tab, sub, psub; bool popup; };   // tab: page; sub: Controls sub-tab; psub: Play sub-tab
    static const Shot shots[] = {{"play", 0, -1, 0, false}, {"install", 1, -1, -1, false}, {"mods", 2, -1, -1, false},
                                 {"controls", 3, 0, -1, false}, {"mouse", 3, 1, -1, false}, {"title", 3, 2, -1, false}, {"options", 0, -1, 1, false},
                                 {"assign", 3, 0, -1, true}};   // the last one shows the "assign keys" dialog over the keyboard list
    int popup_shown_step = -1;
    const int shot_count = int(sizeof shots / sizeof shots[0]);
    g_shot_mode = !shot_dir.empty();
    if (g_shot_mode) g_ui.force_tab = 0;
    int prev_tab = -1;
    bool run_started = false;

    while (!done) {
        SDL_Event ev;
        if (SDL_WaitEventTimeout(&ev, 40)) {
            do {
                ImGui_ImplSDL3_ProcessEvent(&ev);
                if (ev.type == SDL_EVENT_KEY_DOWN && g_ui.capturing) capture_key(ev.key.scancode);
                if (ev.type == SDL_EVENT_QUIT || (ev.type == SDL_EVENT_WINDOW_CLOSE_REQUESTED && ev.window.windowID == SDL_GetWindowID(win))) done = true;
            } while (SDL_PollEvent(&ev));
        }
        {   // a file chosen in the desktop's dialog
            std::lock_guard<std::mutex> lk(g_pick_m);
            if (!g_picked.empty()) {
                char* dst = g_pick_target == 0 ? g_ui.import_path : g_pick_target == 1 ? g_ui.mod_path : g_ui.nsp_path;
                snprintf(dst, 512, "%s", g_picked.c_str());
                g_picked.clear();
            }
        }
        Uint64 now = SDL_GetTicks();
        if (g_run.generation != last_gen) { last_gen = g_run.generation; want_refresh = true; g_ui.opts_synced = false; }
        const int page_key = g_ui.cur_tab * 4 + (g_ui.cur_tab == 0 ? g_ui.cur_psub : 0);
        if (page_key != prev_tab) { prev_tab = page_key; want_refresh = true; }
        if (g_refresh_req) { want_refresh = true; g_refresh_req = false; }
        if (!g_run.running && now - last_refresh > 5000) want_refresh = true;
        if (want_refresh) { refresh(model, g_ui.edition, g_ui.cur_tab == 3 || (g_ui.cur_tab == 0 && g_ui.cur_psub == 1)); want_refresh = false; last_refresh = SDL_GetTicks(); }

        if (!shot_dir.empty() && shots[shot_step].popup && popup_shown_step != shot_step && shot_wait >= 3 && !model.actions.empty()) {
            popup_shown_step = shot_step; open_edit(0, 3, model); shot_wait = 0;
        }
        ImGui_ImplSDLRenderer3_NewFrame();
        ImGui_ImplSDL3_NewFrame();
        ImGui::NewFrame();
        draw(model, want_refresh);
        ImGui::Render();
        SDL_SetRenderDrawColor(ren, 24, 24, 30, 255);
        SDL_RenderClear(ren);
        ImGui_ImplSDLRenderer3_RenderDrawData(ImGui::GetDrawData(), ren);

        if (!shot_dir.empty() && !run_arg.empty() && !run_started) { run_started = true; run_cmd(split(run_arg, ' ')); }
        if (!shot_dir.empty() && !run_arg.empty() && (g_run.running || !run_started || g_run.generation == 0)) { SDL_RenderPresent(ren); continue; }
        if (!shot_dir.empty() && ++shot_wait >= 6) {   // a few frames per tab so the layout and its data settle, then the picture
            SDL_Surface* s = SDL_RenderReadPixels(ren, nullptr);
            if (s) {
                SDL_SaveBMP(s, (shot_dir + "/" + shots[shot_step].name + ".bmp").c_str());
                SDL_DestroySurface(s);
            }
            shot_wait = 0;
            if (++shot_step >= shot_count) done = true;
            else { g_ui.force_tab = shots[shot_step].tab; g_ui.force_sub = shots[shot_step].sub; g_ui.force_psub = shots[shot_step].psub; }
        }
        SDL_RenderPresent(ren);
    }
    if (!shot_dir.empty() && !run_arg.empty()) { std::ofstream lf(shot_dir + "/log.txt"); lf << g_run.text(); }
    g_run.finish(true);
    ImGui_ImplSDLRenderer3_Shutdown();
    ImGui_ImplSDL3_Shutdown();
    ImGui::DestroyContext();
    SDL_DestroyRenderer(ren);
    SDL_DestroyWindow(win);
    SDL_Quit();
    return 0;
}
