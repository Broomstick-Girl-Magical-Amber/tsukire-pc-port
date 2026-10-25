// fontll_trace.cpp - Diagnostico: registra lo que pasa en nn::fontll (el motor de fuentes del sdk).
//
// Va en games/tsukihime/. Cada envoltorio llama a la funcion real del sdk (por find_symbol, igual que
// hle_audio.cpp con AudioRenderSystem::Update) y deja en el log argumentos, resultado y GetError().
// No cambia el comportamiento: solo observa. Se apaga con NX_FONTLL_TRACE=0.
//
// Motivo: el juego se cae en main+144de0 porque un glifo (U+003F '?') nunca se genero; hay que saber que
// fuente fallo al cargarse o al rasterizar.
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <string>

#include <nx/runtime.h>

namespace nx {
namespace {

bool enabled() {
    static bool on = [] {
        const char* e = std::getenv("NX_FONTLL_TRACE");
        return !(e && e[0] == '0');
    }();
    return on;
}

uint64_t real(const char* name) {
    uint64_t a = find_symbol(name);
    if (!a) fatal("fontll_trace: %s no esta exportada en sdk", name);
    return a;
}

constexpr const char* kGetError = "_ZN2nn6fontll18ScalableFontEngine8GetErrorEv";

// GetError() del motor `self`, sin tocar los registros del llamador.
int32_t engine_error(CPU& c, uint64_t self) {
    static uint64_t fn = real(kGetError);
    uint64_t saved[31];
    std::memcpy(saved, c.x, sizeof saved);
    c.x[0] = self;
    call(c, fn);
    int32_t err = int32_t(c.x[0]);
    std::memcpy(c.x, saved, sizeof saved);
    return err;
}

std::string cstr_safe(uint64_t p, size_t max = 64) {
    if (!p) return "(null)";
    const char* s = reinterpret_cast<const char*>(p);
    size_t n = 0;
    while (n < max && s[n]) n++;
    return std::string(s, n);
}

uint32_t word(uint64_t p, int i) {
    if (!p) return 0;
    uint32_t v;
    std::memcpy(&v, reinterpret_cast<const void*>(p + 4 * i), 4);
    return v;
}

#define BUDGET(n)                       \
    static std::atomic<int> budget{n}; \
    bool log_it = enabled() && budget.fetch_sub(1) > 0

// ScalableFontEngineHelper::Decode(const void* data, uint size): BFTTF -> TTF (estatica, sin this).
void Decode(CPU& c) {
    static uint64_t fn = real("_ZN2nn6fontll24ScalableFontEngineHelper6DecodeEPKvj");
    uint64_t data = c.x[0], size = c.x[1];
    uint32_t w0 = word(data, 0), w1 = word(data, 1);
    call(c, fn);
    BUDGET(20);
    if (log_it)
        log("fontll: Decode(data %llx [%08x %08x], size %#llx) -> %#llx", (unsigned long long)data, w0, w1,
            (unsigned long long)size, (unsigned long long)c.x[0]);
}

// Initialize(void* work, uint size)
void Initialize(CPU& c) {
    static uint64_t fn = real("_ZN2nn6fontll18ScalableFontEngine10InitializeEPvj");
    uint64_t self = c.x[0], size = c.x[2];
    call(c, fn);
    uint64_t rc = c.x[0];
    BUDGET(20);
    if (log_it)
        log("fontll: [%llx] Initialize(work %#llx bytes) -> %#llx, error %d", (unsigned long long)self,
            (unsigned long long)size, (unsigned long long)rc, engine_error(c, self));
    c.x[0] = rc;
}

// LoadFont(char* name_out, const void* data, uint index, uint size?)
void LoadFont(CPU& c) {
    static uint64_t fn = real("_ZN2nn6fontll18ScalableFontEngine8LoadFontEPcPKvjj");
    uint64_t self = c.x[0], name = c.x[1], data = c.x[2], a3 = c.x[3], a4 = c.x[4];
    uint32_t w0 = word(data, 0), w1 = word(data, 1);
    call(c, fn);
    uint64_t rc = c.x[0];
    BUDGET(40);
    if (log_it)
        log("fontll: [%llx] LoadFont(data %llx [%08x %08x], %#llx, %#llx) -> %#llx, name \"%s\", error %d",
            (unsigned long long)self, (unsigned long long)data, w0, w1, (unsigned long long)a3,
            (unsigned long long)a4, (unsigned long long)rc, cstr_safe(name).c_str(), engine_error(c, self));
    c.x[0] = rc;
}

// SetFont(char* name)
void SetFont(CPU& c) {
    static uint64_t fn = real("_ZN2nn6fontll18ScalableFontEngine7SetFontEPc");
    uint64_t self = c.x[0], name = c.x[1];
    std::string n = cstr_safe(name);
    call(c, fn);
    uint64_t rc = c.x[0];
    BUDGET(40);
    if (log_it)
        log("fontll: [%llx] SetFont(\"%s\") -> %#llx, error %d", (unsigned long long)self, n.c_str(),
            (unsigned long long)rc, engine_error(c, self));
    c.x[0] = rc;
}

// SetScale(int, int, int, int)
void SetScale(CPU& c) {
    static uint64_t fn = real("_ZN2nn6fontll18ScalableFontEngine8SetScaleEiiii");
    uint64_t self = c.x[0];
    int32_t a = int32_t(c.x[1]), b = int32_t(c.x[2]), d = int32_t(c.x[3]), e = int32_t(c.x[4]);
    call(c, fn);
    uint64_t rc = c.x[0];
    BUDGET(10);
    if (log_it)
        log("fontll: [%llx] SetScale(%#x, %#x, %#x, %#x) -> %#llx, error %d", (unsigned long long)self, a, b, d, e,
            (unsigned long long)rc, engine_error(c, self));
    c.x[0] = rc;
}

// AcquireGlyphmap(uint code, ushort mode) -> GlyphMap* (NULL si falla)
void AcquireGlyphmap(CPU& c) {
    static uint64_t fn = real("_ZN2nn6fontll18ScalableFontEngine15AcquireGlyphmapEjt");
    static std::atomic<int> ok_budget{5};
    uint64_t self = c.x[0];
    uint32_t code = uint32_t(c.x[1]), mode = uint32_t(c.x[2]) & 0xFFFF;
    call(c, fn);
    uint64_t rc = c.x[0];
    if (enabled() && ((rc == 0 && [] { static std::atomic<int> b{30}; return b.fetch_sub(1) > 0; }()) ||
                      (rc != 0 && ok_budget.fetch_sub(1) > 0)))
        log("fontll: [%llx] AcquireGlyphmap(U+%04X, mode %u) -> %llx, error %d", (unsigned long long)self, code,
            mode, (unsigned long long)rc, engine_error(c, self));
    c.x[0] = rc;
}

// CheckGlyphExist(uint code) -> bool (se registran los primeros "no existe")
void CheckGlyphExist(CPU& c) {
    static uint64_t fn = real("_ZN2nn6fontll18ScalableFontEngine15CheckGlyphExistEj");
    uint64_t self = c.x[0];
    uint32_t code = uint32_t(c.x[1]);
    call(c, fn);
    uint64_t rc = c.x[0];
    if (enabled() && (rc & 1) == 0) {
        static std::atomic<int> b{20};
        if (b.fetch_sub(1) > 0)
            log("fontll: [%llx] CheckGlyphExist(U+%04X) -> no", (unsigned long long)self, code);
    }
    c.x[0] = rc;
}

}  // namespace

NX_HLE("_ZN2nn6fontll24ScalableFontEngineHelper6DecodeEPKvj", Decode);
NX_HLE("_ZN2nn6fontll18ScalableFontEngine10InitializeEPvj", Initialize);
NX_HLE("_ZN2nn6fontll18ScalableFontEngine8LoadFontEPcPKvjj", LoadFont);
NX_HLE("_ZN2nn6fontll18ScalableFontEngine7SetFontEPc", SetFont);
NX_HLE("_ZN2nn6fontll18ScalableFontEngine8SetScaleEiiii", SetScale);
NX_HLE("_ZN2nn6fontll18ScalableFontEngine15AcquireGlyphmapEjt", AcquireGlyphmap);
NX_HLE("_ZN2nn6fontll18ScalableFontEngine15CheckGlyphExistEj", CheckGlyphExist);

}  // namespace nx