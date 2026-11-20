// launcher_gui.cpp - Graphical launcher of the native Linux build: Dear ImGui over SDL3 (SDL_Renderer, so it also runs
// without a Vulkan driver: it is the window that tells you what is missing). It is a thin front end of
// tools/linux/launcher.sh: every action is one command of that script, and what is shown comes from its --tsv output.
// It contains no code or data of the game.
//
//   framework/build/launcher/tsukihime_launcher [--root <folder>] [--shot <folder> [--run "<launcher.sh arguments>"]]
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
struct Layer { std::string name, state, note; };
struct Nsp { std::string name, state; long long size = 0; };
struct Check { std::string level, msg; };

struct Model {
    std::string keys = "none", vulkan = "unknown";
    std::vector<std::string> dumps;
    bool tools = false;
    EditionInfo ed[2];                    // japan, global
    std::string save_count, save_last;    // of the selected edition
    std::vector<std::string> copies;      // newest first
    std::vector<Layer> layers;
    std::vector<Nsp> nsps;
    std::vector<Check> checks;
};

static const char* const kEd[2] = {"japan", "global"};
static const char* const kEdName[2] = {"Japanese", "Global"};

static void refresh(Model& m, int e) {
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

static const ImVec4 kGreen(0.35f, 0.80f, 0.45f, 1), kOrange(0.95f, 0.68f, 0.25f, 1), kRed(0.95f, 0.38f, 0.38f, 1), kGrey(0.65f, 0.65f, 0.7f, 1);

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
    if (ImGui::Button("Browse...", ImVec2(100, 0))) {
        g_pick_target = target;
        if (folder) SDL_ShowOpenFolderDialog(pick_cb, nullptr, g_ui.window, nullptr, false);
        else SDL_ShowOpenFileDialog(pick_cb, nullptr, g_ui.window, nullptr, 0, nullptr, false);
    }
    ImGui::PopID();
}

static void tab_play(const Model& m) {
    const EditionInfo& e = m.ed[g_ui.edition];
    ImGui::Spacing();
    line("prod.keys", m.keys == "data" ? "found (data/prod.keys)" : m.keys == "root" ? "found in the root (the recompile moves it)" : "MISSING: put it in data/ or in the root",
         m.keys == "none" ? kRed : kGreen);
    line("Dump", m.dumps.empty() ? "none in the root (.xci Japanese, .nsp Global)" : m.dumps[0] + (m.dumps.size() > 1 ? " (+" + std::to_string(m.dumps.size() - 1) + ")" : ""),
         m.dumps.empty() ? kOrange : kGreen);
    line("Tools", m.tools ? "installed" : "not installed: use the Install tab", m.tools ? kGreen : kOrange);
    line("Vulkan", m.vulkan == "yes" ? "loader and driver found" : m.vulkan == "no" ? "no loader or no driver: the game cannot start (Install asks to install it)" : "unknown",
         m.vulkan == "yes" ? kGreen : m.vulkan == "no" ? kRed : kGrey);
    line(kEdName[g_ui.edition], e.text, e.state == "ready" || e.state == "running" ? kGreen : kOrange);
    ImGui::Spacing();
    ImGui::Separator();
    ImGui::Spacing();
    ImGui::TextUnformatted("GPU hang limit (ms), empty = default. In a virtual machine without a GPU try 120000:");
    ImGui::SetNextItemWidth(140);
    ImGui::InputText("##gh", g_ui.gpu_hang, sizeof g_ui.gpu_hang, ImGuiInputTextFlags_CharsDecimal);
    ImGui::Spacing();
    bool can = e.state == "ready" && !g_run.running;
    ImGui::BeginDisabled(!can);
    if (ImGui::Button("Play", ImVec2(160, 36))) {
        auto a = with_ed({"play"});
        if (g_ui.gpu_hang[0]) { a.push_back("--gpu-hang"); a.push_back(g_ui.gpu_hang); }
        run_cmd(a, true);
    }
    ImGui::EndDisabled();
    ImGui::SameLine();
    if (ImGui::Button("Show the game log")) run_cmd({"log"});
    ImGui::SameLine();
    if (ImGui::Button("Open data folder")) open_folder(g_root + "/data/" + (g_ui.edition == 0 ? "tsuki" : "tsuki_global"));
}

