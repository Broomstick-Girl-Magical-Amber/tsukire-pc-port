r"""apply_linux_discord.py - native Linux: Discord Rich Presence (AP_LINUX_DISCORD).

discord_tsuki.cpp spoke to Discord only through the Windows named pipe \\?\pipe\discord-ipc-N. On Linux Discord
listens on a Unix socket, discord-ipc-0..9, in $XDG_RUNTIME_DIR (or $TMPDIR / $TMP / $TEMP / /tmp), and the
Flatpak and Snap builds put it one folder down: app/com.discordapp.Discord, app/com.discordapp.DiscordCanary,
app/dev.vencord.Vesktop, snap.discord, snap.discord-canary. The protocol is the same ([opcode][length][JSON]).
Now, on native Linux:
- the same client (handshake, READY, SET_ACTIVITY, PING/PONG, reconnects every 15 s) over that socket;
- the start time is the process start (/proc/self/stat + btime), as GetProcessTimes on Windows;
- discord_ultimo.txt (last day and route seen) next to the binary, as next to the exe;
- the thread runs at a lower priority (nice +5), as THREAD_PRIORITY_BELOW_NORMAL.
NX_DISCORD=0 and NX_DISCORD_APP_ID work as on Windows. Windows and Android do not change.
"""
from _patch import run

MARK = "AP_LINUX_DISCORD"
DC = "games/tsukihime_common/discord_tsuki.cpp"
RECO = "../tools/recomp_tsukihime.ps1"
LX = "defined(__linux__) && !defined(__ANDROID__)"

