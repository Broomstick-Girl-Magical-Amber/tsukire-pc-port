// Linux host (x86-64): memory (mmap at fixed addresses), pthreads, an SDL3 window with an SDL-made Vulkan surface
// (X11 or Wayland, whichever SDL picked), SDL3 gamepad + keyboard, SDL3 audio, a signal crash handler and main.
//
// Same shape as platform_mac.cpp: main() starts the game (nx::boot) on its own thread and then runs the SDL event
// loop. Anything that touches the window (creating it, its Vulkan surface) is done on the main thread; the game's
// threads ask for it and wait. Input is sampled on the main thread into a snapshot read_pad() copies.
//
// Data: NX_DATA_ROOT or games/<game>'s pc_data_root ("private/<game>"): analysis/{main,sdk}.bin, extract/romfs, user/.
// Settings: NX_<KEY> environment variables, as on Windows and macOS.
#include <dlfcn.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <ucontext.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cerrno>
#include <climits>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <filesystem>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <SDL3/SDL.h>
#include <SDL3/SDL_vulkan.h>

#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>

#include <nx/game.h>
#include <nx/runtime.h>

#include "kernel.h"
#include "platform.h"

namespace nx {
void boot();
#ifdef NX_CUSTOM_WINDOW_TITLE  // AP_LINUX_PLATFORM: the game's window title (games/<game>/game.cpp), as on Windows
namespace game {
extern const wchar_t* const kWindowTitle;
}
#endif
}
#ifdef NX_TITLE_LIVE  // AP_LINUX_PLATFORM: F5 / F6 change the title video (title_bg.cpp), as on Windows
namespace tsuki_title {
void hotkey(int dir);
}
#endif
#ifdef NX_LINUX_WINDOW_ICON  // AP_LINUX_PLATFORM: the game's .ico (CMakeLists.txt) and the PNG decoder of hle_carga.cpp
extern "C" const unsigned char nx_window_icon[];
extern "C" const size_t nx_window_icon_size;
extern "C" __attribute__((weak)) int tsuki_decode_image(const uint8_t* data, size_t size, uint32_t max_side,
                                                        uint32_t* width, uint32_t* height, uint8_t** rgba);
#endif

