// nn::codec HardwareOpusDecoder → libopus en el host.
//
// En la consola, InitializeHardwareOpusDecoder abre un IHardwareOpusDecoder del servicio hwopus y
// DecodeOpusInterleavedWithHardware le manda cada paquete por IPC. Aquí hwopus es un stub de ipc.cpp
// ("stub ok", sin objeto): Initialize "funciona" pero deja la sesión en 0 y Decode crashea al usarla.
//
// Estrategia:
//  - Initialize: se llama la real (deja los campos del HardwareOpusDecoderType como el sdk los espera,
//    por si algún getter los lee) y además se crea un OpusDecoder de libopus asociado a ese puntero.
//  - Decode: reemplazada por completo; nunca llega al IPC.
//  - Finalize: se destruye el decoder del host y se llama la real.
//
// Formato de entrada (igual que el .opus de Nintendo y que hwopus): cada paquete va precedido por
// {u32 BE tamaño; u32 BE final_range}. "consumed" incluye esos 8 bytes. Salida: s16 intercalado;
// "samples" = muestras por canal.
#include <cstdint>
#include <cstring>
#include <memory>
#include <mutex>
#include <unordered_map>

#include <opus.h>

#include <nx/game.h>

#include "kernel.h"  // CPU, log, call, find_symbol, NX_HLE (igual que hle_audio.cpp)

