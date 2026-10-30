// hud_glyphs.cpp - iconos de botones del HUD segun el ultimo dispositivo usado: Xbox, PlayStation, Switch o teclado.
// AP_HUD_GLYPHS (apply_hud_glyphs.py: define NX_HUD_GLYPHS en tsukihime_common.cmake y engancha la entrada y la subida
// de texturas). Sin la define este archivo no compila nada.
//
// - El juego dibuja los iconos de la barra de ayuda (Opciones, Save/Load, pestana de controles) desde un atlas BC7 de
//   384x256 (help_key, en allui). Se reemplaza el atlas entero mientras se sube a la GPU (nvn::g_upload_filter): la
//   memoria del juego no se toca. Al cargar, el juego copia ese atlas a un render target propio (shader 388795e5) y
//   los menus dibujan desde la copia: el runtime la marca como derivada y, al cambiar de dispositivo (se sube
//   nvn::g_upload_filter_generation), la reescribe con lo que da nvn::g_derived_filter (el atlas decodificado a RGBA8).
//   Se supone que esa copia deja los colores igual; NX_HUD_DUMP=1 con NX_SNAP_RAW=1 la vuelca para comprobarlo.
// - La tabla de controles (Opciones > 操作説明, conf_manual*, 1416x720) tambien se copia a un render target: esa copia
//   se arma con manual_<hash>.bin (build_manual.py: la tabla del usuario sin la columna de iconos y los iconos de cada
//   fila por dispositivo; el teclado, con la primera tecla de cada boton). Switch: la tabla original, sin tocar.
// - Solo se reemplaza una textura cuyo contenido (FNV-1a 64 del nivel 0 sin tiling) esta en <data root>/hud/
//   manifest.txt, que build_hud_assets.py escribe al recompilar con los help_key del romfs y de las capas de mods.
// - Switch: el atlas original. Xbox y PlayStation: help_key_xbox.bc7 / help_key_ps.bc7 (mismo layout, por posicion:
//   el A de Switch, a la derecha, es la B de Xbox y el circulo). Teclado: se arma al primer uso con keyboard_tiles.bin
//   y la primera tecla que input.ini (o los mapeos por defecto) da a cada boton; el mouse muestra el teclado.
// - NX_HUD_GLYPHS=auto (por defecto: el ultimo dispositivo con una entrada real) | switch | xbox | ps | keyboard (fijo)
//   | off (no toca nada). Arranca en teclado hasta la primera entrada. Si falta un archivo, ese dispositivo muestra
//   los iconos originales (se avisa una vez en el log).
#ifdef NX_HUD_GLYPHS

#include "hud_glyphs.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <memory>
#include <fstream>
#include <mutex>
#include <sstream>
#include <string>
#include <vector>

#include <nx/runtime.h>

#include "input_tsuki.h"
#include "nvn/nvn.h"  // Texture, g_upload_filter, g_derived_filter, g_upload_filter_generation (apply_hud_glyphs.py)
#include "platform.h"

namespace nx::nvn {  // nvn_bc.cpp (nvn_vk.h): bloques BC -> texeles, el mismo decodificador del runtime
void bc_decode(uint32_t nvn_format, const uint8_t* blocks, uint32_t width, uint32_t height, uint32_t slices, uint8_t* out);
}