namespace nx::plat {

namespace {

pthread_t g_main_thread;

// Work for the main thread (window creation, surfaces), run between event polls.
std::mutex g_main_lock;
std::deque<std::function<void()>> g_main_work;

void run_on_main(std::function<void()> fn) {
    if (pthread_equal(pthread_self(), g_main_thread)) {
        fn();
        return;
    }
    std::mutex m;
    std::condition_variable cv;
    bool done = false;
    {
        std::lock_guard lk(g_main_lock);
        g_main_work.push_back([&] {
            fn();
            std::lock_guard l2(m);
            done = true;
            cv.notify_one();
        });
    }
    std::unique_lock lk(m);
    cv.wait(lk, [&] { return done; });
}

SDL_Window* g_window = nullptr;
std::atomic<bool> g_minimized{false};
std::atomic<uint32_t> g_pixel_w{0}, g_pixel_h{0};

std::mutex g_pad_lock;
PadState g_pad;

}  // namespace

// ---- memory ---------------------------------------------------------------------------------------------------

bool reserve(uint64_t addr, uint64_t size) {
    void* p = mmap(reinterpret_cast<void*>(addr), size, PROT_NONE,
                   MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE | MAP_FIXED_NOREPLACE, -1, 0);
    if (p == MAP_FAILED) return false;
    if (uint64_t(p) != addr) {  // kernels before 4.17 take MAP_FIXED_NOREPLACE as a hint
        munmap(p, size);
        return false;
    }
    return true;
}

bool commit(uint64_t addr, uint64_t size) {
    uint64_t page = uint64_t(getpagesize()) - 1;
    uint64_t a = addr & ~page, e = (addr + size + page) & ~page;
    return mprotect(reinterpret_cast<void*>(a), e - a, PROT_READ | PROT_WRITE) == 0;
}

// As on macOS: only whole host pages are released (MADV_DONTNEED gives zero pages back on the next touch); the freed
// part of a page shared with live data is zeroed instead. With 4 KiB host pages that is the guest's own granule.
void decommit(uint64_t addr, uint64_t size) {
    uint64_t page = uint64_t(getpagesize());
    uint64_t lo = (addr + page - 1) & ~(page - 1), hi = (addr + size) & ~(page - 1);
    if (lo >= hi) {
        std::memset(reinterpret_cast<void*>(addr), 0, size);
        return;
    }
    if (addr < lo) std::memset(reinterpret_cast<void*>(addr), 0, lo - addr);
    if (hi < addr + size) std::memset(reinterpret_cast<void*>(hi), 0, addr + size - hi);
    madvise(reinterpret_cast<void*>(lo), hi - lo, MADV_DONTNEED);
    mprotect(reinterpret_cast<void*>(lo), hi - lo, PROT_NONE);
}

// ---- threads and time -----------------------------------------------------------------------------------------

struct ThreadStart {
    ThreadFn fn;
    void* arg;
};

static void* thread_entry(void* p) {
    ThreadStart s = *static_cast<ThreadStart*>(p);
    delete static_cast<ThreadStart*>(p);
    s.fn(s.arg);
    return nullptr;
}

void start_thread(ThreadFn fn, void* arg, size_t stack_size) {
    pthread_attr_t a;
    pthread_attr_init(&a);
    size_t page = size_t(getpagesize()) - 1;
    pthread_attr_setstacksize(&a, std::max<size_t>((stack_size + page) & ~page, PTHREAD_STACK_MIN));
    pthread_attr_setdetachstate(&a, PTHREAD_CREATE_DETACHED);
    pthread_t t;
    pthread_create(&t, &a, thread_entry, new ThreadStart{fn, arg});
    pthread_attr_destroy(&a);
}

void exit_thread() { pthread_exit(nullptr); }
void exit_process(int code) {
    std::fflush(nullptr);
    _exit(code);
}

void sleep_ns(int64_t ns) {
    timespec ts{time_t(ns / 1000000000), long(ns % 1000000000)};
    while (clock_nanosleep(CLOCK_MONOTONIC, 0, &ts, &ts) == EINTR) {
    }
}

void yield() { sched_yield(); }

void set_thread_name(const char* name) {
    char n[16];  // the kernel keeps 15 characters
    std::snprintf(n, sizeof n, "%s", name);
    pthread_setname_np(pthread_self(), n);
}

int64_t current_thread_id() { return int64_t(syscall(SYS_gettid)); }

bool profile(const int64_t*, const uint64_t* const*, size_t, int, const std::string&) { return false; }  // use perf

uint64_t monotonic_ns() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return uint64_t(ts.tv_sec) * 1000000000ull + uint64_t(ts.tv_nsec);
}

// ---- files and output -----------------------------------------------------------------------------------------

FILE* fopen_utf8(const std::string& path, const char* mode) { return std::fopen(path.c_str(), mode); }
int64_t ftell64(FILE* f) { return ftello(f); }
int fseek64(FILE* f, int64_t off, int origin) { return fseeko(f, off, origin); }

uint64_t read_at(FILE* f, int64_t off, void* buf, uint64_t size) {
    if (size > uint64_t(SSIZE_MAX)) return 0;  // the game passes -1 as "everything": fread read nothing
    int fd = fileno(f);
    uint64_t done = 0;
    while (done < size) {
        ssize_t n = pread(fd, static_cast<uint8_t*>(buf) + done, size_t(size - done), off + int64_t(done));
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        done += uint64_t(n);
    }
    return done;
}

bool make_dirs(const std::string& path) {
    std::error_code ec;
    std::filesystem::create_directories(path, ec);
    return !ec;
}

void log_line(const char* line) { std::fprintf(stderr, "%s\n", line); }

std::string data_root() {
    const char* r = std::getenv("NX_DATA_ROOT");
#ifdef NX_DATA_ROOT_EXE
    // AP_LINUX_PLATFORM: a relative pc_data_root is taken from the binary's folder (not the current one), so the game
    // starts from any folder, a shortcut or a file manager; an absolute one, or NX_DATA_ROOT, as before.
    if (!r) {
        static const std::string root = [] {
            const char* rel = nx::game::kInfo.pc_data_root;
            if (!rel || !*rel || rel[0] == '/') return std::string(rel ? rel : "");
            std::error_code ec;
            const std::filesystem::path exe = std::filesystem::read_symlink("/proc/self/exe", ec);
            if (ec) return std::string(rel);
            return (exe.parent_path() / rel).lexically_normal().string();
        }();
        return root;
    }
#endif
    return r ? r : nx::game::kInfo.pc_data_root;
}

