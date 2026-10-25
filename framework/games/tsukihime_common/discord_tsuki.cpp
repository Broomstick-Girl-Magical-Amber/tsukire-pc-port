// discord_tsuki.cpp - Discord Rich Presence para Tsukihime, las dos ediciones (AP_DISCORD).
//
// Va en games/tsukihime_common/ (lo toma el GLOB de tsukihime_common.cmake). hle_movie.cpp le avisa que video
// empieza y cual termina (apply_discord.py); nada mas del juego se lee: ni memoria ni script.
//
//   Nivel 1: "Jugando a <nombre de la app en el Developer Portal>" + imagen grande + tiempo desde que se abrio el exe.
//   Nivel 2: donde esta, segun el video: menu (titlebg*), prologo, opening (con su ruta), titulo del dia
//            (phasetitle_DD_SS: solo la ruta, sin dia; dias 1-4 "No route yet"), finales y creditos sin decir cuales.
//   Textos en ingles para las dos ediciones: la global solo agrega _en/_zc/_zt al nombre y se quita.
//
// Protocolo (docs.discord.com/developers/topics/rpc, "RPC over IPC"; discord-rpc/documentation/hard-mode.md):
//   pipe \\?\pipe\discord-ipc-0..9 (el primero que abra); cada mensaje es [opcode u32 LE][largo u32 LE][JSON] en
//   UNA sola escritura. HANDSHAKE (0) {"v":1,"client_id":"..."} -> FRAME (1) con evt READY. Despues SET_ACTIVITY por
//   FRAME, con pid. PING (3) se contesta con PONG (4) y el mismo contenido; CLOSE (2) cierra.
//   Limite de Discord: 5 actualizaciones cada 20 s; aqui como mucho una cada 4 s, solo si el texto cambio.
//
// Hilo propio: video_started/video_ended solo copian un nombre bajo un mutex y despiertan al hilo (sin E/S). Si
// Discord no esta abierto o se cierra, se reintenta cada 15 s sin avisar. Ninguna falla del IPC llega al juego.
//   Ultimo dia y ruta vistos: discord_ultimo.txt junto al exe ("12;ciel"), lo escribe el hilo (no el juego) cuando
//   cambian y se lee al arrancar. "Continuar" retoma a mitad de un dia sin video de titulo (probado 2026-10-19);
//   con el archivo se muestra lo ultimo visto hasta el proximo titulo. Con otro save puede quedar atrasado.
//   NX_DISCORD=0            lo apaga (no se crea el hilo ni se abre el pipe).
//   NX_DISCORD_APP_ID=<id>  otro Application ID (pruebas). El de siempre esta en kAppId, aqui abajo.
#include <atomic>
#include <cctype>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <cwchar>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

#include <nx/runtime.h>

