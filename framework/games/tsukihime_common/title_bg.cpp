// title_bg.cpp - choose the title screen background video and music with title.ini (AP_TITLE_BG, AP_TITLE_MUSIC).
//
// Goes in games/tsukihime_common/ (picked up by the GLOB of tsukihime_common.cmake). Two entry points:
//   remap(rel)          hle_movie.cpp asks it for every video the game opens (apply_title_bg.py). Only
//                       movie/titlebg<N>[_en|_zc|_zt].mp4 can be redirected, any other video is left alone.
//   remap_stream(path)  hle_fs.cpp asks it for every file the game opens for reading (apply_title_music.py). Only the two
//                       title tracks can be redirected (kMusicA, kMusicB), any other file is left alone.
// The game still decides everything else: this only changes which file it is served.
//
// The game's own logic picks titlebg1..4 from the routes cleared. This file replaces that pick:
//   video = auto    (default) the game's choice, nothing changes
//           1..4    always that video
//           random  a different video from the pool every time the title screen opens (never the same twice in a row)
//           cycle   the pool in order, one per time the title screen opens
// The music of the title screen is a separate track (not in the mp4): videos 1, 2 and 3 use stream/MOON01_BGM02.opus and
// video 4 uses stream/MOON01_BGM59.opus (found in the log with NX_TRACE_FS=1). The music follows the video:
//   music = follow  (default) the music of the video that is shown (nothing changes while video = auto)
//           game    the game's own music, whatever the video is
//           1..4    always the music of that video (works with video = auto too)
//
// title.ini: NX_TITLE_INI if defined; otherwise next to the exe; otherwise in the data root (<root>\data\tsuki).
//   [title]
//   video = auto | 1 | 2 | 3 | 4 | random | cycle
//   pool  = 1, 2, 3, 4            ; only for random and cycle: which videos they use (default 1,2,3,4)
//   music = follow | game | 1 | 2 | 3 | 4
// NX_TITLE_BG=<value> overrides "video" and NX_TITLE_MUSIC=<value> overrides "music". Comments with ; or #. A mistake goes
// to the log with its line and is skipped. If the chosen file does not exist, the one the game asked for is used.
//
// SCOPE (AP_TITLE_SCOPE). The same two tracks are also played by the story, so the music is only redirected during a
// "visit" to the title screen. A visit starts when the program starts (the title is the first screen and the game opens
// its music BEFORE its video: the log of 2026-11-01 shows MOON01_BGM59.opus, then movie/titlebg4.mp4) and when a titlebg
// video opens, and it ends when that video ends (the player leaves the title: hle_movie.cpp tells video_ended). While
// no visit is open every file goes through untouched. With random and cycle the video and the music of a visit share
// one pick, whichever the game opens first.
// COMING BACK to the title from the story: the game opens the music before the video, so no video can tell us yet. What it
// does open just before (trace of 2026-11-01) is the title UI archive rom:/w_allui.mrg; the story never opens it (its music
// comes after w_allpac.mrg). So opening w_allui.mrg while no visit is open ARMS a visit for kArmMs: a title track opened
// in that window opens the visit. If no track comes, the arm lapses and nothing changes.
#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <random>
#include <string>
#include <vector>

#include <nx/runtime.h>

#include "platform.h"

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

namespace tsuki_title {
std::string remap(const std::string& rel);          // hle_movie.cpp calls it
std::string remap_stream(const std::string& path);  // hle_fs.cpp calls it
void video_started(const void* key, const char* rel);  // hle_movie.cpp: a video was opened (key: its Extractor)
void video_ended(const void* key);                     // hle_movie.cpp: that Extractor was destroyed
}

namespace {

constexpr int kArmMs = 3000;  // w_allui.mrg -> the title music must follow within this

// The title tracks (romfs stream/). Videos 1-3 -> A, video 4 -> B.
constexpr const char* kMusicA = "MOON01_BGM02.opus";
constexpr const char* kMusicB = "MOON01_BGM59.opus";
const char* music_file_for_video(int n) { return n >= 4 ? kMusicB : kMusicA; }

enum class Mode { Auto, Fixed, Random, Cycle };
enum class MusicMode { Follow, Game, Fixed };
enum Event { kVideo = 0, kMusic = 1 };

struct Config {
    Mode mode = Mode::Auto;
    int fixed = 0;
    std::vector<int> pool{1, 2, 3, 4};
    MusicMode music = MusicMode::Follow;
    int music_n = 0;
    std::string source;  // where it was read from (for the log)
};

std::string trim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
}
std::string lower(std::string s) {
    for (char& ch : s) ch = char(std::tolower(static_cast<unsigned char>(ch)));
    return s;
}