std::string setting(const std::string& key) {
    std::string env = "NX_";
    for (char ch : key) env += char(std::toupper(static_cast<unsigned char>(ch)));
    const char* v = std::getenv(env.c_str());
    return v ? v : "";
}

// ---- window ---------------------------------------------------------------------------------------------------

bool headless() {
    static const bool on = std::getenv("NX_HEADLESS") != nullptr;
    return on;
}

void* create_window() {
    run_on_main([] {
        if (g_window) return;
        int w = 1280, h = 720;
        std::string r = setting("resolution");
        if (!r.empty()) std::sscanf(r.c_str(), "%dx%d", &w, &h);
        g_window = SDL_CreateWindow(nx::game::kInfo.title, w, h,
                                    SDL_WINDOW_RESIZABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY | SDL_WINDOW_VULKAN |
                                        (headless() ? SDL_WINDOW_HIDDEN : 0));
        if (!g_window) {
            log_line(SDL_GetError());
            return;
        }
#ifdef NX_CUSTOM_WINDOW_TITLE  // AP_LINUX_PLATFORM: the game's title (UTF-32 wchar_t -> UTF-8)
        {
            std::string t;
            for (const wchar_t* p = nx::game::kWindowTitle; p && *p; ++p) {
                const uint32_t c = uint32_t(*p);
                if (c < 0x80) t += char(c);
                else if (c < 0x800) t += char(0xC0 | (c >> 6)), t += char(0x80 | (c & 0x3F));
                else if (c < 0x10000) t += char(0xE0 | (c >> 12)), t += char(0x80 | ((c >> 6) & 0x3F)), t += char(0x80 | (c & 0x3F));
                else t += char(0xF0 | (c >> 18)), t += char(0x80 | ((c >> 12) & 0x3F)), t += char(0x80 | ((c >> 6) & 0x3F)), t += char(0x80 | (c & 0x3F));
            }
            if (!t.empty()) SDL_SetWindowTitle(g_window, t.c_str());
        }
#endif
#ifdef NX_LINUX_WINDOW_ICON  // AP_LINUX_PLATFORM: the largest PNG image of the game's .ico
        if (tsuki_decode_image && nx_window_icon_size > 6) {
            const uint8_t* ico = nx_window_icon;
            const uint32_t count = ico[4] | (ico[5] << 8);
            uint32_t best_w = 0, best_off = 0, best_size = 0;
            for (uint32_t k = 0; k < count && 6 + 16 * (k + 1) <= nx_window_icon_size; k++) {
                const uint8_t* d = ico + 6 + 16 * k;
                const uint32_t w = d[0] ? d[0] : 256;
                const uint32_t size = d[8] | (d[9] << 8) | (d[10] << 16) | (uint32_t(d[11]) << 24);
                const uint32_t off = d[12] | (d[13] << 8) | (d[14] << 16) | (uint32_t(d[15]) << 24);
                if (off + size > nx_window_icon_size || size < 8 || std::memcmp(ico + off, "\x89PNG", 4) != 0) continue;
                if (w > best_w) best_w = w, best_off = off, best_size = size;
            }
            uint32_t iw = 0, ih = 0;
            uint8_t* rgba = nullptr;
            if (best_size && tsuki_decode_image(ico + best_off, best_size, 256, &iw, &ih, &rgba) == 0 && rgba) {
                if (SDL_Surface* s = SDL_CreateSurfaceFrom(int(iw), int(ih), SDL_PIXELFORMAT_RGBA32, rgba, int(iw * 4))) {
                    SDL_SetWindowIcon(g_window, s);
                    SDL_DestroySurface(s);
                }
                std::free(rgba);
            }
        }
#endif
#ifdef NX_FIXED_ASPECT  // AP_LINUX_PLATFORM: the window keeps the picture's aspect and is at least 640 px wide
        {
            const float aspect = float(std::max(w, 1)) / float(std::max(h, 1));
            SDL_SetWindowAspectRatio(g_window, aspect, aspect);
            SDL_SetWindowMinimumSize(g_window, 640, int(640.0f / aspect + 0.5f));
        }
#endif
        if (!headless()) {
            SDL_RaiseWindow(g_window);
            if (setting("fullscreen") == "1") SDL_SetWindowFullscreen(g_window, true);
        }
        int pw = 0, ph = 0;
        SDL_GetWindowSizeInPixels(g_window, &pw, &ph);
        g_pixel_w = uint32_t(pw), g_pixel_h = uint32_t(ph);
    });
    return g_window;  // the Vulkan surface is made from it (on the main thread, vulkan_create_surface)
}