namespace {

// ---------------------------------------------------------------------------------------------- configuracion
constexpr const char* kAppId = "1561573111818879056";                  // Discord Developer Portal, una app para las 2
constexpr const char* kLargeImage = "landscape";                       // nombre del asset en Rich Presence > Art Assets
constexpr const char* kLargeText = "Tsukihime -A piece of blue glass moon-";
constexpr int kMinSendSeconds = 4;    // entre dos SET_ACTIVITY (Discord: 5 cada 20 s)
constexpr int kSettleMs = 1500;       // el estado tiene que quedar quieto esto antes de mandarse (un video que
                                      // termina y otro que empieza enseguida no hacen parpadear el texto)
constexpr int kRetrySeconds = 15;     // entre intentos de conexion
constexpr int kRetryAfterCloseSeconds = 60;  // si Discord cerro el handshake (p. ej. Application ID invalido)

// ---------------------------------------------------------------------------------------------- videos -> estado
enum class Kind { Ignore, Menu, Prologue, Opening, Ending, Credits, DayTitle };
enum class Route { Unknown, Common, Arcueid, Ciel, Keep };  // Keep: el video no la dice (phasetitle_05_00)

struct VideoInfo {
    Kind kind = Kind::Ignore;
    int day = -1;
    Route route = Route::Unknown;
    std::string name;  // nombre normalizado (para el log)
};

bool starts_with(const std::string& s, const char* p) { return s.compare(0, std::strlen(p), p) == 0; }
bool ends_with(const std::string& s, const char* p) {
    size_t n = std::strlen(p);
    return s.size() >= n && s.compare(s.size() - n, n, p) == 0;
}

// "rom:/movie/phasetitle_13_01_en.mp4" o "movie/phasetitle_13_01_en.mp4" -> "phasetitle_13_01"
std::string normalize(const char* rel) {
    std::string s(rel ? rel : "");
    size_t slash = s.find_last_of("/\\");
    if (slash != std::string::npos) s = s.substr(slash + 1);
    for (char& ch : s) ch = char(std::tolower(static_cast<unsigned char>(ch)));
    if (ends_with(s, ".mp4")) s.resize(s.size() - 4);
    for (const char* lang : {"_en", "_zc", "_zt"})  // idiomas de la edicion global
        if (ends_with(s, lang)) {
            s.resize(s.size() - 3);
            break;
        }
    return s;
}

// Tabla verificada el 2026-10-18 contra los videos de las dos ediciones (romfs\movie) y sus cuadros:
//   phasetitle_DD_00: dias 1-4 comunes, 05_00 lo usan las dos rutas, 06+ Arcueid; _01: Ciel (05_01 a 15_01a/b);
//   _02: Arcueid (10_02). op1_arc / op2_ciel. ed01_arc, ed02_ciel, ed03_ciel. credit00-06. titlebg1-4. prologue.
//   TM_CI (logo) y phaseout (transicion sin texto) no cambian nada.
VideoInfo classify(const char* rel) {
    VideoInfo v;
    v.name = normalize(rel);
    const std::string& s = v.name;
    if (starts_with(s, "titlebg")) {
        v.kind = Kind::Menu;
    } else if (s == "prologue") {
        v.kind = Kind::Prologue;
    } else if (starts_with(s, "op") && s.size() > 2 && std::isdigit(static_cast<unsigned char>(s[2]))) {
        v.kind = Kind::Opening;
        v.route = ends_with(s, "_arc") ? Route::Arcueid : ends_with(s, "_ciel") ? Route::Ciel : Route::Keep;
    } else if (starts_with(s, "ed") && s.size() > 2 && std::isdigit(static_cast<unsigned char>(s[2]))) {
        v.kind = Kind::Ending;
    } else if (starts_with(s, "credit")) {
        v.kind = Kind::Credits;
    } else if (starts_with(s, "phasetitle_") && s.size() >= 16 && s[13] == '_' &&
               std::isdigit(static_cast<unsigned char>(s[11])) && std::isdigit(static_cast<unsigned char>(s[12])) &&
               std::isdigit(static_cast<unsigned char>(s[14])) && std::isdigit(static_cast<unsigned char>(s[15]))) {
        v.kind = Kind::DayTitle;
        v.day = (s[11] - '0') * 10 + (s[12] - '0');
        int sub = (s[14] - '0') * 10 + (s[15] - '0');
        if (sub == 0) v.route = v.day <= 4 ? Route::Common : v.day == 5 ? Route::Keep : Route::Arcueid;
        else if (sub == 1) v.route = Route::Ciel;
        else if (sub == 2) v.route = Route::Arcueid;
        else v.route = Route::Keep;
    }
    return v;
}

const char* route_text(Route r) {
    switch (r) {
    case Route::Common: return "No route";  // dias 1-4 y prologo
    case Route::Arcueid: return "Arcueid route";
    case Route::Ciel: return "Ciel route";
    default: return "";
    }
}

struct Presence {
    std::string details, state;
    bool operator==(const Presence& o) const { return details == o.details && state == o.state; }
    bool operator!=(const Presence& o) const { return !(*this == o); }
};

// Lo que se sabe del juego. Solo se toca con State::m tomado.
struct Story {
    const void* active_key = nullptr;  // Extractor del host del video que corre ahora (si es uno que cuenta)
    Kind active = Kind::Ignore;
    int day = -1;                      // -1 sin saber, 0 prologo
    Route route = Route::Unknown;
    bool seen_menu = false;

