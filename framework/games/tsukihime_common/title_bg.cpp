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
// LIVE SWITCH (AP_TITLE_LIVE, AP_TITLE_LIVE_MUSIC, AP_TITLE_LIVE_OPUS, AP_TITLE_FADE, AP_TITLE_SYNC; apply_title_live*.py, apply_title_fade.py, apply_title_sync.py). On the title screen F6 shows the next background video and F5 the previous
// one (the order of "pool"; hotkeys = off in title.ini turns it off). The chosen video is read in the background and the
// Extractor of the video that is playing swaps its file the next time the game seeks it (the loop), so the change shows when
// the video loops, not at the key press. The choice also holds for the next visits to the title screen (until the exe is
// closed) and the music follows it THEN: a track that is already playing cannot be changed.
//
// COMING BACK to the title from the story: the game opens the music before the video, so no video can tell us yet. What it
// does open just before (trace of 2026-11-01) is the title UI archive rom:/w_allui.mrg; the story never opens it (its music
// comes after w_allpac.mrg). So opening w_allui.mrg while no visit is open ARMS a visit for kArmMs: a title track opened
// in that window opens the visit. If no track comes, the arm lapses and nothing changes.
#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <random>
#include <string>
#include <thread>
#include <unordered_set>
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
bool music_packet(const uint8_t* in, uint32_t len, std::vector<uint8_t>* out);  // hle_codec.cpp: AP_TITLE_LIVE_OPUS
std::string music_swap(const std::string& path, int64_t off);  // hle_fs.cpp: AP_TITLE_LIVE_MUSIC, the track to start over with
void hotkey(int dir);                                  // platform_win.cpp: F6 (+1) / F5 (-1) on the title screen
bool take_swap(const void* key, std::string* rel, std::string* data);  // hle_movie.cpp: the video to swap in (the fade is at black)
float video_gain();                                    // hle_movie.cpp, every decoded frame: 1 = as it is, 0 = black (AP_TITLE_FADE)
bool music_packet(const uint8_t* in, uint32_t len, std::vector<uint8_t>* out, float* gain);  // hle_codec.cpp: AP_TITLE_FADE
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
    bool hotkeys = true;  // F5 / F6 on the title screen
    bool unlock = true;       // only the videos (and music) the save has unlocked: the game's own pick is the highest
    int swap_delay_ms = 450;  // from the swap until the first frames of the new video are on screen (fade in waits)
    int fade_ms = 500;    // F5 / F6: fade out / in of the video and of the music that changes (0 = no fade)
    double loop02 = 0;    // seconds into MOON01_BGM02.opus where its loop starts (F5 / F6 music); 0 = the start
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
            } else if (key == "unlock") {
                const std::string u = lower(val);
                if (u == "on" || u == "1" || u == "yes" || u == "progress") c.unlock = true;
                else if (u == "off" || u == "0" || u == "no" || u == "all") c.unlock = false;
                else nx::log("title: %s:%d: unlock = \"%s\" is not valid (on, off)", file.c_str(), line, val.c_str());
            } else if (key == "swap_delay") {
                char* end = nullptr;
                const long v = std::strtol(val.c_str(), &end, 10);
                if (end == val.c_str() || v < 0 || v > 5000) nx::log("title: %s:%d: swap_delay = \"%s\" is not valid (milliseconds, 0 to 5000)", file.c_str(), line, val.c_str());
                else c.swap_delay_ms = int(v);
            } else if (key == "fade") {
                char* end = nullptr;
                const long v = std::strtol(val.c_str(), &end, 10);
                if (end == val.c_str() || v < 0 || v > 5000) nx::log("title: %s:%d: fade = \"%s\" is not valid (milliseconds, 0 to 5000)", file.c_str(), line, val.c_str());
                else c.fade_ms = int(v);
            } else if (key == "loop_bgm02") {
                char* end = nullptr;
                const double v = std::strtod(val.c_str(), &end);
                if (end == val.c_str() || v < 0) nx::log("title: %s:%d: loop_bgm02 = \"%s\" is not valid (seconds)", file.c_str(), line, val.c_str());
                else c.loop02 = v;
            } else if (key == "hotkeys") {
                const std::string h = lower(val);
                if (h == "on" || h == "1" || h == "yes") c.hotkeys = true;
                else if (h == "off" || h == "0" || h == "no") c.hotkeys = false;
                else nx::log("title: %s:%d: hotkeys = \"%s\" is not valid (on, off)", file.c_str(), line, val.c_str());
            } else if (key == "music") {
                if (!parse_music(val, c)) nx::log("title: %s:%d: music = \"%s\" is not valid (follow, game, 1, 2, 3, 4)", file.c_str(), line, val.c_str());
            } else {
                nx::log("title: %s:%d: unknown key \"%s\" (valid: video, pool, music, hotkeys, unlock, fade, swap_delay, loop_bgm02)", file.c_str(), line, key.c_str());
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
    int override_video = 0;          // F5 / F6: the video the player chose (0 = none); beats title.ini until the exe closes
    int shown = 0;                   // the titlebg number that is playing
    std::string shown_prefix, shown_suffix;
    bool swap_pending = false, swap_ready = false;  // a video chosen with F5/F6 waiting for the loop / already read
    std::string swap_rel, swap_data;
    int swap_n = 0;
    // Live music (AP_TITLE_LIVE_OPUS): the packets of both tracks, to play the chosen one in place of the guest's.
    struct Track {
        std::string data;
        std::vector<uint32_t> offs;           // start of each packet (size BE u32, final range BE u32, data)
        std::unordered_set<uint64_t> keys;    // hash of each whole packet: to know which track the guest plays
    } tracks[2];                              // 0 = MOON01_BGM02, 1 = MOON01_BGM59
    bool tracks_loading = false, tracks_ready = false;
    bool sub_active = false;
    int sub_target = -1;
    size_t sub_idx = 0;
    // Fade (AP_TITLE_FADE): out -> black (the video is swapped and the music changes) -> in
    enum Fade { kIdle, kOut, kHold, kIn } fade = kIdle;
    std::chrono::steady_clock::time_point fade_t0{}, swapped_at{};
    float fade_from = 1.0f;
    bool fade_music = false;         // the music changes with this F5 / F6: it fades too
    int unlocked = 0;                // highest titlebg number the game itself asked for this session (its pick follows the save)
    int music_cap = 1;               // before any video was asked for: what the track the game opened allows (A: 3, B: 4)
    bool music_restart = false;      // the swap changed the music: the chosen track starts from its beginning
    bool swapped = false;            // the video was swapped: the fade in waits a moment for the first frames
    int next_video = 0;              // chosen with F5 / F6 and not shown yet
    int seeks = 0;                   // SeekTo calls on the title video (log only)
    int pick = 0;                    // random / cycle: the video number of this visit, 0 = not picked yet
    int last_pick = 0;
    size_t cycle_pos = 0;
    std::mt19937 rng{std::random_device{}()};
};
State& state() {
    static State* s = new State;  // never freed
    return *s;
}