void release_window(void*) {}
uint64_t window_generation() { return 1; }
bool window_minimized() { return g_minimized; }

void window_size(uint32_t& w, uint32_t& h) {
    w = g_pixel_w;
    h = g_pixel_h;
}

void screen_size(uint32_t& w, uint32_t& h) { window_size(w, h); }
float default_render_scale() { return 1.0f; }

// ---- input: first SDL gamepad + keyboard (sampled on the main thread) -----------------------------------------

static void sample_input() {
    PadState p;
    static SDL_Gamepad* pad = nullptr;
    if (!pad) {
        int n = 0;
        SDL_JoystickID* ids = SDL_GetGamepads(&n);
        if (ids && n > 0) pad = SDL_OpenGamepad(ids[0]);
        SDL_free(ids);
    }
    if (pad && !SDL_GamepadConnected(pad)) {
        SDL_CloseGamepad(pad);
        pad = nullptr;
    }
    if (pad) {
        // SDL names buttons by position (south/east/...); Nintendo's A is the east button, B the south one.
        struct Map {
            SDL_GamepadButton b;
            uint64_t bit;
        };
        static const Map map[] = {
            {SDL_GAMEPAD_BUTTON_EAST, kA},           {SDL_GAMEPAD_BUTTON_SOUTH, kB},
            {SDL_GAMEPAD_BUTTON_NORTH, kX},          {SDL_GAMEPAD_BUTTON_WEST, kY},
            {SDL_GAMEPAD_BUTTON_LEFT_SHOULDER, kL},  {SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER, kR},
            {SDL_GAMEPAD_BUTTON_START, kPlus},       {SDL_GAMEPAD_BUTTON_BACK, kMinus},
            {SDL_GAMEPAD_BUTTON_LEFT_STICK, kStickL}, {SDL_GAMEPAD_BUTTON_RIGHT_STICK, kStickR},
            {SDL_GAMEPAD_BUTTON_DPAD_LEFT, kLeft},   {SDL_GAMEPAD_BUTTON_DPAD_UP, kUp},
            {SDL_GAMEPAD_BUTTON_DPAD_RIGHT, kRight}, {SDL_GAMEPAD_BUTTON_DPAD_DOWN, kDown},
        };
        for (const Map& m : map)
            if (SDL_GetGamepadButton(pad, m.b)) p.buttons |= m.bit;
        if (SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_LEFT_TRIGGER) > 12000) p.buttons |= kZL;
        if (SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_RIGHT_TRIGGER) > 12000) p.buttons |= kZR;
        auto axis = [&](SDL_GamepadAxis a, bool invert) {
            int v = SDL_GetGamepadAxis(pad, a);
            if (v > -7849 && v < 7849) return 0;
            v = std::clamp(v, -kStickMax, kStickMax);
            return invert ? -v : v;
        };
        p.lx = axis(SDL_GAMEPAD_AXIS_LEFTX, false);
        p.ly = axis(SDL_GAMEPAD_AXIS_LEFTY, true);  // SDL: down is positive; Horizon: up is positive
        p.rx = axis(SDL_GAMEPAD_AXIS_RIGHTX, false);
        p.ry = axis(SDL_GAMEPAD_AXIS_RIGHTY, true);
    }
    // Keyboard: WASD / arrows move, Space A, Shift B (run), J X, K Y, Q/E L/R, Z/C ZL/ZR, Enter +, Esc -.
    if (g_window && (SDL_GetWindowFlags(g_window) & SDL_WINDOW_INPUT_FOCUS)) {
        const bool* k = SDL_GetKeyboardState(nullptr);
        if (k[SDL_SCANCODE_A] || k[SDL_SCANCODE_LEFT]) p.lx = -kStickMax, p.buttons |= kLeft;
        if (k[SDL_SCANCODE_D] || k[SDL_SCANCODE_RIGHT]) p.lx = kStickMax, p.buttons |= kRight;
        if (k[SDL_SCANCODE_W] || k[SDL_SCANCODE_UP]) p.ly = kStickMax, p.buttons |= kUp;
        if (k[SDL_SCANCODE_S] || k[SDL_SCANCODE_DOWN]) p.ly = -kStickMax, p.buttons |= kDown;
        if (k[SDL_SCANCODE_SPACE]) p.buttons |= kA;
        if (k[SDL_SCANCODE_LSHIFT] || k[SDL_SCANCODE_RSHIFT]) p.buttons |= kB;
        if (k[SDL_SCANCODE_J]) p.buttons |= kX;
        if (k[SDL_SCANCODE_K]) p.buttons |= kY;
        if (k[SDL_SCANCODE_Q]) p.buttons |= kL;
        if (k[SDL_SCANCODE_E]) p.buttons |= kR;
        if (k[SDL_SCANCODE_Z]) p.buttons |= kZL;
        if (k[SDL_SCANCODE_C]) p.buttons |= kZR;
        if (k[SDL_SCANCODE_RETURN]) p.buttons |= kPlus;
        if (k[SDL_SCANCODE_ESCAPE]) p.buttons |= kMinus;
    }
    std::lock_guard lk(g_pad_lock);
    g_pad = p;
}