    void started(const void* key, const VideoInfo& v) {
        switch (v.kind) {
        case Kind::Ignore: return;
        // El menu NO borra dia ni ruta: "Continuar" vuelve a la partida sin pasar otro titulo de dia (probado
        // 2026-10-19), asi que se conserva lo ultimo visto hasta el proximo phasetitle/prologo/opening.
        case Kind::Menu: seen_menu = true; break;
        case Kind::Prologue: day = 0, route = Route::Common; break;
        case Kind::Opening: if (v.route != Route::Keep) route = v.route; break;
        case Kind::Ending: case Kind::Credits: break;
        case Kind::DayTitle:
            day = v.day;
            if (v.route != Route::Keep) route = v.route;
            else if (route != Route::Arcueid && route != Route::Ciel) route = Route::Common;
            break;
        }
        active_key = key;
        active = v.kind;
    }
    void ended(const void* key) {
        if (key && key == active_key) active_key = nullptr, active = Kind::Ignore;
    }
    Presence compose() const {
        switch (active) {
        case Kind::Menu: return {"In the title menu", ""};
        case Kind::Prologue: return {"Watching the prologue", ""};
        case Kind::Opening: return {"Watching the opening", route_text(route)};
        case Kind::Ending: return {"Watching an ending", route_text(route)};
        case Kind::Credits: return {"Watching the credits", ""};
        default: break;
        }
        // Sin numero de dia (decidido 2026-10-19): cargar o saltar a mitad de un dia no pasa ningun video, y el dia
        // quedaba atrasado. La ruta solo falla si se salta a la otra ruta sin pasar un titulo.
        if (route != Route::Unknown) return {route_text(route), ""};
        return {seen_menu ? "In game" : "Starting up", ""};
    }
};

// ---------------------------------------------------------------------------------------------- estado compartido
// Se reserva y no se libera nunca: el hilo es detached y no debe quedar usando nada destruido al salir.
struct State {
    std::mutex m;
    std::condition_variable cv;
    Story story;
    uint64_t version = 0;  // sube con cada cambio de story
    std::chrono::steady_clock::time_point changed_at{};
    std::string app_id;
    int64_t start_unix = 0;
};
State* g = nullptr;
std::once_flag g_once;
std::atomic<bool> g_on{false};

bool env_off() {
    const char* e = std::getenv("NX_DISCORD");
    return e && std::strcmp(e, "0") == 0;
}

std::string json_escape(const std::string& s) {
    std::string o;
    for (char ch : s) {
        unsigned char u = static_cast<unsigned char>(ch);
        if (ch == '"' || ch == '\\') o += '\\', o += ch;
        else if (u < 0x20) {
            char b[8];
            std::snprintf(b, sizeof b, "\\u%04x", u);
            o += b;
        } else o += ch;
    }
    return o;
}

#ifdef _WIN32
// ---------------------------------------------------------------------------------------------- IPC (Windows)
enum : uint32_t { kOpHandshake = 0, kOpFrame = 1, kOpClose = 2, kOpPing = 3, kOpPong = 4 };

class Pipe {
public:
    ~Pipe() { close(); }
    bool open() {
        for (int n = 0; n < 10; n++) {
            wchar_t path[64];
            std::swprintf(path, 64, L"\\\\?\\pipe\\discord-ipc-%d", n);
            HANDLE h = CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, 0, nullptr);
            if (h != INVALID_HANDLE_VALUE) {
                h_ = h;
                index_ = n;
                return true;
            }
        }
        return false;
    }
    void close() {
        if (h_ != INVALID_HANDLE_VALUE) CloseHandle(h_);
        h_ = INVALID_HANDLE_VALUE;
    }
    bool is_open() const { return h_ != INVALID_HANDLE_VALUE; }
    int index() const { return index_; }