static void tab_saves(const Model& m) {
    ImGui::Spacing();
    if (m.save_count.empty() || m.save_count == "0") ImGui::TextUnformatted("No saves yet.");
    else ImGui::Text("%s file(s), last change %s", m.save_count.c_str(), m.save_last.c_str());
    ImGui::Spacing();
    ImGui::TextUnformatted("Copies (newest first):");
    if (ImGui::BeginListBox("##copies", ImVec2(-FLT_MIN, 110))) {
        for (int i = 0; i < int(m.copies.size()); i++)
            if (ImGui::Selectable(m.copies[i].c_str(), g_ui.sel_copy == i)) g_ui.sel_copy = i;
        ImGui::EndListBox();
    }
    bool idle = !g_run.running;
    ImGui::BeginDisabled(!idle || m.save_count.empty() || m.save_count == "0");
    if (ImGui::Button("Back up now")) run_cmd(with_ed({"saves", "backup"}));
    ImGui::EndDisabled();
    ImGui::SameLine();
    ImGui::BeginDisabled(!idle || g_ui.sel_copy < 0 || g_ui.sel_copy >= int(m.copies.size()));
    if (ImGui::Button("Restore the selected copy...") && g_ui.sel_copy >= 0 && g_ui.sel_copy < int(m.copies.size()))
        ask("Restore saves", "The current saves are copied first (to <date>_before_restore) and then replaced by:\n" + m.copies[g_ui.sel_copy],
            with_ed({"saves", "restore", std::to_string(g_ui.sel_copy + 1)}));
    ImGui::EndDisabled();
    ImGui::SameLine();
    if (ImGui::Button("Open saves folder")) open_folder(g_root + "/data/" + (g_ui.edition == 0 ? "tsuki" : "tsuki_global") + "/user/save");
    ImGui::SameLine();
    if (ImGui::Button("Open copies folder")) open_folder(g_root + "/save_backups/" + kEd[g_ui.edition]);
    ImGui::Spacing();
    ImGui::Separator();
    ImGui::Spacing();
    ImGui::TextUnformatted("Import ONE save file (it replaces the matching one; all the saves are copied first):");
    path_row("imp", g_ui.import_path, sizeof g_ui.import_path, 0, false, "path of the save file");
    ImGui::BeginDisabled(!idle || !g_ui.import_path[0]);
    if (ImGui::Button("Import this file...") && g_ui.import_path[0])
        ask("Import a save file", std::string("The matching save is replaced by:\n") + g_ui.import_path + "\nAll the current saves are copied first (<date>_before_import).",
            with_ed({"saves", "import", g_ui.import_path}), {{"LAUNCHER_ASSUME_YES", "1"}});
    ImGui::EndDisabled();
}

static void tab_install(const Model& m) {
    ImGui::Spacing();
    ImGui::TextWrapped("Install gets everything ready (system packages with sudo, the tools, the libraries) and builds the native game of this edition. "
                       "It asks for your password once, so it opens in a terminal window. Recompile only rebuilds the game (no password) and is what you need after adding a mod or an update.");
    ImGui::Spacing();
    line("Tools", m.tools ? "installed" : "not installed", m.tools ? kGreen : kOrange);
    line("Vulkan", m.vulkan == "yes" ? "loader and driver found" : "missing: the installer shows the command to install it and asks", m.vulkan == "yes" ? kGreen : kOrange);
    line(kEdName[g_ui.edition], m.ed[g_ui.edition].text, m.ed[g_ui.edition].state == "ready" ? kGreen : kOrange);
    ImGui::Spacing();
    ImGui::BeginDisabled(g_run.running);
    if (ImGui::Button("Install and build (in a terminal)", ImVec2(260, 34))) {
        if (!run_in_terminal(with_ed({"install"}))) g_run.append("No terminal program found. Run in a terminal: bash tools/linux/launcher.sh install -e " + std::string(kEd[g_ui.edition]) + "\n");
    }
    ImGui::SameLine();
    ImGui::BeginDisabled(!m.tools);
    if (ImGui::Button("Recompile this edition", ImVec2(220, 34))) run_cmd(with_ed({"recomp"}));
    ImGui::EndDisabled();
    ImGui::EndDisabled();
}