bool parse_number(const std::string& s, int* out) {
    if (s.empty() || s.size() > 2) return false;
    for (char ch : s)
        if (!std::isdigit(static_cast<unsigned char>(ch))) return false;
    int v = std::atoi(s.c_str());
    if (v < 1) return false;
    *out = v;
    return true;
}

// "auto" | "random" | "cycle" | a number >= 1
bool parse_video(const std::string& raw, Config& c) {
    const std::string v = lower(trim(raw));
    if (v == "auto" || v == "game" || v == "default" || v == "off") return c.mode = Mode::Auto, true;
    if (v == "random") return c.mode = Mode::Random, true;
    if (v == "cycle") return c.mode = Mode::Cycle, true;
    int n = 0;
    if (parse_number(v, &n)) return c.mode = Mode::Fixed, c.fixed = n, true;
    return false;
}

// "follow" | "game" | a number >= 1
bool parse_music(const std::string& raw, Config& c) {
    const std::string v = lower(trim(raw));
    if (v == "follow" || v == "video") return c.music = MusicMode::Follow, true;
    if (v == "game" || v == "auto" || v == "default" || v == "off") return c.music = MusicMode::Game, true;
    int n = 0;
    if (parse_number(v, &n)) return c.music = MusicMode::Fixed, c.music_n = n, true;
    return false;
}

bool parse_pool(const std::string& raw, std::vector<int>* out) {
    std::vector<int> v;
    size_t i = 0;
    while (i <= raw.size()) {
        size_t j = raw.find(',', i);
        if (j == std::string::npos) j = raw.size();
        int n = 0;
        if (!parse_number(trim(raw.substr(i, j - i)), &n)) return false;
        if (std::find(v.begin(), v.end(), n) == v.end()) v.push_back(n);
        i = j + 1;
    }
    if (v.empty()) return false;
    *out = std::move(v);
    return true;
}

std::vector<std::filesystem::path> ini_candidates() {
    std::vector<std::filesystem::path> v;
    if (const char* e = std::getenv("NX_TITLE_INI"); e && *e) {
        v.push_back(std::filesystem::u8path(e));
        return v;
    }
#if defined(_WIN32)
    wchar_t exe[MAX_PATH] = {};
    if (GetModuleFileNameW(nullptr, exe, MAX_PATH)) v.push_back(std::filesystem::path(exe).parent_path() / "title.ini");
#endif
    v.push_back(std::filesystem::u8path(nx::plat::data_root()) / "title.ini");
    return v;
}