    // Cabecera y JSON en un solo WriteFile (el pipe no acepta el mensaje partido).
    bool write(uint32_t op, const std::string& json) {
        std::vector<uint8_t> b(8 + json.size());
        uint32_t len = uint32_t(json.size());
        for (int i = 0; i < 4; i++) b[i] = uint8_t(op >> (8 * i)), b[4 + i] = uint8_t(len >> (8 * i));
        std::memcpy(b.data() + 8, json.data(), json.size());
        DWORD done = 0;
        if (!WriteFile(h_, b.data(), DWORD(b.size()), &done, nullptr) || done != b.size()) return fail();
        return true;
    }
    // Hay un mensaje completo esperando? Nunca bloquea: false si no hay nada (o si el pipe se rompio: ver is_open).
    bool poll(uint32_t* op, std::string* json) {
        DWORD avail = 0;
        if (!PeekNamedPipe(h_, nullptr, 0, nullptr, &avail, nullptr)) return fail();
        if (avail < 8) return false;
        uint8_t hdr[8];
        if (!read_exact(hdr, 8)) return false;
        *op = uint32_t(hdr[0]) | uint32_t(hdr[1]) << 8 | uint32_t(hdr[2]) << 16 | uint32_t(hdr[3]) << 24;
        uint32_t len = uint32_t(hdr[4]) | uint32_t(hdr[5]) << 8 | uint32_t(hdr[6]) << 16 | uint32_t(hdr[7]) << 24;
        if (len > (1u << 20)) return fail();
        json->assign(len, '\0');
        return len == 0 || read_exact(&(*json)[0], len);
    }

private:
    bool read_exact(void* dst, DWORD n) {
        auto* p = static_cast<uint8_t*>(dst);
        while (n) {
            DWORD got = 0;
            if (!ReadFile(h_, p, n, &got, nullptr) || got == 0) return fail();
            p += got, n -= got;
        }
        return true;
    }
    bool fail() {
        close();
        return false;
    }
    HANDLE h_ = INVALID_HANDLE_VALUE;
    int index_ = -1;
};

int64_t process_start_unix() {
    FILETIME created, a, b, c;
    if (GetProcessTimes(GetCurrentProcess(), &created, &a, &b, &c)) {
        uint64_t t = (uint64_t(created.dwHighDateTime) << 32) | created.dwLowDateTime;
        if (t > 116444736000000000ull) return int64_t((t - 116444736000000000ull) / 10000000ull);
    }
    return int64_t(std::time(nullptr));
}

// ---------------------------------------------------------------------------------------------- ultimo dia/ruta
const char* route_key(Route r) {
    switch (r) {
    case Route::Common: return "common";
    case Route::Arcueid: return "arcueid";
    case Route::Ciel: return "ciel";
    default: return "";
    }
}
Route route_from_key(const std::string& k) {
    if (k == "common") return Route::Common;
    if (k == "arcueid") return Route::Arcueid;
    if (k == "ciel") return Route::Ciel;
    return Route::Unknown;
}
std::wstring saved_path(const wchar_t* suffix = L"") {
    std::wstring exe(32768, L'\0');
    DWORD n = GetModuleFileNameW(nullptr, &exe[0], DWORD(exe.size()));
    if (n == 0 || n >= exe.size()) return std::wstring();
    exe.resize(n);
    size_t slash = exe.find_last_of(L"\\/");
    if (slash == std::wstring::npos) return std::wstring();
    return exe.substr(0, slash + 1) + L"discord_ultimo.txt" + suffix;
}
// "12;ciel" -> dia 12, Ciel. Si no existe o no se entiende, no cambia nada.
void load_saved() {
    std::wstring path = saved_path();
    if (path.empty()) return;
    std::FILE* f = _wfopen(path.c_str(), L"rb");
    if (!f) return;
    char buf[64] = {};
    size_t got = std::fread(buf, 1, sizeof buf - 1, f);
    std::fclose(f);
    std::string t(buf, got);
    size_t semi = t.find(';');
    if (semi == std::string::npos) return;
    int day = std::atoi(t.substr(0, semi).c_str());
    std::string rk = t.substr(semi + 1);
    while (!rk.empty() && (rk.back() == '\n' || rk.back() == '\r' || rk.back() == ' ')) rk.pop_back();
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
// Escribe el archivo si dia/ruta cambiaron (temporal + reemplazo, para no dejarlo a medias). Solo desde el hilo.
void save_if_changed(int day, Route r, int* saved_day, Route* saved_route) {
    if (day == *saved_day && r == *saved_route) return;
    if (day < 0 && r == Route::Unknown) return;
    *saved_day = day, *saved_route = r;
    std::wstring path = saved_path(), tmp = saved_path(L".tmp");
    if (path.empty()) return;
    std::FILE* f = _wfopen(tmp.c_str(), L"wb");
    if (!f) return;
    std::string t = std::to_string(day) + ";" + route_key(r) + "\n";
    bool ok = std::fwrite(t.data(), 1, t.size(), f) == t.size();
    ok = std::fclose(f) == 0 && ok;
    if (!ok || !MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING)) DeleteFileW(tmp.c_str());
}