namespace nx::tsuki::hud {
namespace {

constexpr uint32_t kAtlasW = 384, kAtlasH = 256;
constexpr size_t kAtlasBytes = size_t(kAtlasW / 4) * (kAtlasH / 4) * 16;
constexpr uint16_t kUnassigned = 0xF000, kArrows = 0xF001, kWasd = 0xF002, kClear = 0xF0FF;
constexpr uint32_t kNvnBc7 = 0x4d;  // bc_decode solo distingue el tipo de bloque (sRGB o no da igual)

const char* nombre(Device d) {
    switch (d) {
    case Device::kSwitch: return "Switch";
    case Device::kXbox: return "Xbox";
    case Device::kPs: return "PlayStation";
    case Device::kKeyboard: return "teclado";
    }
    return "?";
}

enum class Modo { kAuto, kFijo, kOff };
struct Config {
    Modo modo = Modo::kAuto;
    Device fijo = Device::kKeyboard;
};
const Config& config() {
    static const Config c = [] {
        Config x;
        const char* e = std::getenv("NX_HUD_GLYPHS");
        std::string v = e ? e : "";
        for (char& ch : v) ch = char(std::tolower(static_cast<unsigned char>(ch)));
        if (v.empty() || v == "auto") x.modo = Modo::kAuto;
        else if (v == "off" || v == "0") x.modo = Modo::kOff;
        else if (v == "switch") x.modo = Modo::kFijo, x.fijo = Device::kSwitch;
        else if (v == "xbox") x.modo = Modo::kFijo, x.fijo = Device::kXbox;
        else if (v == "ps" || v == "playstation") x.modo = Modo::kFijo, x.fijo = Device::kPs;
        else if (v == "keyboard" || v == "teclado") x.modo = Modo::kFijo, x.fijo = Device::kKeyboard;
        else log("hud: NX_HUD_GLYPHS=%s no se entiende (auto, switch, xbox, ps, keyboard, off): auto", e);
        return x;
    }();
    return c;
}

std::atomic<int> g_device{int(Device::kKeyboard)};

Device actual() { return config().modo == Modo::kFijo ? config().fijo : Device(g_device.load(std::memory_order_acquire)); }

uint64_t fnv1a64(const uint8_t* p, size_t n) {
    uint64_t h = 0xCBF29CE484222325ull;
    for (size_t k = 0; k < n; k++) h = (h ^ p[k]) * 0x100000001B3ull;
    return h;
}

std::filesystem::path carpeta() { return std::filesystem::u8path(plat::data_root()) / "hud"; }

bool leer(const std::filesystem::path& p, std::vector<uint8_t>& out) {
    std::ifstream f(p, std::ios::binary);
    if (!f) return false;
    out.assign(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
    return true;
}

// ---- teclado: atlas armado con keyboard_tiles.bin (formato en build_keyboard_tiles.py) ----------------------------
template <class T>
T rd(const std::vector<uint8_t>& b, size_t at) {
    T v{};
    if (at + sizeof(T) <= b.size()) std::memcpy(&v, b.data() + at, sizeof(T));
    return v;
}

struct Tiles {
    struct Celda {
        uint8_t kind = 0, count = 0;
        uint16_t full = 0, parts[4] = {};
        std::string botones[4];
    };
    struct Slot {
        uint16_t x = 0, y = 0, w = 0, h = 0;
    };
    std::vector<uint8_t> file;
    std::vector<Celda> celdas;
    std::vector<Slot> slots;
    std::vector<std::array<uint32_t, 3>> tiles;  // (slot << 16 | tecla, offset, size), ordenadas como en el archivo

    bool cargar(const std::filesystem::path& p) {
        if (!leer(p, file) || file.size() < 20 || std::memcmp(file.data(), "NXHK", 4) != 0 || rd<uint32_t>(file, 4) != 1)
            return false;
        if (rd<uint16_t>(file, 8) != kAtlasW || rd<uint16_t>(file, 10) != kAtlasH) return false;
        const uint16_t nc = rd<uint16_t>(file, 12), ns = rd<uint16_t>(file, 14);
        const uint32_t nt = rd<uint32_t>(file, 16);
        if (20 + size_t(nc) * 44 + size_t(ns) * 8 + size_t(nt) * 12 > file.size()) return false;
        size_t at = 20;
        for (uint16_t k = 0; k < nc; k++, at += 44) {
            Celda c;
            c.kind = file[at], c.count = std::min<uint8_t>(file[at + 1], 4);
            c.full = rd<uint16_t>(file, at + 2);
            for (int i = 0; i < 4; i++) c.parts[i] = rd<uint16_t>(file, at + 4 + 2 * i);
            for (int i = 0; i < 4; i++) {
                const char* s = reinterpret_cast<const char*>(file.data() + at + 12 + 8 * i);
                const void* fin = std::memchr(s, 0, 8);
                c.botones[i].assign(s, fin ? static_cast<const char*>(fin) - s : 8);
            }
            celdas.push_back(c);
        }
        for (uint16_t k = 0; k < ns; k++, at += 8)
            slots.push_back({rd<uint16_t>(file, at), rd<uint16_t>(file, at + 2), rd<uint16_t>(file, at + 4),
                             rd<uint16_t>(file, at + 6)});
        for (uint32_t k = 0; k < nt; k++, at += 12)
            tiles.push_back({uint32_t(rd<uint16_t>(file, at)) << 16 | rd<uint16_t>(file, at + 2), rd<uint32_t>(file, at + 4),
                             rd<uint32_t>(file, at + 8)});
        return at <= file.size() && !celdas.empty();
    }

    const std::array<uint32_t, 3>* buscar(uint16_t slot, uint16_t tecla) const {
        const uint32_t key = uint32_t(slot) << 16 | tecla;
        for (const auto& t : tiles)
            if (t[0] == key) return &t;
        return nullptr;
    }

    // Copia los bloques de una pieza en el atlas (filas de bloques, ancho del slot / 4).
    void poner(std::vector<uint8_t>& atlas, uint16_t slot, uint16_t tecla) const {
        if (slot >= slots.size()) return;
        const auto* t = buscar(slot, tecla);
        if (!t && tecla != kClear) t = buscar(slot, kUnassigned);  // una tecla sin icono propio: la de "sin asignar"
        const Slot& s = slots[slot];
        const size_t tw = s.w / 4, th = s.h / 4;
        if (!t || (*t)[2] < tw * th * 16 || size_t((*t)[1]) + (*t)[2] > file.size()) return;
        const uint8_t* src = file.data() + (*t)[1];
        for (size_t r = 0; r < th; r++) {
            const size_t by = s.y / 4 + r, bx = s.x / 4;
            if (by >= kAtlasH / 4 || bx + tw > kAtlasW / 4) return;
            std::memcpy(atlas.data() + (by * (kAtlasW / 4) + bx) * 16, src + r * tw * 16, tw * 16);
        }
    }
};

uint16_t flecha(const std::string& b) { return b == "UP" ? 0x26 : b == "RIGHT" ? 0x27 : b == "LEFT" ? 0x25 : b == "DOWN" ? 0x28 : 0; }
uint16_t wasd(const std::string& b) { return b == "UP" ? 'W' : b == "RIGHT" ? 'D' : b == "LEFT" ? 'A' : b == "DOWN" ? 'S' : 0; }

std::vector<uint8_t> armar_teclado(const Tiles& lib) {
    std::vector<uint8_t> atlas(kAtlasBytes, 0);
    for (size_t k = 0; k < atlas.size(); k += 16) atlas[k] = 0x40;  // BC7 modo 6 con todo en 0: transparente
    auto tecla = [](const std::string& b) {
        int vk = first_key(b);
        return vk > 0 && vk < 0xF000 ? uint16_t(vk) : kUnassigned;
    };
    for (const auto& c : lib.celdas) {
        if (c.kind == 0) {
            lib.poner(atlas, c.full, tecla(c.botones[0]));
            continue;
        }
        lib.poner(atlas, c.full, kClear);
        uint16_t ks[4] = {};
        for (int i = 0; i < c.count; i++) ks[i] = tecla(c.botones[i]);
        if (c.kind == 3) {  // cruceta: el bloque de flechas o WASD si son esas teclas, si no las cuatro en 2x2
            bool flechas = c.count == 4, w = c.count == 4;
            for (int i = 0; i < c.count; i++) flechas &= ks[i] == flecha(c.botones[i]), w &= ks[i] == wasd(c.botones[i]);
            if (flechas || w) {
                lib.poner(atlas, c.full, flechas ? kArrows : kWasd);
                continue;
            }
        }
        for (int i = 0; i < c.count; i++) lib.poner(atlas, c.parts[i], ks[i]);
    }
    return atlas;
}

// ---- recursos ----------------------------------------------------------------------------------------------------
// Tabla de controles (conf_manual*, manual_<hash>.bin de build_manual.py): la tabla del usuario sin la columna de
// iconos (BC7) y los iconos a estampar en cada fila, por dispositivo.
struct Icono {
    uint16_t w = 0, h = 0;
    std::vector<uint8_t> rgba;
};
struct Tabla {
    uint16_t w = 0, h = 0;
    struct Fila {
        uint16_t x = 0, y = 0, w = 0, h = 0;
        std::string boton;
    };
    std::vector<Fila> filas;
    std::vector<uint8_t> base;  // BC7
    std::vector<Icono> xbox, ps;
    std::vector<std::pair<uint16_t, Icono>> teclas;  // vk (o kUnassigned) -> icono

    bool cargar(const std::filesystem::path& p) {
        std::vector<uint8_t> f;
        if (!leer(p, f) || f.size() < 16 || std::memcmp(f.data(), "NXHM", 4) != 0 || rd<uint32_t>(f, 4) != 1) return false;
        w = rd<uint16_t>(f, 8), h = rd<uint16_t>(f, 10);
        const uint16_t nf = rd<uint16_t>(f, 12), nk = rd<uint16_t>(f, 14);
        size_t at = 16;
        for (uint16_t k = 0; k < nf; k++, at += 16) {
            if (at + 16 > f.size()) return false;
            Fila fila{rd<uint16_t>(f, at), rd<uint16_t>(f, at + 2), rd<uint16_t>(f, at + 4), rd<uint16_t>(f, at + 6), {}};
            const char* s = reinterpret_cast<const char*>(f.data() + at + 8);
            const void* fin = std::memchr(s, 0, 8);
            fila.boton.assign(s, fin ? static_cast<const char*>(fin) - s : 8);
            filas.push_back(fila);
        }
        const uint32_t nb = rd<uint32_t>(f, at);
        at += 4;
        if (nb != size_t(w / 4) * (h / 4) * 16 || at + nb > f.size()) return false;
        base.assign(f.begin() + std::ptrdiff_t(at), f.begin() + std::ptrdiff_t(at + nb));
        at += nb;
        auto icono = [&](Icono& ic) {
            ic.w = rd<uint16_t>(f, at), ic.h = rd<uint16_t>(f, at + 2);
            const size_t n = size_t(ic.w) * ic.h * 4;
            at += 4;
            if (at + n > f.size()) return false;
            ic.rgba.assign(f.begin() + std::ptrdiff_t(at), f.begin() + std::ptrdiff_t(at + n));
            at += n;
            return true;
        };
        xbox.resize(nf), ps.resize(nf);
        for (auto& ic : xbox)
            if (!icono(ic)) return false;
        for (auto& ic : ps)
            if (!icono(ic)) return false;
        for (uint32_t k = 0; k <= nk; k++) {  // las teclas y al final la de "sin asignar"
            const uint16_t vk = rd<uint16_t>(f, at);
            at += 2;
            Icono ic;
            if (!icono(ic)) return false;
            teclas.push_back({vk, std::move(ic)});
        }
        return true;
    }

    const Icono* tecla(uint16_t vk) const {
        for (const auto& t : teclas)
            if (t.first == vk) return &t.second;
        for (const auto& t : teclas)
            if (t.first == kUnassigned) return &t.second;
        return nullptr;
    }

    // Icono centrado en la fila, mezclado sobre la tabla (alfa directo, bytes sRGB como la textura).
    void estampar(std::vector<uint8_t>& rgba, const Fila& f, const Icono& ic) const {
        const int x0 = int(f.x) + (int(f.w) - int(ic.w)) / 2, y0 = int(f.y) + (int(f.h) - int(ic.h)) / 2;
        for (int y = 0; y < ic.h; y++)
            for (int x = 0; x < ic.w; x++) {
                const int tx = x0 + x, ty = y0 + y;
                if (tx < 0 || ty < 0 || tx >= w || ty >= h) continue;
                const uint8_t* s = &ic.rgba[(size_t(y) * ic.w + x) * 4];
                uint8_t* d = &rgba[(size_t(ty) * w + tx) * 4];
                const float sa = s[3] / 255.0f, da = d[3] / 255.0f, oa = sa + da * (1 - sa);
                if (oa <= 0) continue;
                for (int c = 0; c < 3; c++) d[c] = uint8_t((s[c] * sa + d[c] * da * (1 - sa)) / oa + 0.5f);
                d[3] = uint8_t(oa * 255 + 0.5f);
            }
    }
};

struct Conocida {
    std::string nombre;
    uint32_t w = 0, h = 0;
    uint64_t hash = 0;
};

struct Recursos {
    std::vector<uint64_t> hashes;  // help_key 384x256
    std::vector<Conocida> tablas;  // conf_manual*
    std::vector<uint8_t> xbox, ps, teclado;
};

const Recursos& recursos() {
    static const Recursos r = [] {
        Recursos x;
        const auto dir = carpeta();
        std::ifstream m(dir / "manifest.txt");
        std::string linea;
        while (std::getline(m, linea)) {
            std::istringstream s(linea);
            std::string nombre_tex, fmt, hash;
            uint32_t w = 0, h = 0;
            if (!(s >> nombre_tex >> w >> h >> fmt >> hash)) continue;
            const uint64_t v = std::strtoull(hash.c_str(), nullptr, 16);
            if (nombre_tex == "help_key" && w == kAtlasW && h == kAtlasH) x.hashes.push_back(v);
            else if (nombre_tex.rfind("conf_manual", 0) == 0 && w % 4 == 0 && h % 4 == 0) x.tablas.push_back({nombre_tex, w, h, v});
        }
        if (x.hashes.empty() && x.tablas.empty()) {
            log("hud: sin %s (build_hud_assets.py, al recompilar): los iconos quedan los del juego",
                (dir / "manifest.txt").string().c_str());
            return x;
        }
        auto atlas = [&](const char* archivo, std::vector<uint8_t>& out) {
            if (!leer(dir / archivo, out) || out.size() != kAtlasBytes) {
                log("hud: falta %s o no mide %zu bytes: ese dispositivo muestra los iconos del juego", archivo, kAtlasBytes);
                out.clear();
            }
        };
        atlas("help_key_xbox.bc7", x.xbox);
        atlas("help_key_ps.bc7", x.ps);
        Tiles lib;
        if (lib.cargar(dir / "keyboard_tiles.bin"))
            x.teclado = armar_teclado(lib);
        else
            log("hud: falta keyboard_tiles.bin o no es valido: el teclado muestra los iconos del juego");
        log("hud: iconos por dispositivo (NX_HUD_GLYPHS=%s): %zu atlas y %zu tablas conocidas, Xbox %s, PlayStation %s, teclado %s",
            std::getenv("NX_HUD_GLYPHS") ? std::getenv("NX_HUD_GLYPHS") : "auto", x.hashes.size(), x.tablas.size(),
            x.xbox.empty() ? "no" : "si", x.ps.empty() ? "no" : "si", x.teclado.empty() ? "no" : "si");
        return x;
    }();
    return r;
}

// Las tablas se leen al primer uso (cada una pesa ~2 MB).
const Tabla* tabla(uint64_t hash) {
    static std::mutex lock;
    static std::vector<std::pair<uint64_t, std::unique_ptr<Tabla>>> cache;
    std::lock_guard lk(lock);
    for (const auto& c : cache)
        if (c.first == hash) return c.second.get();
    char nombre_archivo[48];
    std::snprintf(nombre_archivo, sizeof nombre_archivo, "manual_%016llx.bin", (unsigned long long)hash);
    auto t = std::make_unique<Tabla>();
    if (!t->cargar(carpeta() / nombre_archivo)) {
        log("hud: falta %s o no es valido: la tabla de controles queda la del juego", nombre_archivo);
        t.reset();
    }
    cache.push_back({hash, std::move(t)});
    return cache.back().second.get();
}

// Los originales (Switch) tal como los subio el juego, guardados en memoria la primera vez para poder volver a ellos
// en la copia derivada. No se escriben a disco.
std::mutex g_original_lock;
std::vector<std::pair<uint64_t, std::vector<uint8_t>>> g_originales;

void guardar_original(uint64_t hash, const uint8_t* p, size_t n) {
    std::lock_guard lk(g_original_lock);
    for (const auto& o : g_originales)
        if (o.first == hash) return;
    g_originales.push_back({hash, std::vector<uint8_t>(p, p + n)});
}

std::vector<uint8_t> original(uint64_t hash) {
    std::lock_guard lk(g_original_lock);
    for (const auto& o : g_originales)
        if (o.first == hash) return o.second;
    return {};
}

const std::vector<uint8_t>* bloques(Device d, const Recursos& r) {
    const std::vector<uint8_t>* src = d == Device::kXbox ? &r.xbox : d == Device::kPs ? &r.ps : d == Device::kKeyboard ? &r.teclado : nullptr;
    return src && !src->empty() ? src : nullptr;
}

bool contiene(const std::vector<uint64_t>& v, uint64_t h) {
    for (uint64_t k : v)
        if (k == h) return true;
    return false;
}
const Conocida* tabla_conocida(const Recursos& r, uint64_t hash, uint32_t w, uint32_t h) {
    for (const auto& t : r.tablas)
        if (t.hash == hash && t.w == w && t.h == h) return &t;
    return nullptr;
}

// Llamado por la subida de cada textura block-linear (hilo de render): sale enseguida (0) si no es un help_key ni una
// tabla de controles. Si lo es, devuelve su hash como etiqueta: la imagen y la copia que el juego haga de ella siguen
// al dispositivo. El help_key se reemplaza aqui mismo; la tabla, solo en su copia (derivada).
uint64_t filtro(const nvn::Texture& t, uint8_t* nivel0, uint64_t size) {
    const Recursos& r = recursos();
    const bool atlas = t.width == kAtlasW && t.height == kAtlasH && size == kAtlasBytes && !r.hashes.empty();
    bool posible_tabla = false;
    for (const auto& c : r.tablas) posible_tabla |= c.w == t.width && c.h == t.height && size == size_t(c.w / 4) * (c.h / 4) * 16;
    if (!atlas && !posible_tabla) return 0;
    const uint64_t h = fnv1a64(nivel0, size_t(size));
    if (posible_tabla && tabla_conocida(r, h, t.width, t.height)) {
        guardar_original(h, nivel0, size_t(size));
        static std::atomic<int> logs{0};
        if (logs.fetch_add(1) < 16) log("hud: tabla de controles %ux%u reconocida (%016llx)", t.width, t.height, (unsigned long long)h);
        return h;
    }
    if (!atlas) return 0;
    if (!contiene(r.hashes, h)) {
        static std::atomic<int> otros{0};
        if (otros.fetch_add(1) < 4) log("hud: textura de %ux%u que no es un atlas conocido (%016llx): no se toca", t.width, t.height, (unsigned long long)h);
        return 0;
    }
    guardar_original(h, nivel0, kAtlasBytes);
    const Device d = actual();
    static std::atomic<int> logs{0};
    if (logs.fetch_add(1) < 64) log("hud: atlas de iconos %ux%u subido con los de %s", t.width, t.height, nombre(d));
    if (const auto* src = bloques(d, r)) std::memcpy(nivel0, src->data(), kAtlasBytes);
    return h;
}

// La copia que el juego hizo (render target RGBA8 del mismo tamano) pide su contenido nuevo, texel por texel (la copia
// del juego deja los colores igual): el atlas del dispositivo, o la tabla sin iconos con los del dispositivo encima.
bool derivada(uint64_t tag, uint32_t w, uint32_t h, std::vector<uint8_t>& rgba) {
    const Recursos& r = recursos();
    const Device d = actual();
    if (w == kAtlasW && h == kAtlasH && contiene(r.hashes, tag)) {
        std::vector<uint8_t> blocks = bloques(d, r) ? *bloques(d, r) : original(tag);
        if (blocks.size() != kAtlasBytes) return false;
        rgba.resize(size_t(w) * h * 4);
        nvn::bc_decode(kNvnBc7, blocks.data(), w, h, 1, rgba.data());
        static std::atomic<int> logs{0};
        if (logs.fetch_add(1) < 64) log("hud: copia del atlas en la UI reescrita con los iconos de %s", nombre(d));
        return true;
    }
    if (!tabla_conocida(r, tag, w, h)) return false;
    const Tabla* t = d == Device::kSwitch ? nullptr : tabla(tag);
    rgba.resize(size_t(w) * h * 4);
    if (!t || t->w != w || t->h != h) {  // Switch (o sin datos): la tabla original
        std::vector<uint8_t> blocks = original(tag);
        if (blocks.size() != size_t(w / 4) * (h / 4) * 16) return false;
        nvn::bc_decode(kNvnBc7, blocks.data(), w, h, 1, rgba.data());
    } else {
        nvn::bc_decode(kNvnBc7, t->base.data(), w, h, 1, rgba.data());
        for (size_t k = 0; k < t->filas.size(); k++) {
            const Icono* ic = nullptr;
            if (d == Device::kXbox) ic = k < t->xbox.size() ? &t->xbox[k] : nullptr;
            else if (d == Device::kPs) ic = k < t->ps.size() ? &t->ps[k] : nullptr;
            else {
                const int vk = first_key(t->filas[k].boton);
                ic = t->tecla(vk > 0 && vk < 0xF000 ? uint16_t(vk) : kUnassigned);
            }
            if (ic) t->estampar(rgba, t->filas[k], *ic);
        }
    }
    static std::atomic<int> logs{0};
    if (logs.fetch_add(1) < 64) log("hud: tabla de controles reescrita con los iconos de %s", nombre(d));
    return true;
}

const bool g_registrado = [] {
    if (config().modo != Modo::kOff) {
        nvn::g_derived_filter.store(&derivada, std::memory_order_release);
        nvn::g_upload_filter.store(&filtro, std::memory_order_release);
    }
    return true;
}();

}  // namespace

void note_input(Device d) {
    if (config().modo != Modo::kAuto) return;
    const int antes = g_device.exchange(int(d), std::memory_order_acq_rel);
    if (antes == int(d)) return;
    log("hud: iconos de %s (ultima entrada)", nombre(d));
    nvn::g_upload_filter_generation.fetch_add(1, std::memory_order_acq_rel);  // el atlas vuelve a subirse al usarse
}

}  // namespace nx::tsuki::hud

#endif  // NX_HUD_GLYPHS
