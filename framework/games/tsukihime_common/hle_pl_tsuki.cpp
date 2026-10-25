// hle_pl_tsuki.cpp - nn::pl::GetSharedFont, que Tsukihime importa y runtime/src/hle_pl.cpp no cubre.
//
// Va en games/tsukihime/ (el CMake del framework compila games/<juego>/*.cpp).
// Sin esto, GetSharedFont cae al sdk real, que pide la lista al servicio pl:u (cmd 5,
// GetSharedFontInOrderOfPriority); el stub no devuelve fuentes y el juego reintenta sin fin.
//
// Las fuentes salen de las HLE del framework (GetSharedFontAddress / GetSharedFontSize), asi se usa la misma
// configuracion: <data root>/fonts/shared_<tipo>.ttf, shared.ttf, NX_SHARED_FONT o una fuente del sistema.
//
// Formato: el framework entrega {0x18029A7F en claro, tamano, TTF}, un atajo que acepta el Decode del SDK 16.
// Tsukihime (SDK 11) pasa lo que devuelve GetSharedFont directo a ScalableFontEngine::LoadFont, que solo acepta
// un TrueType/OpenType sin cabecera: con cualquier cabecera BFTTF (en claro o cifrada) da error 403 y deja al
// motor sin fuente (luego el juego se cae buscando el glifo '?'). GetSharedFont entrega la fuente ya lista.
// NX_PL_FONT_FORMAT elige el formato:
//   ttf (por defecto): direccion y tamano del TrueType, sin cabecera (probado: lo que acepta LoadFont)
//   header:            magic y tamano cifrados, TTF en claro           (probado: error 403)
//   full:              cabecera y TTF cifrados, como el .bfttf         (probado: error 403)
//   plain:             el atajo del framework sin cambios (SDK 16)     (probado: error 403)
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>

#include <nx/runtime.h>

namespace nx {
namespace {

// nn::pl::SharedFontType: 0 estandar (incluye japones), 1 chino simplificado, 2 su extension,
// 3 chino tradicional, 4 coreano, 5 extension de Nintendo (simbolos de botones).
constexpr uint32_t kTypeCount = 6;
constexpr uint32_t kMagic = 0x18029A7F;    // primera palabra de un BFTTF ya descifrado (little endian)
constexpr uint32_t kKey = 0x06186249;      // clave XOR de las fuentes compartidas (por palabra, little endian)

uint32_t bswap32(uint32_t v) {
    return (v >> 24) | ((v >> 8) & 0xFF00) | ((v << 8) & 0xFF0000) | (v << 24);
}

uint64_t call_with_type(CPU& c, const char* name, uint32_t type) {
    HleFn fn = hle_lookup(name);
    if (!fn) fatal("pl: falta la HLE %s", name);
    c.x[0] = type;
    fn(c);
    return c.x[0];
}

std::string format_name() {
    const char* e = std::getenv("NX_PL_FONT_FORMAT");
    std::string f = e ? e : "ttf";
    if (f != "ttf" && f != "header" && f != "full" && f != "plain") f = "ttf";
    return f;
}

struct Packed {
    uint64_t addr = 0, size = 0;
};

// Copia la fuente del framework ({kMagic, ttf_size, ttf}) en el formato elegido. Una copia por tipo, cacheada.
Packed packed_font(CPU& c, uint32_t type) {
    static std::mutex lock;
    static Packed cache[kTypeCount];
    static const std::string fmt = format_name();
    std::lock_guard lk(lock);
    Packed& p = cache[type];
    if (p.addr) return p;

    uint64_t src = call_with_type(c, "_ZN2nn2pl20GetSharedFontAddressENS0_14SharedFontTypeE", type);
    uint64_t total = call_with_type(c, "_ZN2nn2pl17GetSharedFontSizeENS0_14SharedFontTypeE", type);
    if (fmt == "plain" || !src || total < 8) {
        p = {src, total};
        return p;
    }
    const uint32_t* in = reinterpret_cast<const uint32_t*>(src);
    uint32_t ttf_size = in[1];
    if (fmt == "ttf") {  // el TrueType tal cual, saltando la cabecera del framework
        p = {src + 8, ttf_size};
        log("pl: fuente %u: TTF sin cabecera, %u bytes, empieza con %08x", type, ttf_size, in[2]);
        return p;
    }
    uint64_t words = (uint64_t(ttf_size) + 3) / 4;
    p.size = 8 + words * 4;
    p.addr = kernel_alloc(p.size);
    auto* out = reinterpret_cast<uint32_t*>(p.addr);
    std::memcpy(out + 2, in + 2, ttf_size);
    if (ttf_size % 4) std::memset(reinterpret_cast<uint8_t*>(out + 2) + ttf_size, 0, 4 - ttf_size % 4);
    out[0] = kMagic ^ kKey;  // 0x1E1AF836
    if (fmt == "header") {
        out[1] = bswap32(ttf_size) ^ kKey;  // tamano big endian, cifrado
    } else {                                // full: como el .bfttf del firmware
        out[1] = ttf_size ^ kKey;
        for (uint64_t i = 0; i < words; i++) out[2 + i] ^= kKey;
    }
    log("pl: fuente %u re-empaquetada (%s): %u bytes de TTF, cabecera %08x %08x", type, fmt.c_str(), ttf_size,
        out[0], out[1]);
    return p;
}

// bool GetSharedFont(int* outCount, SharedFontData* outFonts, int maxCount, settings::LanguageCode)
// SharedFontData: {SharedFontType type (int32, con relleno); const void* address; size_t size} = 0x18 bytes.
// Para japones el orden de prioridad es el mismo que el de los tipos: estandar primero.
void GetSharedFont(CPU& c) {
    auto* out_count = reinterpret_cast<int32_t*>(c.x[0]);
    uint64_t out = c.x[1];
    int32_t max = int32_t(c.x[2]);
    uint64_t saved[8];
    std::memcpy(saved, c.x, sizeof saved);

    int32_t n = 0;
    for (uint32_t type = 0; type < kTypeCount && n < max && out; type++) {
        Packed f = packed_font(c, type);
        uint8_t* e = reinterpret_cast<uint8_t*>(out + uint64_t(n) * 0x18);
        std::memset(e, 0, 0x18);
        std::memcpy(e, &type, 4);
        std::memcpy(e + 8, &f.addr, 8);
        std::memcpy(e + 0x10, &f.size, 8);
        n++;
    }

    std::memcpy(c.x, saved, sizeof saved);
    if (out_count) *out_count = n;
    static bool logged = false;
    if (!logged) log("pl: GetSharedFont -> %d fuentes", n), logged = true;
    // Devuelve bool (true = fuentes cargadas), no un Result: el juego reintenta cada 100 ms mientras el bit 0
    // sea 0 (main+ceab4: GetSharedFont; tbnz w0, #0 -> listo; si no, SleepThread(100 ms) y vuelve a llamar).
    c.x[0] = n > 0 ? 1 : 0;
}

}  // namespace

NX_HLE("_ZN2nn2pl13GetSharedFontEPiPNS0_14SharedFontDataEiNS_8settings12LanguageCodeE", GetSharedFont);

}  // namespace nx