std::string short_text(const std::string& s) { return s.size() > 300 ? s.substr(0, 300) + "..." : s; }

class Client {
public:
    void run() {
        using clock = std::chrono::steady_clock;
        auto next_try = clock::now();
        auto last_send = clock::now() - std::chrono::seconds(kMinSendSeconds);
        uint64_t sent_version = ~0ull;
        Presence sent;
        bool have_sent = false;
        int saved_day = -2;
        Route saved_route = Route::Unknown;
        {
            std::lock_guard<std::mutex> lk(g->m);  // lo que vino del archivo no se vuelve a escribir igual
            saved_day = g->story.day, saved_route = g->story.route;
        }
        for (;;) {
            {
                int day;
                Route r;
                {
                    std::lock_guard<std::mutex> lk(g->m);
                    day = g->story.day, r = g->story.route;
                }
                save_if_changed(day, r, &saved_day, &saved_route);
            }
            auto now = clock::now();
            if (!pipe_.is_open() && now >= next_try) {
                int wait = connect();
                if (!pipe_.is_open()) next_try = clock::now() + std::chrono::seconds(wait);
                else have_sent = false, sent_version = ~0ull;
            }
            if (pipe_.is_open()) {
                service();
                uint64_t ver;
                Presence p;
                clock::time_point changed;
                {
                    std::lock_guard<std::mutex> lk(g->m);
                    ver = g->version;
                    changed = g->changed_at;
                    p = g->story.compose();
                }
                if (pipe_.is_open() && ver != sent_version && clock::now() - last_send >= std::chrono::seconds(kMinSendSeconds) &&
                    clock::now() - changed >= std::chrono::milliseconds(kSettleMs)) {
                    if (!have_sent || p != sent) {
                        if (send_activity(p)) {
                            nx::log("discord: presencia: \"%s\" | \"%s\"", p.details.c_str(), p.state.c_str());
                            sent = p, have_sent = true, last_send = clock::now();
                        }
                    }
                    if (pipe_.is_open()) sent_version = ver;
                }
                if (!pipe_.is_open()) {
                    nx::log("discord: se perdio la conexion; se reintenta cada %d s", kRetrySeconds);
                    next_try = clock::now() + std::chrono::seconds(kRetrySeconds);
                }
            }
            // Despertar: cada 1 s (PING de Discord, reintentos) o antes si cambia el video.
            std::unique_lock<std::mutex> lk(g->m);
            uint64_t seen = g->version;
            g->cv.wait_for(lk, std::chrono::seconds(1), [&] { return g->version != seen; });
        }
    }

private:
    // Devuelve cuanto esperar antes del proximo intento si no conecto.
    int connect() {
        if (!pipe_.open()) {
            if (!said_absent_) {
                said_absent_ = true;
                nx::log("discord: Discord no esta abierto (sin pipe discord-ipc-0..9); se reintenta cada %d s en silencio",
                        kRetrySeconds);
            }
            return kRetrySeconds;
        }
        std::string hs = "{\"v\":1,\"client_id\":\"" + json_escape(g->app_id) + "\"}";
        if (!pipe_.write(kOpHandshake, hs)) return kRetrySeconds;
        auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (pipe_.is_open() && std::chrono::steady_clock::now() < deadline) {
            uint32_t op = 0;
            std::string js;
            if (!pipe_.poll(&op, &js)) {
                std::this_thread::sleep_for(std::chrono::milliseconds(50));
                continue;
            }
            if (op == kOpFrame && js.find("\"READY\"") != std::string::npos) {
                nx::log("discord: conectado (discord-ipc-%d), Application ID %s", pipe_.index(), g->app_id.c_str());
                said_absent_ = false;
                return 0;
            }
            if (op == kOpClose) {
                nx::log("discord: Discord cerro el handshake: %s (reintento en %d s)", short_text(js).c_str(),
                        kRetryAfterCloseSeconds);
                pipe_.close();
                return kRetryAfterCloseSeconds;
            }
            if (op == kOpPing) pipe_.write(kOpPong, js);
        }
        if (pipe_.is_open()) nx::log("discord: sin READY en 5 s; se reintenta en %d s", kRetrySeconds);
        pipe_.close();
        return kRetrySeconds;
    }
    // Mensajes entrantes: PING -> PONG, CLOSE -> cerrar, ERROR -> al log (las primeras veces).
    void service() {
        uint32_t op = 0;
        std::string js;
        while (pipe_.is_open() && pipe_.poll(&op, &js)) {
            if (op == kOpPing) pipe_.write(kOpPong, js);
            else if (op == kOpClose) {
                nx::log("discord: Discord cerro la conexion: %s", short_text(js).c_str());
                pipe_.close();
            } else if (op == kOpFrame && js.find("\"evt\":\"ERROR\"") != std::string::npos && errors_++ < 5) {
                nx::log("discord: error de Discord: %s", short_text(js).c_str());
            }
        }
    }
    bool send_activity(const Presence& p) {
        std::string act = "{\"type\":0";
        if (!p.details.empty()) act += ",\"details\":\"" + json_escape(p.details) + "\"";
        if (!p.state.empty()) act += ",\"state\":\"" + json_escape(p.state) + "\"";
        act += ",\"timestamps\":{\"start\":" + std::to_string(g->start_unix) + "}";  // segundos, como el ejemplo de la doc
        act += ",\"assets\":{\"large_image\":\"" + json_escape(kLargeImage) + "\",\"large_text\":\"" +
               json_escape(kLargeText) + "\"}}";
        std::string msg = "{\"cmd\":\"SET_ACTIVITY\",\"args\":{\"pid\":" + std::to_string(GetCurrentProcessId()) +
                          ",\"activity\":" + act + "},\"nonce\":\"" + std::to_string(++nonce_) + "\"}";
        return pipe_.write(kOpFrame, msg);
    }