static const ImVec4& level_color(const std::string& l) { return l == "error" ? kRed : l == "warn" ? kOrange : l == "ok" ? kGreen : kGrey; }

static void tab_mods(const Model& m) {
    ImGui::Spacing();
    for (auto& c : m.checks) ImGui::TextColored(level_color(c.level), "%s", c.msg.c_str());
    bool idle = !g_run.running;
    ImGui::BeginDisabled(!idle || !m.tools);
    if (ImGui::Button("Recompile this edition")) run_cmd(with_ed({"recomp"}));
    ImGui::EndDisabled();
    ImGui::Spacing();
    ImGui::Separator();
    ImGui::TextUnformatted("Mods (the game reads them in this order; the last one wins). Untick = disabled:");
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
        if (m.layers.empty()) ImGui::TextDisabled("(no mods in mods/%s)", kEd[g_ui.edition]);
        ImGui::EndListBox();
    }
    path_row("mod", g_ui.mod_path, sizeof g_ui.mod_path, 1, true, "folder of the mod to move into mods/");
    ImGui::BeginDisabled(!idle);
    ImGui::BeginDisabled(!g_ui.mod_path[0]);
    if (ImGui::Button("Add this mod folder")) run_cmd({"mods", "add", g_ui.mod_path, "-e", kEd[g_ui.edition]});
    ImGui::EndDisabled();
    ImGui::SameLine();
    ImGui::BeginDisabled(g_ui.sel_layer < 0 || g_ui.sel_layer >= int(m.layers.size()));
    if (ImGui::Button("Remove the selected mod...") && g_ui.sel_layer >= 0 && g_ui.sel_layer < int(m.layers.size()))
        ask("Remove mod", "Move this layer to the trash?\n" + m.layers[g_ui.sel_layer].name, {"mods", "remove", m.layers[g_ui.sel_layer].name, "-e", kEd[g_ui.edition]});
    ImGui::EndDisabled();
    ImGui::EndDisabled();
    ImGui::SameLine();
    if (ImGui::Button("Open mods folder")) open_folder(g_root + "/mods/" + kEd[g_ui.edition]);
    ImGui::Spacing();
    ImGui::Separator();
    ImGui::TextUnformatted("Updates and patches (.nsp in updates/; the recompile of the right edition processes them):");
    if (ImGui::BeginListBox("##nsps", ImVec2(-FLT_MIN, 80))) {
        for (int i = 0; i < int(m.nsps.size()); i++) {
            const Nsp& n = m.nsps[i];
            char sz[32];
            snprintf(sz, sizeof sz, "%.1f MB", double(n.size) / 1048576.0);
            if (ImGui::Selectable((n.name + "   [" + n.state + ", " + sz + "]").c_str(), g_ui.sel_nsp == i)) g_ui.sel_nsp = i;
        }
        if (m.nsps.empty()) ImGui::TextDisabled("(no .nsp in updates/)");
        ImGui::EndListBox();
    }
    path_row("nsp", g_ui.nsp_path, sizeof g_ui.nsp_path, 2, false, "the .nsp file to move into updates/");
    ImGui::BeginDisabled(!idle);
    ImGui::BeginDisabled(!g_ui.nsp_path[0]);
    if (ImGui::Button("Add this .nsp")) run_cmd({"updates", "add", g_ui.nsp_path});
    ImGui::EndDisabled();
    ImGui::SameLine();
    ImGui::BeginDisabled(g_ui.sel_nsp < 0 || g_ui.sel_nsp >= int(m.nsps.size()));
    if (ImGui::Button("Remove the selected .nsp...") && g_ui.sel_nsp >= 0 && g_ui.sel_nsp < int(m.nsps.size()))
        ask("Remove update", "Move this file to the trash?\n" + m.nsps[g_ui.sel_nsp].name, {"updates", "remove", m.nsps[g_ui.sel_nsp].name});
    ImGui::EndDisabled();
    ImGui::EndDisabled();
    ImGui::SameLine();
    if (ImGui::Button("Open updates folder")) open_folder(g_root + "/updates");
}