namespace nx {

namespace {

// nn::codec::OpusResult. El juego solo distingue 0 de distinto de 0 en la práctica;
// los valores de error son representativos, no verificados contra el SDK.
constexpr uint32_t kOpusSuccess = 0;
constexpr uint32_t kOpusInvalidPointer = 1;
constexpr uint32_t kOpusInsufficientPcmBuffer = 7;
constexpr uint32_t kOpusBrokenData = 8;

struct HostDecoder {
    OpusDecoder* dec = nullptr;
    int rate = 48000, channels = 2;
    std::mutex lock;  // por si dos hilos usan el mismo decoder (no debería pasar)
    ~HostDecoder() {
        if (dec) opus_decoder_destroy(dec);
    }
};

std::mutex g_lock;
std::unordered_map<uint64_t, std::shared_ptr<HostDecoder>> g_decoders;  // clave: HardwareOpusDecoderType* del guest

std::shared_ptr<HostDecoder> make_decoder(int rate, int channels) {
    auto d = std::make_shared<HostDecoder>();
    d->rate = rate;
    d->channels = channels;
    int err = 0;
    d->dec = opus_decoder_create(rate, channels, &err);
    if (err != OPUS_OK) {
        log("codec: opus_decoder_create(%d Hz, %d ch) fallo: %s", rate, channels, opus_strerror(err));
        return nullptr;
    }
    return d;
}

std::shared_ptr<HostDecoder> lookup(uint64_t type) {
    std::lock_guard lk(g_lock);
    auto it = g_decoders.find(type);
    if (it != g_decoders.end()) return it->second;
    // Un decoder que no pasó por nuestro Initialize (el hook no encontró el símbolo, o lo creó el
    // constructor de la clase por otro camino): 48 kHz estéreo, lo normal en las pistas del juego.
    static int warned = 0;
    if (warned++ < 5) log("codec: decoder %llx sin Initialize visto; se asume 48000 Hz, 2 canales", (unsigned long long)type);
    auto d = make_decoder(48000, 2);
    if (d) g_decoders[type] = d;
    return d;
}

// OpusResult InitializeHardwareOpusDecoder(HardwareOpusDecoderType*, int sampleRate, int channelCount,
//                                          void* workBuffer, size_t workBufferSize, int option)
// (SDK 11.4.3: el último int es nuevo respecto de SDKs viejos; no lo usamos)
void InitializeHardwareOpusDecoder(CPU& c) {
    static uint64_t real = find_symbol("_ZN2nn5codec29InitializeHardwareOpusDecoderEPNS0_23HardwareOpusDecoderTypeEiiPvmi");
    uint64_t type = c.x[0];
    int rate = int(c.x[1]), channels = int(c.x[2]);
    uint64_t rc = 0;
    if (real) {
        call(c, real);
        rc = c.x[0] & 0xffffffff;
    }
    auto d = make_decoder(rate, channels);
    {
        std::lock_guard lk(g_lock);
        if (d) g_decoders[type] = d;
        else g_decoders.erase(type);
    }
    log("codec: HardwareOpusDecoder %llx: %d Hz, %d canales (real %llx, host %s)", (unsigned long long)type, rate,
        channels, (unsigned long long)rc, d ? "ok" : "fallo");
    // Si la real falló por el stub de hwopus pero libopus sí pudo, el juego sigue adelante.
    c.x[0] = d ? kOpusSuccess : (rc ? rc : kOpusInvalidPointer);
}

// void FinalizeHardwareOpusDecoder(HardwareOpusDecoderType*)
void FinalizeHardwareOpusDecoder(CPU& c) {
    static uint64_t real = find_symbol("_ZN2nn5codec27FinalizeHardwareOpusDecoderEPNS0_23HardwareOpusDecoderTypeE");
    {
        std::lock_guard lk(g_lock);
        g_decoders.erase(c.x[0]);
    }
    if (real) call(c, real);
}

// void ResetHardwareOpusDecoder(HardwareOpusDecoderType*): el juego la usa al hacer seek o reiniciar una pista.
void ResetHardwareOpusDecoder(CPU& c) {
    std::shared_ptr<HostDecoder> d;
    {
        std::lock_guard lk(g_lock);
        auto it = g_decoders.find(c.x[0]);
        if (it != g_decoders.end()) d = it->second;
    }
    if (d) {
        std::lock_guard lk(d->lock);
        opus_decoder_ctl(d->dec, OPUS_RESET_STATE);
    }
    // La real pasaría por IPC (stub): no se llama.
}

// OpusResult DecodeOpusInterleavedWithHardware(HardwareOpusDecoderType*, size_t* consumed, int* sampleCount,
//                                              int16_t* out, size_t outSize, const void* in, size_t inSize)
void DecodeOpusInterleavedWithHardware(CPU& c) {
    uint64_t type = c.x[0];
    auto consumed = reinterpret_cast<uint64_t*>(c.x[1]);
    auto samples = reinterpret_cast<int32_t*>(c.x[2]);
    auto out = reinterpret_cast<int16_t*>(c.x[3]);
    size_t out_size = c.x[4];
    auto in = reinterpret_cast<const uint8_t*>(c.x[5]);
    size_t in_size = c.x[6];

    static uint64_t calls = 0, errors = 0;
    auto fail = [&](uint32_t rc, const char* why) {
        if (errors++ < 20) log("codec: decode %llx: %s (in %zu, out %zu)", (unsigned long long)type, why, in_size, out_size);
        c.x[0] = rc;
    };

    if (!consumed || !samples || !out || !in) return fail(kOpusInvalidPointer, "puntero nulo");
    auto d = lookup(type);
    if (!d) return fail(kOpusInvalidPointer, "sin decoder");
    if (in_size < 8) return fail(kOpusBrokenData, "entrada < 8 bytes");
    uint32_t len = (uint32_t(in[0]) << 24) | (uint32_t(in[1]) << 16) | (uint32_t(in[2]) << 8) | in[3];
    if (len == 0 || 8ull + len > in_size) return fail(kOpusBrokenData, "tamaño de paquete inválido");

    int max_frames = int(out_size / (2 * size_t(d->channels)));
    int n;
    {
        std::lock_guard lk(d->lock);
        n = opus_decode(d->dec, in + 8, int(len), out, max_frames, 0);
    }
    if (n == OPUS_BUFFER_TOO_SMALL) return fail(kOpusInsufficientPcmBuffer, "buffer PCM chico");
    if (n < 0) return fail(kOpusBrokenData, opus_strerror(n));

    *consumed = 8 + len;
    *samples = n;
    if (++calls == 1 || calls % 10000 == 0)
        log("codec: %llu paquetes decodificados (ultimo: %u bytes -> %d muestras/canal)", (unsigned long long)calls, len, n);
    c.x[0] = kOpusSuccess;
}

}  // namespace

NX_HLE("_ZN2nn5codec29InitializeHardwareOpusDecoderEPNS0_23HardwareOpusDecoderTypeEiiPvmi", InitializeHardwareOpusDecoder);
NX_HLE("_ZN2nn5codec27FinalizeHardwareOpusDecoderEPNS0_23HardwareOpusDecoderTypeE", FinalizeHardwareOpusDecoder);
NX_HLE("_ZN2nn5codec24ResetHardwareOpusDecoderEPNS0_23HardwareOpusDecoderTypeE", ResetHardwareOpusDecoder);
NX_HLE("_ZN2nn5codec33DecodeOpusInterleavedWithHardwareEPNS0_23HardwareOpusDecoderTypeEPmPiPsmPKvm", DecodeOpusInterleavedWithHardware);

}  // namespace nx