    Pipe pipe_;
    bool said_absent_ = false;
    int errors_ = 0;
    uint64_t nonce_ = 0;
};

void thread_main() {
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
    try {
        load_saved();
        Client().run();
    } catch (...) {
        nx::log("discord: el hilo de Discord se detuvo por una excepcion; el juego sigue sin presencia");
    }
}
#endif  // _WIN32

void ensure_started() {
    std::call_once(g_once, [] {
        if (env_off()) {
            nx::log("discord: apagado (NX_DISCORD=0)");
            return;
        }
#ifdef _WIN32
        try {
            g = new State;
            const char* id = std::getenv("NX_DISCORD_APP_ID");
            bool ok = id && *id && std::strlen(id) < 32;
            for (const char* p = id; ok && *p; p++) ok = std::isdigit(static_cast<unsigned char>(*p)) != 0;
            g->app_id = ok ? id : kAppId;
            g->start_unix = process_start_unix();
            std::thread(thread_main).detach();
            g_on = true;
        } catch (...) {
            nx::log("discord: no se pudo iniciar el hilo; el juego sigue sin presencia");
        }
#endif
    });
}

}  // namespace

// ---------------------------------------------------------------------------------------------- avisos del HLE
// Los llama hle_movie.cpp (apply_discord.py) desde los hilos del juego: solo copian y despiertan, sin E/S.
namespace tsuki_discord {

void video_started(const void* key, const char* romfs_rel) {
    try {
        ensure_started();
        if (!g_on) return;
        VideoInfo v = classify(romfs_rel);
        if (v.kind == Kind::Ignore) {
            if (v.name != "tm_ci" && v.name != "phaseout") nx::log("discord: video sin texto en la tabla: %s", v.name.c_str());
            return;
        }
        {
            std::lock_guard<std::mutex> lk(g->m);
            g->story.started(key, v);
            g->version++;
            g->changed_at = std::chrono::steady_clock::now();
        }
        g->cv.notify_one();
    } catch (...) {
    }
}

void video_ended(const void* key) {
    try {
        if (!g_on) return;
        {
            std::lock_guard<std::mutex> lk(g->m);
            if (!key || key != g->story.active_key) return;  // solo el video que cuenta ahora
            g->story.ended(key);
            g->version++;
            g->changed_at = std::chrono::steady_clock::now();
        }
        g->cv.notify_one();
    } catch (...) {
    }
}

}  // namespace tsuki_discord