static void draw(Model& m, bool& want_refresh) {
    ImGuiIO& io = ImGui::GetIO();
    ImGui::SetNextWindowPos(ImVec2(0, 0));
    ImGui::SetNextWindowSize(io.DisplaySize);
    ImGui::Begin("##main", nullptr, ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoSavedSettings);
    ImGui::TextUnformatted("Edition:");
    for (int i = 0; i < 2; i++) {
        ImGui::SameLine();
        if (ImGui::RadioButton(kEdName[i], g_ui.edition == i)) { g_ui.edition = i; g_ui.sel_copy = g_ui.sel_layer = g_ui.sel_nsp = -1; want_refresh = true; }
    }
    ImGui::SameLine();
    if (ImGui::Button("Refresh")) want_refresh = true;
    ImGui::SameLine();
    ImGui::TextDisabled("  %s", g_root.c_str());

    float log_h = io.DisplaySize.y * 0.22f;
    if (ImGui::BeginTabBar("##tabs")) {
        const char* names[4] = {"Play", "Saves", "Install", "Mods"};
        for (int t = 0; t < 4; t++) {
            ImGuiTabItemFlags fl = g_ui.force_tab == t ? ImGuiTabItemFlags_SetSelected : 0;
            if (ImGui::BeginTabItem(names[t], nullptr, fl)) {
                ImGui::BeginChild("##body", ImVec2(0, -log_h - 6), ImGuiChildFlags_None);
                if (t == 0) tab_play(m); else if (t == 1) tab_saves(m); else if (t == 2) tab_install(m); else tab_mods(m);
                ImGui::EndChild();
                ImGui::EndTabItem();
            }
        }
        ImGui::EndTabBar();
    }
    ImGui::Separator();
    if (ImGui::Button("Clear")) g_run.clear();
    ImGui::SameLine();
    ImGui::BeginDisabled(!g_run.running);
    if (ImGui::Button("Stop")) g_run.stop();
    ImGui::EndDisabled();
    ImGui::SameLine();
    ImGui::TextDisabled(g_run.running ? "running..." : "idle");
    ImGui::BeginChild("##log", ImVec2(0, 0), ImGuiChildFlags_Borders);
    static size_t last_len = 0;
    std::string txt = g_run.text();
    ImGui::TextUnformatted(txt.c_str());
    if (txt.size() != last_len) { ImGui::SetScrollHereY(1.0f); last_len = txt.size(); }
    ImGui::EndChild();

    if (g_ui.popup_open) { ImGui::OpenPopup("Confirm"); g_ui.popup_open = false; }
    if (ImGui::BeginPopupModal("Confirm", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
        ImGui::TextUnformatted(g_ui.popup_title.c_str());
        ImGui::Separator();
        ImGui::TextWrapped("%s", g_ui.popup_text.c_str());
        ImGui::Spacing();
        if (ImGui::Button("Yes", ImVec2(120, 0))) { run_cmd(g_ui.popup_cmd, false, g_ui.popup_env); ImGui::CloseCurrentPopup(); }
        ImGui::SameLine();
        if (ImGui::Button("Cancel", ImVec2(120, 0))) ImGui::CloseCurrentPopup();
        ImGui::EndPopup();
    }
    ImGui::End();
}

static void apply_style(float scale) {
    ImGui::StyleColorsDark();
    ImGuiStyle& st = ImGui::GetStyle();
    st.WindowRounding = 0; st.FrameRounding = 4; st.TabRounding = 4; st.ChildRounding = 4;
    st.FramePadding = ImVec2(8, 5); st.ItemSpacing = ImVec2(8, 7);
    st.ScaleAllSizes(scale);
    ImGuiIO& io = ImGui::GetIO();
    const char* fonts[] = {"/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
                           "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf", "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
                           "/usr/share/fonts/noto/NotoSans-Regular.ttf", "/usr/share/fonts/google-noto/NotoSans-Regular.ttf"};
    bool loaded = false;
    for (const char* f : fonts)
        if (file_exists(f) && io.Fonts->AddFontFromFileTTF(f, 16.0f)) { loaded = true; break; }
    if (!loaded) io.Fonts->AddFontDefault();
    st.FontScaleMain = scale;
}

int main(int argc, char** argv) {
    std::string root_arg, shot_dir, run_arg;
    for (int i = 1; i < argc; i++) {
        if (!std::strcmp(argv[i], "--root") && i + 1 < argc) root_arg = argv[++i];
        else if (!std::strcmp(argv[i], "--shot") && i + 1 < argc) shot_dir = argv[++i];
        else if (!std::strcmp(argv[i], "--run") && i + 1 < argc) run_arg = argv[++i];
        else if (!std::strcmp(argv[i], "--help")) { std::puts("tsukihime_launcher [--root <folder of the port>]"); return 0; }
    }
    g_root = find_root(root_arg);
    if (g_root.empty()) {
        std::fprintf(stderr, "tsukihime_launcher: cannot find tools/linux/launcher.sh (run it from the port folder or give --root <folder>)\n");
        return 1;
    }
    g_script = g_root + "/tools/linux/launcher.sh";

    SDL_SetHint(SDL_HINT_APP_ID, "tsukihime_launcher");
    if (!SDL_Init(SDL_INIT_VIDEO)) {
        std::fprintf(stderr, "tsukihime_launcher: SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    SDL_Window* win = SDL_CreateWindow("Tsukihime PC port", 980, 800, SDL_WINDOW_RESIZABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY);
    if (!win) { std::fprintf(stderr, "tsukihime_launcher: window: %s\n", SDL_GetError()); return 1; }
    g_ui.window = win;
    SDL_SetWindowMinimumSize(win, 760, 560);
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
    const char* tab_names[4] = {"play", "saves", "install", "mods"};
    if (!shot_dir.empty()) g_ui.force_tab = 0;
    bool run_started = false;

    while (!done) {
        SDL_Event ev;
        if (SDL_WaitEventTimeout(&ev, 40)) {
            do {
                ImGui_ImplSDL3_ProcessEvent(&ev);
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
        if (g_run.generation != last_gen) { last_gen = g_run.generation; want_refresh = true; }
        if (!g_run.running && now - last_refresh > 5000) want_refresh = true;
        if (want_refresh) { refresh(model, g_ui.edition); want_refresh = false; last_refresh = SDL_GetTicks(); }

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
        if (!shot_dir.empty() && ++shot_wait >= 4) {   // a few frames per tab so the layout settles, then the picture
            SDL_Surface* s = SDL_RenderReadPixels(ren, nullptr);
            if (s) {
                SDL_SaveBMP(s, (shot_dir + "/" + tab_names[shot_step] + ".bmp").c_str());
                SDL_DestroySurface(s);
            }
            shot_wait = 0;
            if (++shot_step >= 4) done = true; else g_ui.force_tab = shot_step;
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