void read_pad(PadState& p) {
    {
        std::lock_guard lk(g_pad_lock);
        p.buttons |= g_pad.buttons;
        if (g_pad.lx) p.lx = g_pad.lx;
        if (g_pad.ly) p.ly = g_pad.ly;
        if (g_pad.rx) p.rx = g_pad.rx;
        if (g_pad.ry) p.ry = g_pad.ry;
    }
    input_script(p);
}

// ---- audio: SDL3 stream, s16 stereo 48 kHz, fed from the queue of submitted buffers ----------------------------

namespace {
struct Pending {
    const uint8_t* data;
    size_t size, pos;
    void* ctx;
};
AudioDone g_done = nullptr;
SDL_AudioStream* g_stream = nullptr;
std::mutex g_audio;
std::deque<Pending> g_pending;

void SDLCALL audio_cb(void*, SDL_AudioStream* s, int additional, int) {
    uint8_t buf[4096];
    while (additional > 0) {
        int n = std::min<int>(additional, sizeof buf);
        int filled = 0;
        std::vector<void*> finished;
        {
            std::lock_guard lk(g_audio);
            while (filled < n && !g_pending.empty()) {
                Pending& p = g_pending.front();
                size_t take = std::min(size_t(n - filled), p.size - p.pos);
                std::memcpy(buf + filled, p.data + p.pos, take);
                filled += int(take);
                p.pos += take;
                if (p.pos == p.size) {
                    finished.push_back(p.ctx);
                    g_pending.pop_front();
                }
            }
        }
        if (filled < n) std::memset(buf + filled, 0, size_t(n - filled));  // underrun: silence
        SDL_PutAudioStreamData(s, buf, n);
        for (void* ctx : finished) g_done(ctx);
        additional -= n;
    }
}
}  // namespace

bool audio_open(AudioDone done) {
    g_done = done;
    // Headless test runs (and NX_MUTE=1) are silent: no device, the callers pace themselves with a timer.
    if (headless() || std::getenv("NX_MUTE")) return false;
    SDL_AudioSpec spec{SDL_AUDIO_S16LE, 2, 48000};
    g_stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, audio_cb, nullptr);
    if (!g_stream) {
        log("audio: %s", SDL_GetError());
        return false;
    }
    SDL_ResumeAudioStreamDevice(g_stream);
    return true;
}

bool audio_submit(const void* data, size_t bytes, void* ctx) {
    if (!g_stream) return false;
    std::lock_guard lk(g_audio);
    g_pending.push_back({static_cast<const uint8_t*>(data), bytes, 0, ctx});
    return true;
}

uint32_t audio_queued() {
    std::lock_guard lk(g_audio);
    return uint32_t(g_pending.size());
}