Config load_config() {
    Config c;
    std::filesystem::path path;
    std::ifstream in;
    for (const auto& p : ini_candidates()) {
        in.open(p);
        if (in) {
            path = p;
            break;
        }
        in.clear();
    }
    if (!path.empty()) {
        c.source = path.string();
        const std::string file = c.source;
        bool in_title = false;
        std::string raw;
        for (int line = 1; std::getline(in, raw); line++) {
            if (line == 1 && raw.compare(0, 3, "\xEF\xBB\xBF") == 0) raw.erase(0, 3);  // BOM
            if (size_t k = raw.find_first_of(";#"); k != std::string::npos) raw.erase(k);
            const std::string s = trim(raw);
            if (s.empty()) continue;
            if (s.front() == '[') {
                const size_t close = s.find(']');
                if (close == std::string::npos) {
                    nx::log("title: %s:%d: missing ] in the section", file.c_str(), line);
                    continue;
                }
                const std::string name = lower(trim(s.substr(1, close - 1)));
                in_title = name == "title" || name == "titulo";
                if (!in_title) nx::log("title: %s:%d: unknown section [%s] (valid: title); its lines are ignored", file.c_str(), line, name.c_str());
                continue;
            }
            const size_t eq = s.find('=');
            if (eq == std::string::npos) {
                nx::log("title: %s:%d: missing '=' (format: video = auto|1|2|3|4|random|cycle)", file.c_str(), line);
                continue;
            }
            if (!in_title) continue;
            const std::string key = lower(trim(s.substr(0, eq))), val = trim(s.substr(eq + 1));
            if (key == "video") {
                if (!parse_video(val, c)) nx::log("title: %s:%d: video = \"%s\" is not valid (auto, 1, 2, 3, 4, random, cycle)", file.c_str(), line, val.c_str());
            } else if (key == "pool") {
                if (!parse_pool(val, &c.pool)) nx::log("title: %s:%d: pool = \"%s\" is not valid (numbers separated by commas)", file.c_str(), line, val.c_str());
            } else if (key == "music") {
                if (!parse_music(val, c)) nx::log("title: %s:%d: music = \"%s\" is not valid (follow, game, 1, 2, 3, 4)", file.c_str(), line, val.c_str());
            } else {
                nx::log("title: %s:%d: unknown key \"%s\" (valid: video, pool, music)", file.c_str(), line, key.c_str());
            }
        }
    }
    if (const char* e = std::getenv("NX_TITLE_BG"); e && *e) {
        if (parse_video(e, c)) c.source += c.source.empty() ? "NX_TITLE_BG" : " + NX_TITLE_BG";
        else nx::log("title: NX_TITLE_BG=\"%s\" is not valid (auto, 1, 2, 3, 4, random, cycle); ignored", e);
    }
    if (const char* e = std::getenv("NX_TITLE_MUSIC"); e && *e) {
        if (parse_music(e, c)) c.source += c.source.empty() ? "NX_TITLE_MUSIC" : " + NX_TITLE_MUSIC";
        else nx::log("title: NX_TITLE_MUSIC=\"%s\" is not valid (follow, game, 1, 2, 3, 4); ignored", e);
    }
    const char* m = c.mode == Mode::Auto ? "auto" : c.mode == Mode::Fixed ? "fixed" : c.mode == Mode::Random ? "random" : "cycle";
    if (c.mode == Mode::Fixed) nx::log("title: background video fixed to titlebg%d (%s)", c.fixed, c.source.c_str());
    else nx::log("title: background video %s (%s)", m, c.source.empty() ? "no title.ini" : c.source.c_str());
    if (c.music == MusicMode::Fixed) nx::log("title: music fixed to the one of titlebg%d (%s)", c.music_n, music_file_for_video(c.music_n));
    else nx::log("title: music %s", c.music == MusicMode::Game ? "game (not touched)" : "follows the video");
    return c;
}

const Config& config() {
    static const Config c = load_config();
    return c;
}

// movie/titlebg2_en.mp4 -> prefix "movie/titlebg", number 2, suffix "_en.mp4". false if it is not a titlebg video.
struct TitleName {
    std::string prefix, suffix;
    int number = 0;
};
bool parse_name(const std::string& rel, TitleName* out) {
    const size_t slash = rel.find_last_of("/\\");
    const size_t base = slash == std::string::npos ? 0 : slash + 1;
    static const std::string kStem = "titlebg";
    if (lower(rel.substr(base, kStem.size())) != kStem) return false;
    size_t d = base + kStem.size(), e = d;
    while (e < rel.size() && std::isdigit(static_cast<unsigned char>(rel[e]))) e++;
    int n = 0;
    if (e == d || !parse_number(rel.substr(d, e - d), &n)) return false;
    out->prefix = rel.substr(0, d);
    out->suffix = rel.substr(e);
    out->number = n;
    return true;
}

// ---------------------------------------------------------------------------------------------- the visit
// One visit to the title screen: from program start (or the first titlebg video after one ended) to the end of that video.
struct State {
    std::mutex m;
    bool visit_open = true;          // the title is the first screen of the program
    bool armed = false;              // w_allui.mrg was opened while no visit was open
    std::chrono::steady_clock::time_point armed_at{};
    const void* active_key = nullptr;  // Extractor of the titlebg video of this visit
    int pick = 0;                    // random / cycle: the video number of this visit, 0 = not picked yet
    int last_pick = 0;
    size_t cycle_pos = 0;
    std::mt19937 rng{std::random_device{}()};
};
State& state() {
    static State* s = new State;  // never freed
    return *s;
}

// A titlebg video is being opened: if the previous visit had ended, this starts a new one.
void open_visit() {
    State& st = state();
    std::lock_guard<std::mutex> lk(st.m);
    if (!st.visit_open) st.visit_open = true, st.pick = 0;
}