// The gain (1 = normal, 0 = black / silent) of the fade at this moment; moves the phases on. st.m held.
float fade_gain_locked(State& st, const Config& c) {
    if (st.fade == State::kIdle) return 1.0f;
    const auto now = std::chrono::steady_clock::now();
    const float d = float(std::max(1, c.fade_ms));
    auto ms = [&](std::chrono::steady_clock::time_point t) {
        return float(std::chrono::duration_cast<std::chrono::microseconds>(now - t).count()) / 1000.0f;
    };
    if (st.fade == State::kOut) {
        const float t = ms(st.fade_t0) / d;
        if (t < 1.0f) return st.fade_from * (1.0f - t);
        st.fade = State::kHold;
    }
    if (st.fade == State::kHold) {
        if (!st.swapped || ms(st.swapped_at) < float(c.swap_delay_ms)) return 0.0f;
        st.fade = State::kIn, st.fade_t0 = now;
        return 0.0f;
    }
    const float t = ms(st.fade_t0) / d;  // kIn
    if (t >= 1.0f) {
        st.fade = State::kIdle;
        st.fade_music = false;
        return 1.0f;
    }
    return t;
}

// The highest video (and so the music) the save allows: the game picks the title video from the progress, so the highest
// number it has asked for is what is unlocked (1 on a new save). st.m held.
int cap_locked(const State& st, const Config& c) {
    if (!c.unlock) return 99;
    return std::max(1, st.unlocked ? st.unlocked : st.music_cap);
}
// The pool of title.ini limited to what is unlocked (never empty). st.m held.
std::vector<int> allowed_pool_locked(const State& st, const Config& c) {
    std::vector<int> v;
    const int cap = cap_locked(st, c);
    for (int n : c.pool)
        if (n <= cap) v.push_back(n);
    if (v.empty()) v.push_back(1);
    return v;
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
    const std::vector<int> pool = allowed_pool_locked(st, c);
    if (st.pick && std::find(pool.begin(), pool.end(), st.pick) != pool.end()) return st.pick;
    if (st.pick && c.mode == Mode::Cycle && st.cycle_pos) st.cycle_pos--;  // the pick is redone (a video was locked): same turn
    int pick;
    if (c.mode == Mode::Cycle) {
        pick = pool[st.cycle_pos % pool.size()];
        st.cycle_pos++;
    } else {
        std::vector<int> options;
        for (int n : pool)
            if (n != st.last_pick || pool.size() == 1) options.push_back(n);
        pick = options[std::uniform_int_distribution<size_t>(0, options.size() - 1)(st.rng)];
    }
    st.pick = st.last_pick = pick;
    return pick;
}