void audio_pause(bool paused) {
    if (!g_stream) return;
    if (paused)
        SDL_PauseAudioStreamDevice(g_stream);
    else
        SDL_ResumeAudioStreamDevice(g_stream);
}

// ---- Vulkan (the system loader, through SDL) ------------------------------------------------------------------

void* vulkan_get_instance_proc_addr() {
    static void* gipa = [] {
        if (!SDL_Vulkan_LoadLibrary(nullptr)) {
            log_line(SDL_GetError());
            return static_cast<void*>(nullptr);
        }
        return reinterpret_cast<void*>(SDL_Vulkan_GetVkGetInstanceProcAddr());
    }();
    return gipa;
}

// The window system's surface extension (VK_KHR_xlib_surface, VK_KHR_wayland_surface, ...): SDL lists it next to
// VK_KHR_surface, which vk_bridge.cpp adds by itself.
const char* vulkan_surface_extension() {
    static const char* ext = [] {
        const char* found = "VK_KHR_xlib_surface";
        Uint32 n = 0;
        const char* const* names = SDL_Vulkan_GetInstanceExtensions(&n);
        for (Uint32 k = 0; names && k < n; k++)
            if (std::strcmp(names[k], VK_KHR_SURFACE_EXTENSION_NAME) != 0) found = names[k];
        log("platform: %s video driver, %s", SDL_GetCurrentVideoDriver(), found);
        return found;
    }();
    return ext;
}

int vulkan_create_surface(void* instance, void* native_window, uint64_t* surface, void* /*gipa: SDL's own*/) {
    VkSurfaceKHR s = VK_NULL_HANDLE;
    bool ok = false;
    run_on_main([&] {
        ok = native_window && SDL_Vulkan_CreateSurface(static_cast<SDL_Window*>(native_window),
                                                       static_cast<VkInstance>(instance), nullptr, &s);
        if (!ok) log("platform: SDL_Vulkan_CreateSurface: %s", SDL_GetError());
    });
    *surface = reinterpret_cast<uint64_t>(s);
    return ok ? VK_SUCCESS : VK_ERROR_INITIALIZATION_FAILED;
}

// AP_LINUX_PLATFORM: an error box that blocks until it is closed (shown by the main thread, which owns SDL's windows).
void message_box(const char* title, const char* text) {
    std::fprintf(stderr, "%s: %s\n", title, text);
    std::fflush(stderr);
    if (headless()) return;
    run_on_main([&] {
        if (!SDL_ShowSimpleMessageBox(SDL_MESSAGEBOX_ERROR, title, text, g_window))
            std::fprintf(stderr, "(no message box: %s)\n", SDL_GetError());
    });
}

#ifdef NX_FIXED_ASPECT
// AP_LINUX_PLATFORM: Ctrl+1/2/3: the window at pct % of 1920 px wide (in the window's aspect), inside the usable area
// of its display, centred where it was. Not in fullscreen.
static void set_window_width(int pct) {
    if (!g_window || (SDL_GetWindowFlags(g_window) & SDL_WINDOW_FULLSCREEN)) return;
    if (SDL_GetWindowFlags(g_window) & SDL_WINDOW_MAXIMIZED) SDL_RestoreWindow(g_window);
    int cw = 0, ch = 0, x = 0, y = 0;
    SDL_GetWindowSize(g_window, &cw, &ch);
    SDL_GetWindowPosition(g_window, &x, &y);
    const float aspect = ch > 0 ? float(cw) / float(ch) : 16.0f / 9.0f;
    const float scale = std::max(0.25f, SDL_GetWindowPixelDensity(g_window));  // 1920 px, not 1920 points
    int w = int(1920.0f * float(pct) / 100.0f / scale), h = int(float(w) / aspect + 0.5f);
    SDL_Rect area{};
    if (SDL_GetDisplayUsableBounds(SDL_GetDisplayForWindow(g_window), &area)) {
        if (w > area.w) w = area.w, h = int(float(w) / aspect + 0.5f);
        if (h > area.h) h = area.h, w = int(float(h) * aspect + 0.5f);
    }
    w = std::max(w, 640), h = int(float(w) / aspect + 0.5f);
    SDL_SetWindowSize(g_window, w, h);
    int nx_ = x + (cw - w) / 2, ny = y + (ch - h) / 2;
    if (area.w > 0) {
        nx_ = std::max(area.x, std::min(nx_, area.x + area.w - w));
        ny = std::max(area.y, std::min(ny, area.y + area.h - h));
    }
    SDL_SetWindowPosition(g_window, nx_, ny);
    log("window: %dx%d (%d%% of 1920 px wide)", w, h, pct);
}
#endif

}  // namespace nx::plat