// random / cycle: the video number of this visit (video and music share it). 0 if no visit is open.
int visit_pick(const Config& c) {
    State& st = state();
    std::lock_guard<std::mutex> lk(st.m);
    if (!st.visit_open) return 0;
    if (st.pick) return st.pick;
    int pick;
    if (c.mode == Mode::Cycle) {
        pick = c.pool[st.cycle_pos % c.pool.size()];
        st.cycle_pos++;
    } else {
        std::vector<int> options;
        for (int n : c.pool)
            if (n != st.last_pick || c.pool.size() == 1) options.push_back(n);
        pick = options[std::uniform_int_distribution<size_t>(0, options.size() - 1)(st.rng)];
    }
    st.pick = st.last_pick = pick;
    return pick;
}

bool visit_is_open() {
    State& st = state();
    std::lock_guard<std::mutex> lk(st.m);
    if (!st.visit_open && st.armed) {
        st.armed = false;
        if (std::chrono::steady_clock::now() - st.armed_at < std::chrono::milliseconds(kArmMs)) {
            st.visit_open = true, st.pick = 0;  // the title music after its UI: a visit that has no video yet
            nx::log("title: the title screen is coming back (its UI was just opened)");
        }
    }
    return st.visit_open;
}

void arm_visit() {
    State& st = state();
    std::lock_guard<std::mutex> lk(st.m);
    if (!st.visit_open) st.armed = true, st.armed_at = std::chrono::steady_clock::now();
}

// The video number to show; 0 = the game's choice.
int video_number(const Config& c) {
    switch (c.mode) {
    case Mode::Fixed: return c.fixed;
    case Mode::Random:
    case Mode::Cycle: return visit_pick(c);
    default: return 0;
    }
}

// The video number whose music to play; 0 = the game's choice. Only inside a visit (see SCOPE).
int music_number(const Config& c) {
    if (c.music == MusicMode::Game) return 0;
    if (!visit_is_open()) return -1;  // outside the title screen: untouched (-1 only so the caller can log it)
    if (c.music == MusicMode::Fixed) return c.music_n;
    switch (c.mode) {
    case Mode::Fixed: return c.fixed;
    case Mode::Random:
    case Mode::Cycle: return visit_pick(c);
    default: return 0;
    }
}

}  // namespace

namespace tsuki_title {

// The file to open instead of `rel` (romfs-relative), or "" to keep `rel`.
std::string remap(const std::string& rel) {
    TitleName n;
    if (!parse_name(rel, &n)) return {};
    open_visit();
    const Config& c = config();
    if (c.mode == Mode::Auto) return {};
    const int pick = video_number(c);
    if (pick == 0 || pick == n.number) return {};
    return n.prefix + std::to_string(pick) + n.suffix;
}

// The file to open instead of `path` (a guest path, "rom:/stream/MOON01_BGM02.opus"), or "" to keep `path`.
std::string remap_stream(const std::string& path) {
    const size_t slash = path.find_last_of("/\\");
    const std::string name = path.substr(slash == std::string::npos ? 0 : slash + 1);
    const std::string low = lower(name);
    if (low == "w_allui.mrg") return arm_visit(), std::string();  // the title UI: see COMING BACK
    if (low != lower(kMusicA) && low != lower(kMusicB)) return {};
    const Config& c = config();
    const int n = music_number(c);
    if (n == -1) {
        // Would it have changed? Only then it is worth a line in the log (return trips to the title, the story).
        const bool would = c.music == MusicMode::Fixed || c.mode != Mode::Auto;
        if (would) nx::log("title: %s opened outside the title screen: left as the game wants", name.c_str());
        return {};
    }
    if (n == 0) return {};
    const char* want = music_file_for_video(n);
    if (low == lower(want)) return {};
    std::string alt = (slash == std::string::npos ? std::string() : path.substr(0, slash + 1)) + want;
    nx::log("title: music %s -> %s", name.c_str(), want);
    return alt;
}

void video_started(const void* key, const char* rel) {
    TitleName n;
    if (!rel || !parse_name(rel, &n)) return;
    State& st = state();
    std::lock_guard<std::mutex> lk(st.m);
    st.active_key = key;
    st.visit_open = true;
}

void video_ended(const void* key) {
    State& st = state();
    std::lock_guard<std::mutex> lk(st.m);
    if (key && key == st.active_key) {
        st.active_key = nullptr;
        st.visit_open = false;
        st.pick = 0;
    }
}

}  // namespace tsuki_title