int override_number() {
    State& st = state();
    std::lock_guard<std::mutex> lk(st.m);
    return st.override_video;
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

// n if the save has it unlocked, else 0 (the game's own choice).
int unlocked_or_zero(const Config& c, int n, const char* what) {
    State& st = state();
    std::lock_guard<std::mutex> lk(st.m);
    if (n <= cap_locked(st, c)) return n;
    static int logged = 0;
    if (logged++ < 5) nx::log("title: %s %d is not unlocked in this save (up to %d): the game's own stays", what, n, cap_locked(st, c));
    return 0;
}

// The video number to show; 0 = the game's choice.
int video_number(const Config& c) {
    switch (c.mode) {
    case Mode::Fixed: return unlocked_or_zero(c, c.fixed, "video");
    case Mode::Random:
    case Mode::Cycle: return visit_pick(c);
    default: return 0;
    }
}

// The video number whose music to play; 0 = the game's choice. Only inside a visit (see SCOPE).
int music_number(const Config& c) {
    if (c.music == MusicMode::Game) return 0;
    if (!visit_is_open()) return -1;  // outside the title screen: untouched (-1 only so the caller can log it)
    if (c.music == MusicMode::Fixed) return unlocked_or_zero(c, c.music_n, "music of video");
    if (const int ov = override_number()) return ov;  // F5 / F6
    switch (c.mode) {
    case Mode::Fixed: return unlocked_or_zero(c, c.fixed, "video");
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
    {  // the game picks the video from the progress of the save: that is what is unlocked
        State& st = state();
        std::lock_guard<std::mutex> lk(st.m);
        if (n.number > st.unlocked) {
            st.unlocked = n.number;
            if (c.unlock) nx::log("title: this save has titlebg1..%d unlocked", n.number);
        }
    }
    const int ov = override_number();  // F5 / F6
    if (c.mode == Mode::Auto && !ov) return {};
    const int pick = ov ? ov : video_number(c);
    if (pick == 0 || pick == n.number) return {};
    return n.prefix + std::to_string(pick) + n.suffix;
}

// The file to open instead of `path` (a guest path, "rom:/stream/MOON01_BGM02.opus"), or "" to keep `path`.
std::string remap_stream(const std::string& path) {
    const size_t slash = path.find_last_of("/\\");
    const std::string name = path.substr(slash == std::string::npos ? 0 : slash + 1);
    const std::string low = lower(name);
    if (low == "w_allui.mrg") return arm_visit(), std::string();  // the title UI: see COMING BACK
    if (low.size() > 5 && low.compare(low.size() - 5, 5, ".opus") == 0 && lower(path).find("stream/") != std::string::npos) {
        // A new music stream starts: the one that was being replaced is over (the story, the title again...).
        State& st = state();
        std::lock_guard<std::mutex> lk(st.m);
        st.sub_active = false;
        st.music_restart = false;
    }
    if (low != lower(kMusicA) && low != lower(kMusicB)) return {};
    const Config& c = config();
    {  // the track the game opens says how far the save has got, before it asks for the video
        State& st = state();
        std::lock_guard<std::mutex> lk(st.m);
        if (!st.unlocked) st.music_cap = low == lower(kMusicB) ? 4 : 3;
    }
    const int n = music_number(c);
    if (n == -1) {
        // Would it have changed? Only then it is worth a line in the log (return trips to the title, the story).
        const bool would = c.music == MusicMode::Fixed || c.mode != Mode::Auto || override_number();
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

// AP_TITLE_LIVE_MUSIC (apply_title_live_music.py): the game started a title track over (read at offset 0). If the player chose
// with F5 / F6 a video whose track is the other one, the track to continue with ("" = the same one).
std::string music_swap(const std::string& path, int64_t off) {
    if (off != 0) return {};
    const size_t slash = path.find_last_of("/\\");
    const std::string low = lower(path.substr(slash == std::string::npos ? 0 : slash + 1));
    if (low != lower(kMusicA) && low != lower(kMusicB)) return {};
    const Config& c = config();
    if (!c.hotkeys || c.music != MusicMode::Follow) return {};
    State& st = state();
    int ov;
    {
        std::lock_guard<std::mutex> lk(st.m);
        if (!st.visit_open || !st.override_video) return {};  // not on the title screen (the story is never touched)
        ov = st.override_video;
    }
    const char* want = music_file_for_video(ov);
    if (low == lower(want)) return {};
    return (slash == std::string::npos ? std::string() : path.substr(0, slash + 1)) + want;
}

void video_started(const void* key, const char* rel) {
    TitleName n;
    if (!rel || !parse_name(rel, &n)) return;
    State& st = state();
    std::lock_guard<std::mutex> lk(st.m);
    st.active_key = key;
    st.visit_open = true;
    st.shown = n.number, st.shown_prefix = n.prefix, st.shown_suffix = n.suffix;
}

void video_ended(const void* key) {
    State& st = state();
    std::lock_guard<std::mutex> lk(st.m);
    if (key && key == st.active_key) {
        st.active_key = nullptr;
        st.visit_open = false;
        st.pick = 0;
        st.swap_pending = st.swap_ready = false, st.swap_data.clear();
        st.fade = State::kIdle, st.fade_music = false, st.swapped = false, st.next_video = 0, st.music_restart = false;
    }
}

// Reads a title track and splits it into packets. The file is the Switch Opus of the game: 0x20 bytes of header, a
// 0x80000004 chunk header, then [u32 BE size][u32 BE final range][opus packet] one after another.
// FNV-1a of a whole framed packet (size, final range, data): size and range alone repeat between the two tracks.
uint64_t packet_hash(const uint8_t* p, size_t n) {
    uint64_t h = 1469598103934665603ull;
    for (size_t i = 0; i < n; i++) h = (h ^ p[i]) * 1099511628211ull;
    return h;
}

bool load_track(const char* name, State::Track* t) {
    t->data = nx::read_game_file(std::string("stream/") + name);
    const std::string& d = t->data;
    if (d.size() < 0x30) return false;
    uint32_t magic;
    std::memcpy(&magic, d.data(), 4);
    if (magic != 0x80000001u) return false;
    size_t off = 0x28;
    auto be = [&](size_t o) {
        return (uint32_t(uint8_t(d[o])) << 24) | (uint32_t(uint8_t(d[o + 1])) << 16) | (uint32_t(uint8_t(d[o + 2])) << 8) | uint8_t(d[o + 3]);
    };
    while (off + 8 <= d.size()) {
        const uint32_t sz = be(off);
        if (sz == 0 || off + 8 + sz > d.size()) break;
        t->offs.push_back(uint32_t(off));
        t->keys.insert(packet_hash(reinterpret_cast<const uint8_t*>(d.data()) + off, 8 + size_t(sz)));
        off += 8 + sz;
    }
    return t->offs.size() > 100;
}

// Where the loop of a track starts, as a packet index. BGM59: packet 892 (byte 222388, the jump the game makes at the end).
size_t loop_index(int track, const Config& c) {
    if (track == 1) return 892;
    return size_t(c.loop02 * 50.0);  // 20 ms per packet
}

// F6 (dir = +1) / F5 (-1) on the title screen: the next / previous video of the pool, with a fade. Runs in the window thread.
void hotkey(int dir) {
    const Config& c = config();
    if (!c.hotkeys) return;
    State& st = state();
    std::string rel;
    int next = 0;
    {
        std::lock_guard<std::mutex> lk(st.m);
        if (!st.visit_open || !st.active_key || !st.shown) {
            nx::log("title: F5/F6 ignored (the title screen is not up)");
            return;
        }
        const int base = st.next_video ? st.next_video : st.override_video ? st.override_video : st.shown;
        const std::vector<int> pool = allowed_pool_locked(st, c);  // only what the save has unlocked
        size_t idx = 0;
        for (size_t i = 0; i < pool.size(); i++)
            if (pool[i] == base) idx = i;
        next = pool[(idx + pool.size() + (dir < 0 ? pool.size() - 1 : 1)) % pool.size()];
        if (next == base) {
            nx::log("title: F5/F6: titlebg%d is the only video this save has unlocked", base);
            return;
        }
        const int playing = st.override_video ? st.override_video : st.shown;  // the track that is sounding
        st.fade_music = c.music == MusicMode::Follow &&
                        std::strcmp(music_file_for_video(next), music_file_for_video(playing)) != 0;
        st.next_video = next;
        st.swap_pending = true, st.swap_ready = false, st.swap_data.clear();
        st.swapped = false;
        st.swap_n = next;
        st.swap_rel = rel = st.shown_prefix + std::to_string(next) + st.shown_suffix;
        if (c.fade_ms > 0 && (st.fade == State::kIdle || st.fade == State::kIn)) {
            st.fade_from = fade_gain_locked(st, c);
            st.fade = State::kOut;
            st.fade_t0 = std::chrono::steady_clock::now();
        } else if (c.fade_ms <= 0) {
            st.fade = State::kHold;  // no fade: it swaps as soon as the video is read
        }
    }
    nx::log("title: F%d -> titlebg%d (fade %d ms)", dir < 0 ? 5 : 6, next, c.fade_ms);
    {
        std::lock_guard<std::mutex> lk(st.m);
        if (!st.tracks_ready && !st.tracks_loading) {
            st.tracks_loading = true;
            std::thread([] {
                State::Track t[2];
                const bool ok = load_track(kMusicA, &t[0]) && load_track(kMusicB, &t[1]);
                State& s3 = state();
                std::lock_guard<std::mutex> lk3(s3.m);
                s3.tracks_loading = false;
                if (!ok) {
                    nx::log("title: could not read the title tracks: F5/F6 will not change the music");
                    return;
                }
                s3.tracks[0] = std::move(t[0]), s3.tracks[1] = std::move(t[1]);
                s3.tracks_ready = true;
                nx::log("title: the title tracks are loaded (%zu and %zu packets)", s3.tracks[0].offs.size(), s3.tracks[1].offs.size());
            }).detach();
        }
    }
    std::thread([rel] {  // 150 MB: not in the window thread nor in the game's
        std::string d = nx::read_game_file(rel);
        State& s2 = state();
        std::lock_guard<std::mutex> lk(s2.m);
        if (!s2.swap_pending || s2.swap_rel != rel) return;  // another key press came after this one
        if (d.empty()) {
            nx::log("title: %s does not exist: F5/F6 choice dropped", rel.c_str());
            s2.swap_pending = false, s2.next_video = 0, s2.fade_music = false;
            if (s2.fade != State::kIdle) s2.fade = State::kIn, s2.fade_t0 = std::chrono::steady_clock::now(), s2.fade_from = 0;
            return;
        }
        s2.swap_data = std::move(d);
        s2.swap_ready = true;
        nx::log("title: %s read", rel.c_str());
    }).detach();
}

// hle_movie.cpp, from the game's threads (every packet, and at the loop): the video chosen with F5/F6, once it has been read
// and the fade has reached black. Taking it also chooses the music (the one of that video) and starts the fade in.
bool take_swap(const void* key, std::string* rel, std::string* data) {
    State& st = state();
    std::lock_guard<std::mutex> lk(st.m);
    if (!st.swap_pending || !st.swap_ready || !key || key != st.active_key) return false;
    const Config& c = config();
    if (c.fade_ms > 0) {
        fade_gain_locked(st, c);  // moves Out -> Hold when its time is up
        if (st.fade != State::kHold) return false;
    }
    *rel = st.swap_rel;
    *data = std::move(st.swap_data);
    st.swap_data.clear();
    st.swap_pending = st.swap_ready = false;
    st.shown = st.swap_n;
    st.override_video = st.swap_n;  // from now on the music is this video's
    st.music_restart = st.fade_music;
    st.next_video = 0;
    st.swapped = true;
    st.swapped_at = std::chrono::steady_clock::now();
    if (c.fade_ms <= 0) st.fade = State::kIdle;
    return true;
}

// hle_movie.cpp, at every decoded frame of a video.
float video_gain() {
    const Config& c = config();
    State& st = state();
    std::lock_guard<std::mutex> lk(st.m);
    if (st.fade == State::kIdle || !st.active_key) return 1.0f;
    return fade_gain_locked(st, c);
}

// hle_codec.cpp, at every Opus packet the game decodes (`in` = size BE, final range BE, data; `len` = size). While the
// title screen is up and F5 / F6 chose a track other than the one the game is playing, the packet to decode is the next one
// of the chosen track (`*out` = the opus data, no header). The game does not notice: it keeps its own count and reads.
bool music_packet(const uint8_t* in, uint32_t len, std::vector<uint8_t>* out, float* gain) {
    *gain = 1.0f;
    const Config& c = config();
    if (!c.hotkeys || c.music != MusicMode::Follow) return false;
    State& st = state();
    std::lock_guard<std::mutex> lk(st.m);
    // Out of the title screen (the gallery, other menus) the game goes on with the same music stream: while the track
    // we put in its place is playing it stays, until the game starts another stream (remap_stream).
    if (!st.tracks_ready || (!st.visit_open && !st.sub_active)) return false;
    const uint64_t key = packet_hash(in, 8 + size_t(len));
    const bool in0 = st.tracks[0].keys.count(key) != 0, in1 = st.tracks[1].keys.count(key) != 0;
    if (!in0 && !in1) return false;  // not a title track (a voice, a sound effect)
    if (st.fade != State::kIdle && st.fade_music) *gain = fade_gain_locked(st, c);  // the music that changes fades
    if (st.visit_open && !st.override_video) {
        st.sub_active = false;
        return false;
    }
    const int target = st.visit_open ? (st.override_video >= 4 ? 1 : 0) : st.sub_target;
    // The same packet in both tracks (silence) does not say which one the game plays: it only matters when nothing
    // is being substituted yet.
    const int guest = in0 && in1 ? -1 : in1 ? 1 : 0;
    // The game already plays the chosen track and nothing asked for it to start over: its own music goes on.
    if (!st.sub_active && !st.music_restart && (guest == target || guest < 0)) return false;
    const State::Track& t = st.tracks[target];
    // A change of music (the track differs from the one that was sounding) always starts the chosen track from its
    // beginning, also when it is the one the game is in the middle of: the game's own stream is replaced then too.
    if (!st.sub_active || st.sub_target != target || st.music_restart) {
        st.sub_active = true;
        st.sub_target = target;
        st.sub_idx = 0;
        st.music_restart = false;
        nx::log("title: music -> %s, from its start", target ? kMusicB : kMusicA);
    }
    const uint32_t o = t.offs[st.sub_idx];
    const uint32_t sz = (uint32_t(uint8_t(t.data[o])) << 24) | (uint32_t(uint8_t(t.data[o + 1])) << 16) |
                        (uint32_t(uint8_t(t.data[o + 2])) << 8) | uint8_t(t.data[o + 3]);
    out->assign(t.data.begin() + o + 8, t.data.begin() + o + 8 + sz);
    if (++st.sub_idx >= t.offs.size()) st.sub_idx = std::min(loop_index(target, c), t.offs.size() - 1);
    return true;
}

bool music_packet(const uint8_t* in, uint32_t len, std::vector<uint8_t>* out) {  // apply_title_live_opus.py (no fade)
    float g;
    return music_packet(in, len, out, &g);
}

}  // namespace tsuki_title