// ---- crash handler and entry ----------------------------------------------------------------------------------

namespace {

void crash_handler(int sig, siginfo_t* info, void* uc) {
    if ((sig == SIGSEGV || sig == SIGBUS) && nx::plat::write_watch_fault(info->si_addr)) return;  // write_watch.cpp
    const auto& m = static_cast<ucontext_t*>(uc)->uc_mcontext;
    Dl_info d{};
    uint64_t pc = uint64_t(m.gregs[REG_RIP]);
    dladdr(reinterpret_cast<void*>(pc), &d);
    nx::log("CRASH: signal %d at %s+%llx (%s), data address %llx", sig, d.dli_fname ? d.dli_fname : "?",
            (unsigned long long)(pc - uint64_t(d.dli_fbase)), d.dli_sname ? d.dli_sname : "?",
            (unsigned long long)uint64_t(info->si_addr));
    nx::CPU& c = nx::current_cpu();
    nx::log("  guest lr %s  sp %llx", nx::describe(c.x[30]).c_str(), (unsigned long long)c.x[31]);
    for (int k = 0; k < 32; k += 4)
        nx::log("  x%-2d %016llx %016llx %016llx %016llx", k, (unsigned long long)c.x[k], (unsigned long long)c.x[k + 1],
                (unsigned long long)c.x[k + 2], (unsigned long long)c.x[k + 3]);
    nx::backtrace(c);
    nx::log_recent_files();
    std::fflush(nullptr);
    signal(sig, SIG_DFL);
    raise(sig);
}

void game_thread() {
    try {
        nx::boot();
    } catch (const nx::LongJmp& j) {
        nx::fatal("longjmp to buffer %llx with no setjmp frame", (unsigned long long)j.buf);
    } catch (const std::exception& e) {
        nx::fatal("host exception: %s", e.what());
    }
}

}  // namespace