EDITS = [
    (DC,
     '''#include <windows.h>
#endif

#include <nx/runtime.h>
''',
     '''#include <windows.h>
#endif
#if defined(__linux__) && !defined(__ANDROID__)  // AP_LINUX_DISCORD (apply_linux_discord.py): the Unix socket
#include <cerrno>
#include <filesystem>
#include <fstream>
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <unistd.h>
#endif

#include <nx/runtime.h>
'''),
    (DC,
     '''#ifdef _WIN32
// ---------------------------------------------------------------------------------------------- IPC (Windows)
enum : uint32_t { kOpHandshake = 0, kOpFrame = 1, kOpClose = 2, kOpPing = 3, kOpPong = 4 };

class Pipe {
''',
     '''#if defined(_WIN32) || (%s)  // AP_LINUX_DISCORD: native Linux too
// ---------------------------------------------------------------------------------------------- IPC (Windows)
enum : uint32_t { kOpHandshake = 0, kOpFrame = 1, kOpClose = 2, kOpPing = 3, kOpPong = 4 };

#ifdef _WIN32  // AP_LINUX_DISCORD: the Linux Pipe, process_start_unix and current_pid are after the Windows ones
class Pipe {
''' % LX),
    (DC,
     '''    return int64_t(std::time(nullptr));
}

// ---------------------------------------------------------------------------------------------- ultimo dia/ruta
''',
     '''    return int64_t(std::time(nullptr));
}

unsigned long current_pid() { return GetCurrentProcessId(); }  // AP_LINUX_DISCORD
#else
// ---------------------------------------------------------------------------------------------- IPC (Linux)
// AP_LINUX_DISCORD (apply_linux_discord.py): Discord listens on a Unix socket discord-ipc-N in the runtime folder;
// Flatpak and Snap builds one folder down. Same messages as the pipe: [opcode u32 LE][length u32 LE][JSON].
class Pipe {
public:
    ~Pipe() { close(); }
    bool open() {
        std::vector<std::string> bases;
        for (const char* v : {"XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"})
            if (const char* e = std::getenv(v); e && *e) bases.push_back(e);
        bases.push_back("/tmp");
        static const char* const kSubs[] = {"", "app/com.discordapp.Discord/", "app/com.discordapp.DiscordCanary/",
                                            "app/dev.vencord.Vesktop/", "snap.discord/", "snap.discord-canary/"};
        for (int n = 0; n < 10; n++)
            for (const std::string& b : bases)
                for (const char* sub : kSubs) {
                    const std::string path = b + "/" + sub + "discord-ipc-" + std::to_string(n);
                    sockaddr_un a{};
                    if (path.size() >= sizeof a.sun_path) continue;
                    a.sun_family = AF_UNIX;
                    std::memcpy(a.sun_path, path.c_str(), path.size() + 1);
                    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
                    if (fd < 0) return false;
                    if (::connect(fd, reinterpret_cast<const sockaddr*>(&a), sizeof a) == 0) {
                        fd_ = fd;
                        index_ = n;
                        return true;
                    }
                    ::close(fd);
                }
        return false;
    }
    void close() {
        if (fd_ >= 0) ::close(fd_);
        fd_ = -1;
    }
    bool is_open() const { return fd_ >= 0; }
    int index() const { return index_; }

    // Header and JSON in one buffer, sent whole (MSG_NOSIGNAL: a closed Discord is an error, not SIGPIPE).
    bool write(uint32_t op, const std::string& json) {
        std::vector<uint8_t> b(8 + json.size());
        uint32_t len = uint32_t(json.size());
        for (int i = 0; i < 4; i++) b[i] = uint8_t(op >> (8 * i)), b[4 + i] = uint8_t(len >> (8 * i));
        std::memcpy(b.data() + 8, json.data(), json.size());
        size_t off = 0;
        while (off < b.size()) {
            const ssize_t n = ::send(fd_, b.data() + off, b.size() - off, MSG_NOSIGNAL);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) return fail();
            off += size_t(n);
        }
        return true;
    }
    // A whole message waiting? Never blocks: false if there is nothing (or if Discord went away: see is_open).
    bool poll(uint32_t* op, std::string* json) {
        int avail = 0;
        if (::ioctl(fd_, FIONREAD, &avail) < 0) return fail();
        if (avail == 0) {  // nothing, or the other end closed (readable with 0 bytes)
            pollfd p{fd_, POLLIN, 0};
            if (::poll(&p, 1, 0) > 0) {
                char c;
                const ssize_t r = ::recv(fd_, &c, 1, MSG_PEEK | MSG_DONTWAIT);
                if (r == 0 || (r < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) return fail();
            }
            return false;
        }
        if (avail < 8) return false;
        uint8_t hdr[8];
        if (!read_exact(hdr, 8)) return false;
        *op = uint32_t(hdr[0]) | uint32_t(hdr[1]) << 8 | uint32_t(hdr[2]) << 16 | uint32_t(hdr[3]) << 24;
        uint32_t len = uint32_t(hdr[4]) | uint32_t(hdr[5]) << 8 | uint32_t(hdr[6]) << 16 | uint32_t(hdr[7]) << 24;
        if (len > (1u << 20)) return fail();
        json->assign(len, '\\0');
        return len == 0 || read_exact(&(*json)[0], len);
    }

private:
    bool read_exact(void* dst, size_t n) {
        auto* p = static_cast<uint8_t*>(dst);
        while (n) {
            const ssize_t got = ::recv(fd_, p, n, 0);
            if (got < 0 && errno == EINTR) continue;
            if (got <= 0) return fail();
            p += got, n -= size_t(got);
        }
        return true;
    }
    bool fail() {
        close();
        return false;
    }
    int fd_ = -1;
    int index_ = -1;
};

// When the process started (Unix seconds): /proc/self/stat field 22 (clock ticks after boot) + btime of /proc/stat.
int64_t process_start_unix() {
    std::ifstream st("/proc/self/stat");
    std::string s((std::istreambuf_iterator<char>(st)), std::istreambuf_iterator<char>());
    const size_t paren = s.rfind(')');  // the name (field 2) can hold spaces; fields 3.. come after the last ')'
    std::ifstream ps("/proc/stat");
    std::string line;
    long long btime = -1;
    while (std::getline(ps, line))
        if (line.compare(0, 6, "btime ") == 0) btime = std::atoll(line.c_str() + 6);
    const long tck = ::sysconf(_SC_CLK_TCK);
    if (paren != std::string::npos && btime > 0 && tck > 0) {
        const char* p = s.c_str() + paren + 1;
        for (int field = 3; field < 22 && *p; field++) {  // skip to field 22
            while (*p == ' ') p++;
            while (*p && *p != ' ') p++;
        }
        const unsigned long long ticks = std::strtoull(p, nullptr, 10);
        if (ticks) return int64_t(btime + (long long)(ticks / (unsigned long long)tck));
    }
    return int64_t(std::time(nullptr));
}

unsigned long current_pid() { return (unsigned long)::getpid(); }
#endif  // AP_LINUX_DISCORD: Pipe, process_start_unix, current_pid

// ---------------------------------------------------------------------------------------------- ultimo dia/ruta
'''),
    (DC,
     '''std::wstring saved_path(const wchar_t* suffix = L"") {
''',
     '''#ifdef _WIN32  // AP_LINUX_DISCORD: the Linux saved_path, load_saved and save_if_changed are after these
std::wstring saved_path(const wchar_t* suffix = L"") {
'''),
    (DC,
     '''    if (!ok || !MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING)) DeleteFileW(tmp.c_str());
}
''',
     '''    if (!ok || !MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING)) DeleteFileW(tmp.c_str());
}
#else
// AP_LINUX_DISCORD: the same as above with the file next to the binary (/proc/self/exe).
std::string saved_path(const char* suffix = "") {
    std::error_code ec;
    const std::filesystem::path exe = std::filesystem::read_symlink("/proc/self/exe", ec);
    if (ec || exe.empty()) return std::string();
    return (exe.parent_path() / "discord_ultimo.txt").string() + suffix;
}
// "12;ciel" -> dia 12, Ciel. Si no existe o no se entiende, no cambia nada.
void load_saved() {
    std::string path = saved_path();
    if (path.empty()) return;
    std::FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return;
    char buf[64] = {};
    size_t got = std::fread(buf, 1, sizeof buf - 1, f);
    std::fclose(f);
    std::string t(buf, got);
    size_t semi = t.find(';');
    if (semi == std::string::npos) return;
    int day = std::atoi(t.substr(0, semi).c_str());
    std::string rk = t.substr(semi + 1);
    while (!rk.empty() && (rk.back() == '\\n' || rk.back() == '\\r' || rk.back() == ' ')) rk.pop_back();
    Route r = route_from_key(rk);
    if (day < -1 || day > 99 || (day < 0 && r == Route::Unknown)) return;
    {
        std::lock_guard<std::mutex> lk(g->m);
        if (g->story.day >= 0 || g->story.route != Route::Unknown) return;  // ya se vio algo en esta sesion
        g->story.day = day;
        g->story.route = r;
        g->version++;
    }
    nx::log("discord: ultimo estado guardado: dia %d, ruta %s", day, r == Route::Unknown ? "-" : route_key(r));
}
// Escribe el archivo si dia/ruta cambiaron (temporal + rename, para no dejarlo a medias). Solo desde el hilo.
void save_if_changed(int day, Route r, int* saved_day, Route* saved_route) {
    if (day == *saved_day && r == *saved_route) return;
    if (day < 0 && r == Route::Unknown) return;
    *saved_day = day, *saved_route = r;
    std::string path = saved_path(), tmp = saved_path(".tmp");
    if (path.empty()) return;
    std::FILE* f = std::fopen(tmp.c_str(), "wb");
    if (!f) return;
    std::string t = std::to_string(day) + ";" + route_key(r) + "\\n";
    bool ok = std::fwrite(t.data(), 1, t.size(), f) == t.size();
    ok = std::fclose(f) == 0 && ok;
    if (!ok || std::rename(tmp.c_str(), path.c_str()) != 0) std::remove(tmp.c_str());
}
#endif  // AP_LINUX_DISCORD: saved_path, load_saved, save_if_changed
'''),
    (DC,
     '''        std::string msg = "{\\"cmd\\":\\"SET_ACTIVITY\\",\\"args\\":{\\"pid\\":" + std::to_string(GetCurrentProcessId()) +
''',
     '''        std::string msg = "{\\"cmd\\":\\"SET_ACTIVITY\\",\\"args\\":{\\"pid\\":" + std::to_string(current_pid()) +  // AP_LINUX_DISCORD
'''),
    (DC,
     '''    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
''',
     '''#ifdef _WIN32  // AP_LINUX_DISCORD
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
#else
    ::setpriority(PRIO_PROCESS, pid_t(::syscall(SYS_gettid)), 5);  // this thread only (Linux: nice per thread)
#endif
'''),
    (DC,
     '''#endif  // _WIN32

void ensure_started() {
''',
     '''#endif  // _WIN32 (AP_LINUX_DISCORD: and native Linux)

void ensure_started() {
'''),
    (DC,
     '''#ifdef _WIN32
        try {
            g = new State;
''',
     '''#if defined(_WIN32) || (%s)  // AP_LINUX_DISCORD: native Linux too
        try {
            g = new State;
''' % LX),
    (RECO,
     '''    @('both', 'framework\\games\\tsukihime_common\\hle_movie.cpp', 'AP_DISCORD', 'apply_discord.py', $false),
''',
     '''    @('both', 'framework\\games\\tsukihime_common\\hle_movie.cpp', 'AP_DISCORD', 'apply_discord.py', $false),
    @('both', 'framework\\games\\tsukihime_common\\discord_tsuki.cpp', 'AP_LINUX_DISCORD', 'apply_linux_discord.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