#ifdef NX_CONFIG_ENV
// AP_LINUX_PLATFORM: the NX_* lines of <data root>/config.txt become environment variables before anything reads
// them, as AP_CONFIG_ENV on Windows (the launcher writes that file; it can be edited by hand). A variable already set
// wins (terminal, shortcut, launch options). Comments with ; or #. An empty value sets nothing, and so does 0 for the
// switches that are only tested for existence. Read once: a change needs the game started again.
static void load_config_env() {
    const std::string path = nx::plat::data_root() + "/config.txt";
    FILE* f = nx::plat::fopen_utf8(path, "rb");
    if (!f) return;
    std::string text;
    char buf[4096];
    for (size_t n; (n = std::fread(buf, 1, sizeof buf, f)) > 0;) text.append(buf, n);
    std::fclose(f);
    if (text.compare(0, 3, "\xEF\xBB\xBF") == 0) text.erase(0, 3);
    auto trim = [](const std::string& s) {
        size_t a = s.find_first_not_of(" \t\r"), b = s.find_last_not_of(" \t\r");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    };
    static const char* const kExistence[] = {"NX_HANDHELD", "NX_MUTE", "NX_FIFO", "NX_NO_FRAME_LIMIT"};
    std::string set, kept;
    size_t pos = 0;
    while (pos < text.size()) {
        size_t end = text.find('\n', pos);
        std::string line = text.substr(pos, end == std::string::npos ? std::string::npos : end - pos);
        pos = end == std::string::npos ? text.size() : end + 1;
        if (size_t c = line.find_first_of(";#"); c != std::string::npos) line.erase(c);
        size_t eq = line.find('=');
        if (eq == std::string::npos) continue;
        const std::string name = trim(line.substr(0, eq)), value = trim(line.substr(eq + 1));
        if (name.size() < 4 || name.compare(0, 3, "NX_") != 0 || value.empty() || name == "NX_DATA_ROOT") continue;
        if (name.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_") != std::string::npos) continue;
        bool existence = false;
        for (const char* k : kExistence) existence |= name == k;
        if (existence && value == "0") continue;
        if (std::getenv(name.c_str())) {
            kept += " " + name;
            continue;
        }
        if (setenv(name.c_str(), value.c_str(), 0) == 0) set += " " + name;
    }
    nx::log("config: %s:%s%s%s", path.c_str(), set.empty() ? " nothing set" : set.c_str(),
            kept.empty() ? "" : "; already in the environment (kept):", kept.c_str());
}
#endif

int main() {
    nx::plat::g_main_thread = pthread_self();
#ifdef NX_CONFIG_ENV
    load_config_env();  // AP_LINUX_PLATFORM: first, so every NX_* of config.txt is seen (the log, the memory guard...)
#endif

    // The crash handler runs on its own stack: a guest stack overflow would leave it none.
    static std::vector<uint8_t> alt(1 << 20);
    stack_t ss{};
    ss.ss_sp = alt.data();
    ss.ss_size = alt.size();
    sigaltstack(&ss, nullptr);
    struct sigaction sa{};
    sa.sa_sigaction = crash_handler;
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
    sigaction(SIGSEGV, &sa, nullptr);
    sigaction(SIGBUS, &sa, nullptr);
    sigaction(SIGILL, &sa, nullptr);
    sigaction(SIGFPE, &sa, nullptr);

    // AP_LINUX_AUDIO_OPT (apply_linux_audio_opt.py): audio is optional; without a device the game runs silent
    if (!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMEPAD)) {
        std::fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    if (!SDL_InitSubSystem(SDL_INIT_AUDIO))
        std::fprintf(stderr, "SDL audio: %s (the game runs without sound)\n", SDL_GetError());

    nx::plat::start_memory_guard();  // before anything can grow: a leak must never freeze the machine

    // The game on its own thread, with a big stack (recompiled calls nest deeply); the main thread keeps the event
    // loop.
    nx::plat::start_thread([](void*) { game_thread(); nx::plat::exit_process(0); }, nullptr, 64ull << 20);

    using namespace nx::plat;
    for (;;) {
        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            switch (e.type) {
            case SDL_EVENT_QUIT:
                nx::log("platform: quit requested (window closed)");
                exit_process(0);
            case SDL_EVENT_WINDOW_MINIMIZED:
            case SDL_EVENT_WINDOW_HIDDEN:
                g_minimized = true;
                break;
            case SDL_EVENT_WINDOW_RESTORED:
            case SDL_EVENT_WINDOW_SHOWN:
                g_minimized = false;
                break;
            case SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED:
                g_pixel_w = uint32_t(e.window.data1), g_pixel_h = uint32_t(e.window.data2);
                break;
            case SDL_EVENT_KEY_DOWN:
                if ((e.key.scancode == SDL_SCANCODE_F11 ||
                     (e.key.scancode == SDL_SCANCODE_RETURN && (e.key.mod & SDL_KMOD_ALT))) && g_window)
                    SDL_SetWindowFullscreen(g_window, !(SDL_GetWindowFlags(g_window) & SDL_WINDOW_FULLSCREEN));
#ifdef NX_TITLE_LIVE  // AP_LINUX_PLATFORM: F5 / F6 change the title video and music (title_bg.cpp)
                if ((e.key.scancode == SDL_SCANCODE_F5 || e.key.scancode == SDL_SCANCODE_F6) && !e.key.repeat)
                    tsuki_title::hotkey(e.key.scancode == SDL_SCANCODE_F6 ? 1 : -1);
#endif
#ifdef NX_FIXED_ASPECT  // AP_LINUX_PLATFORM: Ctrl+1/2/3, the window at 50/75/100% of 1920 px wide
                if ((e.key.mod & SDL_KMOD_CTRL) && !e.key.repeat && e.key.scancode >= SDL_SCANCODE_1 &&
                    e.key.scancode <= SDL_SCANCODE_3)
                    set_window_width(e.key.scancode == SDL_SCANCODE_1 ? 50 : e.key.scancode == SDL_SCANCODE_2 ? 75 : 100);
#endif
                break;
            default:
                break;
            }
        }
        std::deque<std::function<void()>> work;
        {
            std::lock_guard lk(g_main_lock);
            work.swap(g_main_work);
        }
        for (auto& fn : work) fn();
        sample_input();
        SDL_Delay(2);
    }
}
