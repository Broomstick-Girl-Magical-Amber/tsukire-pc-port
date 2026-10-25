// hle_movie.cpp - HLE de movie:: (multimedia.nss) para Tsukihime, sobre movie_core (FFmpeg).
//
// Va en runtime/src/ del framework. Registra los 30 simbolos movie:: que importa main y arma en memoria del
// guest las vtables de movie::Extractor y movie::Decoder con el orden exacto sacado de subsdk1
// (ver vtables.md). Al registrar estos nombres, el loader enlaza los imports de main aqui y no a subsdk1.
//
// Layout de los objetos del guest (los aloja el juego; nosotros solo escribimos en ellos):
//   movie::Extractor / movie::Decoder : [+0] puntero a vtable (guest)  [+8] puntero al objeto host
//   movie::Buffer / movie::MediaData  : [+0] puntero al objeto host
// TODO: confirmar sizeof real de cada clase en main (tamano que pasa a operator new / reserva en stack).
//       Este esquema necesita 16 bytes para Extractor/Decoder y 8 para Buffer/MediaData.
#include <algorithm>
#include <atomic>
#include <chrono>
#include <thread>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include <nx/runtime.h>

#include "movie_core.h"
#include "nvn/nvn.h"  // Texture, get<>, objects_lock, format_block (apply_movie_texture.py)

// AP_DISCORD (apply_discord.py): avisos para Discord Rich Presence (games/tsukihime_common/discord_tsuki.cpp).
// Solo copian el nombre del video y despiertan a su hilo; no hacen E/S ni tocan la decodificacion.
namespace tsuki_discord {
void video_started(const void* key, const char* romfs_rel);
void video_ended(const void* key);
}  // namespace tsuki_discord

using namespace movie_host;

namespace {

// ---------------------------------------------------------------------------------------------- objetos host

struct HostBuffer {
    std::shared_ptr<std::vector<uint8_t>> owned;  // memoria propia (Buffer(size)); compartida en copias
    uint8_t* base = nullptr;
    size_t capacity = 0;
    int32_t offset = 0, size = 0;
    int32_t i32 = 0;
    uint32_t u32 = 0;
    int64_t i64 = 0;
    uint64_t nvn_dev = 0, nvn_sync = 0;
    bool borrowed = false;  // base apunta a memoria de un decoder nuestro (GetInputBuffer / GetOutputBuffer)

    void point_to(uint8_t* p, size_t cap, int32_t sz, bool ours = false) {
        owned.reset();
        borrowed = ours;
        base = p;
        capacity = cap;
        offset = 0;
        size = sz;
    }
};

template <class T> T*& slot0(uint64_t obj) { return *reinterpret_cast<T**>(obj); }
template <class T> T*& slot8(uint64_t obj) { return *reinterpret_cast<T**>(obj + 8); }

HostBuffer* buf(uint64_t obj) { return slot0<HostBuffer>(obj); }
MediaDataStore* md(uint64_t obj) { return slot0<MediaDataStore>(obj); }
Extractor* ext(uint64_t obj) { return slot8<Extractor>(obj); }
// El Decoder del host, mas lo que el modo con eventos necesita (RegisterClientEvents).
struct DecoderSlot {
    std::unique_ptr<Decoder> d;
    std::atomic<uint64_t> events{0};  // movie::DecoderEvents* del juego
    std::atomic<bool> format_signaled{false};  // el juego llama desde sus hilos de entrada y salida a la vez
    std::atomic<int> track{-1};  // pista del Extractor que alimenta a este decoder (sale del formato de Configure)
    uint32_t type = 0, format = 0, mode = 0;  // argumentos del constructor (DecoderType, DecoderOutputFormat, DecoderMode)
};
DecoderSlot* dslot(uint64_t obj) { return slot8<DecoderSlot>(obj); }
Decoder* dec(uint64_t obj) { return dslot(obj)->d.get(); }

// NX_MOVIE_TRACE=1 (apagado por defecto): las primeras 30 llamadas de cada funcion en cada objeto, y despues la 100, 1000, 10000...
// con su resultado. Sirve para ver en que orden llama el juego y en que bucle queda. 
bool movie_trace() {
    static const bool on = [] {
        const char* e = std::getenv("NX_MOVIE_TRACE");
        return e && std::strcmp(e, "0") != 0;
    }();
    return on;
}
// Ultima llamada movie:: de cada hilo del juego (para el diagnostico periodico: quien dejo de llamar y desde cuando).
struct LastCall {
    std::string name;
    uint64_t obj = 0;
    Status status = kOk;
    std::chrono::steady_clock::time_point when;
    uint64_t calls = 0;
};
std::mutex g_last_m;
std::map<std::thread::id, LastCall> g_last;

void trace_call(Status s, uint64_t obj) {
    if (!movie_trace()) return;
    {
        const char* nm = nx::hle_current_name();
        std::lock_guard<std::mutex> lk(g_last_m);
        LastCall& l = g_last[std::this_thread::get_id()];
        l.name = nm ? nm : "?";
        l.obj = obj;
        l.status = s;
        l.when = std::chrono::steady_clock::now();
        l.calls++;
    }
    static std::mutex m;
    static std::map<std::string, uint64_t> counts;
    const char* name = nx::hle_current_name();
    uint64_t n;
    {
        std::lock_guard<std::mutex> lk(m);
        n = ++counts[std::string(name ? name : "?") + "@" + std::to_string(obj)];  // por funcion y por objeto
    }
    bool pow10 = n >= 100 && [n] { uint64_t v = n; while (v % 10 == 0) v /= 10; return v == 1; }();
    if (n <= 30 || pow10)
        nx::log("movie: #%llu %s(obj %llx) -> %s", (unsigned long long)n, name ? name : "?", (unsigned long long)obj,
                status_name(s));
}
// NX_MOVIE_TIMING=1 (apagado por defecto): una linea por frame de video (formato 3, a textura NVN) al soltarlo:
// pts, keyframe, cuanto costo decodificarlo y convertirlo, cuanto tardo AcquireOutputBufferIndex (si decodifico en
// ese momento), si el juego lo mostro (GetOutputBuffer = copia a la textura) o lo solto sin mostrar (descartado), y
// cada 60 frames un resumen. Para ver por que el video da un tiron cada ~1 s.
bool movie_timing() {
    static const bool on = [] {
        const char* e = std::getenv("NX_MOVIE_TIMING");
        return e && std::strcmp(e, "0") != 0;
    }();
    return on;
}
uint64_t mono_ns() {
    return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now().time_since_epoch()).count());
}
double since_start_ms(uint64_t t) {  // ms desde el primer evento de la traza
    static const uint64_t t0 = mono_ns();
    return double(int64_t(t - t0)) / 1e6;
}
struct FrameTiming {
    int64_t pts_us = 0;
    bool key = false, shown = false;
    uint64_t decode_ns = 0, convert_ns = 0, acquire_call_ns = 0, acquired_at = 0, shown_at = 0;
};
struct VideoTiming {
    std::map<int, FrameTiming> held;  // por indice de salida
    uint64_t last_shown_at = 0;
    int64_t last_pts = -1;
    uint64_t frames = 0, shown = 0, dropped = 0, max_work_ns = 0, sum_work_ns = 0, max_gap_ns = 0;
};
std::mutex g_timing_m;
std::map<const void*, VideoTiming> g_timing;  // por Decoder del host

void ret(nx::CPU& c, Status s) {
    trace_call(s, c.x[0]);
    c.x[0] = uint32_t(s);
}
#define TRACE(...) \
    do {           \
        if (nx::g_trace) nx::log(__VA_ARGS__); \
    } while (0)

// ---------------------------------------------------------------------------------------------- vtables

// Orden de subsdk1 (indice virtual 0 = slot 2 de la tabla). No cambiar sin volver a sacar vtables.md.
const char* const kExtractorVtable[] = {
    "_ZN5movie9ExtractorD2Ev",
    "_ZN5movie9ExtractorD0Ev",
    "_ZN5movie9Extractor13SetDataSourceEPKc",
    "_ZN5movie9Extractor13SetDataSourceEPKvi",
    "_ZN5movie9Extractor13GetTrackCountEPi",
    "_ZN5movie9Extractor21GetTrackConfigurationEiPNS_9MediaDataE",
    "_ZN5movie9Extractor11SelectTrackEi",
    "_ZN5movie9Extractor13UnselectTrackEi",
    "_ZN5movie9Extractor6SeekToEl",
    "_ZN5movie9Extractor29GetTrackIndexForAvailableDataEPm",
    "_ZN5movie9Extractor4ReadEPNS_6BufferE",
    "_ZN5movie9Extractor7AdvanceEv",
    "_ZN5movie9Extractor13GetSampleTimeEPl",
    "_ZN5movie9Extractor17GetSampleMetaDataEPNS_9MediaDataE",
    "_ZN5movie9Extractor16GetBufferedRangeEPlS1_",
    "_ZN5movie9Extractor13SetDataSourceEPNS_18ClientStreamReaderEPKc",
};
const char* const kDecoderVtable[] = {
    "_ZN5movie7DecoderD2Ev",
    "_ZN5movie7DecoderD0Ev",
    "_ZN5movie7Decoder9ConfigureEPNS_9MediaDataE",
    "_ZN5movie7Decoder20RegisterClientEventsEPNS_13DecoderEventsE",
    "_ZN5movie7Decoder15GetOutputFormatEPNS_9MediaDataE",
    "_ZN5movie7Decoder5StartEv",
    "_ZN5movie7Decoder4StopEv",
    "_ZN5movie7Decoder5ResetEv",
    "_ZN5movie7Decoder5FlushEv",
    "_ZN5movie7Decoder23AcquireInputBufferIndexEPiS1_",
    "_ZN5movie7Decoder14GetInputBufferEiPNS_6BufferE",
    "_ZN5movie7Decoder24SendInputBufferForDecodeEiiilj",
    "_ZN5movie7Decoder24AcquireOutputBufferIndexEPiPlPjS1_",
    "_ZN5movie7Decoder15GetOutputBufferEiPNS_6BufferE",
    "_ZN5movie7Decoder19GetOutputBufferSizeEPiS1_",
    "_ZN5movie7Decoder24ReleaseOutputBufferIndexEi",
    "_ZN5movie7Decoder12GetLastErrorEv",
    "_ZN5movie7Decoder15GetOutputBufferEiPNS_6BufferEPNS_9MediaDataE",
    "_ZN5movie7Decoder21GetOutputBufferFormatEiPNS_9MediaDataE",
};

template <size_t N> uint64_t build_vtable(const char* const (&names)[N]) {
    uint64_t table = nx::kernel_alloc((N + 2) * 8, 16);
    auto* slots = reinterpret_cast<uint64_t*>(table);
    slots[0] = 0;  // offset-to-top
    slots[1] = 0;  // typeinfo: TODO si el juego usa dynamic_cast/typeid sobre estas clases
    for (size_t i = 0; i < N; i++) slots[2 + i] = nx::hle_address(names[i]);
    return table + 16;  // el objeto apunta al primer metodo virtual
}
uint64_t extractor_vtable() {
    static uint64_t vt = build_vtable(kExtractorVtable);
    return vt;
}
uint64_t decoder_vtable() {
    static uint64_t vt = build_vtable(kDecoderVtable);
    return vt;
}

// operator delete del guest (lo exporta main), para los destructores "deleting" (D0).
void guest_delete(nx::CPU& c, uint64_t obj) {
    static uint64_t del = nx::find_symbol("_ZdlPv");
    if (!del) nx::fatal("movie: _ZdlPv no encontrado");
    c.x[0] = obj;
    nx::guest_call(c, del);
}

// "rom:/movie/op.mp4" -> "movie/op.mp4"
std::string romfs_relative(const char* guest_path) {
    std::string p(guest_path ? guest_path : "");
    size_t k = p.find(":/");
    if (k != std::string::npos) p = p.substr(k + 2);
    while (!p.empty() && p[0] == '/') p.erase(0, 1);
    return p;
}

// ============================================================================================== funciones libres

void SetAllocator(nx::CPU&) {}  // usamos el heap del host
void SetCoreMask(nx::CPU&) {}
void GetThreadPriority(nx::CPU& c) { c.x[0] = 0; }
void SetThreadPriority(nx::CPU&) {}

void GetH264NalUnitStartPositionAndNalSize(nx::CPU& c) {
    auto** data = reinterpret_cast<const uint8_t**>(c.x[0]);
    auto* size = reinterpret_cast<size_t*>(c.x[1]);
    auto** nal = reinterpret_cast<const uint8_t**>(c.x[2]);
    auto* nal_size = reinterpret_cast<size_t*>(c.x[3]);
    ret(c, next_nal_unit(data, size, nal, nal_size));
}

NX_HLE("_ZN5movie12SetAllocatorEPFPvmmS0_EPFvS0_S0_EPFS0_S0_mS0_ES0_", SetAllocator);
NX_HLE("_ZN5movie11SetCoreMaskEm", SetCoreMask);
NX_HLE("_ZN5movie17GetThreadPriorityEv", GetThreadPriority);
NX_HLE("_ZN5movie17SetThreadPriorityEi", SetThreadPriority);
NX_HLE("_ZN5movie37GetH264NalUnitStartPositionAndNalSizeEPPhPmS1_S2_", GetH264NalUnitStartPositionAndNalSize);

// ============================================================================================== movie::Buffer

void Buffer_ctor(nx::CPU& c) { slot0<HostBuffer>(c.x[0]) = new HostBuffer; }
void Buffer_ctor_size(nx::CPU& c) {
    auto* b = new HostBuffer;
    b->owned = std::make_shared<std::vector<uint8_t>>(c.x[1]);
    b->base = b->owned->data();
    b->capacity = c.x[1];
    b->size = int32_t(c.x[1]);
    slot0<HostBuffer>(c.x[0]) = b;
}
void Buffer_ctor_ptr(nx::CPU& c) {
    auto* b = new HostBuffer;
    b->base = reinterpret_cast<uint8_t*>(c.x[1]);
    b->capacity = c.x[2];
    b->size = int32_t(c.x[2]);
    slot0<HostBuffer>(c.x[0]) = b;
}
void Buffer_ctor_copy(nx::CPU& c) { slot0<HostBuffer>(c.x[0]) = new HostBuffer(*buf(c.x[1])); }
void Buffer_dtor(nx::CPU& c) {
    delete buf(c.x[0]);
    slot0<HostBuffer>(c.x[0]) = nullptr;
}
void Buffer_assign(nx::CPU& c) {
    if (c.x[0] != c.x[1]) *buf(c.x[0]) = *buf(c.x[1]);
    // devuelve *this: x0 ya lo contiene
}
void Buffer_SetInt32Data(nx::CPU& c) { buf(c.x[0])->i32 = int32_t(c.x[1]); }
void Buffer_SetInt64Data(nx::CPU& c) { buf(c.x[0])->i64 = int64_t(c.x[1]); }
void Buffer_SetUint32Data(nx::CPU& c) { buf(c.x[0])->u32 = uint32_t(c.x[1]); }
void Buffer_SetDataAndCapacity(nx::CPU& c) {
    buf(c.x[0])->point_to(reinterpret_cast<uint8_t*>(c.x[1]), c.x[2], 0);
}
void Buffer_SetRange(nx::CPU& c) {
    auto* b = buf(c.x[0]);
    b->offset = int32_t(c.x[1]);
    b->size = int32_t(c.x[2]);
}
void Buffer_Base(nx::CPU& c) { c.x[0] = reinterpret_cast<uint64_t>(buf(c.x[0])->base); }
void Buffer_Size(nx::CPU& c) { c.x[0] = uint64_t(buf(c.x[0])->size); }
void Buffer_Offset(nx::CPU& c) { c.x[0] = uint64_t(buf(c.x[0])->offset); }
void Buffer_Capacity(nx::CPU& c) { c.x[0] = buf(c.x[0])->capacity; }
void Buffer_GetInt64Data(nx::CPU& c) { c.x[0] = uint64_t(buf(c.x[0])->i64); }
void Buffer_GetUint32Data(nx::CPU& c) { c.x[0] = buf(c.x[0])->u32; }

NX_HLE("_ZN5movie6BufferC1Ev", Buffer_ctor);
NX_HLE("_ZN5movie6BufferC1Em", Buffer_ctor_size);
NX_HLE("_ZN5movie6BufferC1EPvm", Buffer_ctor_ptr);
NX_HLE("_ZN5movie6BufferC1ERKS0_", Buffer_ctor_copy);
NX_HLE("_ZN5movie6BufferD1Ev", Buffer_dtor);
NX_HLE("_ZN5movie6BufferaSERKS0_", Buffer_assign);
NX_HLE("_ZN5movie6Buffer12SetInt32DataEi", Buffer_SetInt32Data);
NX_HLE("_ZN5movie6Buffer12SetInt64DataEl", Buffer_SetInt64Data);
NX_HLE("_ZN5movie6Buffer13SetUint32DataEj", Buffer_SetUint32Data);
NX_HLE("_ZN5movie6Buffer18SetDataAndCapacityEPvm", Buffer_SetDataAndCapacity);
NX_HLE("_ZN5movie6Buffer8SetRangeEii", Buffer_SetRange);
NX_HLE("_ZNK5movie6Buffer4BaseEv", Buffer_Base);
NX_HLE("_ZNK5movie6Buffer4SizeEv", Buffer_Size);
NX_HLE("_ZNK5movie6Buffer6OffsetEv", Buffer_Offset);
NX_HLE("_ZNK5movie6Buffer8CapacityEv", Buffer_Capacity);
NX_HLE("_ZNK5movie6Buffer12GetInt64DataEv", Buffer_GetInt64Data);
NX_HLE("_ZNK5movie6Buffer13GetUint32DataEv", Buffer_GetUint32Data);

// ============================================================================================== movie::MediaData

void MediaData_ctor(nx::CPU& c) { slot0<MediaDataStore>(c.x[0]) = new MediaDataStore; }
void MediaData_ctor_copy(nx::CPU& c) { slot0<MediaDataStore>(c.x[0]) = new MediaDataStore(*md(c.x[1])); }
void MediaData_dtor(nx::CPU& c) {
    delete md(c.x[0]);
    slot0<MediaDataStore>(c.x[0]) = nullptr;
}
void MediaData_Clear(nx::CPU& c) { md(c.x[0])->clear(); }
void MediaData_FindInt32(nx::CPU& c) {
    const char* key = reinterpret_cast<const char*>(c.x[1]);
    const int32_t* v = md(c.x[0])->find<int32_t>(key);
    TRACE("movie: MediaData::FindInt32(\"%s\") -> %s", key, v ? std::to_string(*v).c_str() : "(no)");
    if (v) *reinterpret_cast<int32_t*>(c.x[2]) = *v;
    c.x[0] = v ? 1 : 0;  // bool
}
void MediaData_FindString(nx::CPU& c) {
    const char* key = reinterpret_cast<const char*>(c.x[1]);
    const std::string* v = md(c.x[0])->find<std::string>(key);
    TRACE("movie: MediaData::FindString(\"%s\") -> %s", key, v ? v->c_str() : "(no)");
    if (v) *reinterpret_cast<const char**>(c.x[2]) = v->c_str();
    c.x[0] = v ? 1 : 0;
}

NX_HLE("_ZN5movie9MediaDataC1Ev", MediaData_ctor);
NX_HLE("_ZN5movie9MediaDataC1ERKS0_", MediaData_ctor_copy);
NX_HLE("_ZN5movie9MediaDataD1Ev", MediaData_dtor);
NX_HLE("_ZN5movie9MediaData5ClearEv", MediaData_Clear);
NX_HLE("_ZNK5movie9MediaData9FindInt32EPKcPi", MediaData_FindInt32);
NX_HLE("_ZNK5movie9MediaData10FindStringEPKcPS2_", MediaData_FindString);

// ============================================================================================== movie::Extractor

void Extractor_ctor(nx::CPU& c) {
    nx::log("movie: Extractor(ContainerType=%u)", uint32_t(c.x[1]));  // TODO: anotar el valor que usa el juego
    *reinterpret_cast<uint64_t*>(c.x[0]) = extractor_vtable();
    slot8<Extractor>(c.x[0]) = new Extractor;
}
void Extractor_D2(nx::CPU& c) {
    tsuki_discord::video_ended(ext(c.x[0]));  // AP_DISCORD
    delete ext(c.x[0]);
    slot8<Extractor>(c.x[0]) = nullptr;
}
void Extractor_D0(nx::CPU& c) {
    uint64_t obj = c.x[0];
    Extractor_D2(c);
    guest_delete(c, obj);
}
void Extractor_SetDataSource_path(nx::CPU& c) {
    std::string rel = romfs_relative(reinterpret_cast<const char*>(c.x[1]));
    std::string data = nx::read_game_file(rel);
    nx::log("movie: SetDataSource(\"%s\") -> %zu bytes", rel.c_str(), data.size());
    if (!data.empty()) tsuki_discord::video_started(ext(c.x[0]), rel.c_str());  // AP_DISCORD
    ret(c, data.empty() ? kIoError : ext(c.x[0])->open_memory(std::move(data)));
}
void Extractor_SetDataSource_mem(nx::CPU& c) {
    const char* p = reinterpret_cast<const char*>(c.x[1]);
    int n = int32_t(c.x[2]);
    nx::log("movie: SetDataSource(memoria, %d bytes)", n);
    ret(c, n > 0 ? ext(c.x[0])->open_memory(std::string(p, size_t(n))) : kInvalidArgument);
}
void Extractor_SetDataSource_reader(nx::CPU& c) {
    // (ClientStreamReader* reader, const char* path). Tsukihime pasa su propio lector Y la ruta del archivo
    // ("rom:/movie/prologue.mp4"). El lector solo es la forma del juego de leer ese mismo archivo, asi que se abre
    // directo por la ruta y el lector no se usa (no hace falta reconstruir la vtable de ClientStreamReader).
    const char* path = reinterpret_cast<const char*>(c.x[2]);
    std::string rel = romfs_relative(path);
    std::string data = rel.empty() ? std::string() : nx::read_game_file(rel);
    nx::log("movie: SetDataSource(ClientStreamReader=%llx, \"%s\") -> abierto por ruta, %zu bytes",
            (unsigned long long)c.x[1], path ? path : "(null)", data.size());
    if (!data.empty()) tsuki_discord::video_started(ext(c.x[0]), rel.c_str());  // AP_DISCORD
    ret(c, data.empty() ? kIoError : ext(c.x[0])->open_memory(std::move(data)));
}
void Extractor_GetTrackCount(nx::CPU& c) {
    ret(c, ext(c.x[0])->track_count(reinterpret_cast<int*>(c.x[1])));
}
void Extractor_GetTrackConfiguration(nx::CPU& c) {
    ret(c, ext(c.x[0])->track_configuration(int32_t(c.x[1]), md(c.x[2])));
}
void Extractor_SelectTrack(nx::CPU& c) { ret(c, ext(c.x[0])->select_track(int32_t(c.x[1]))); }
void Extractor_UnselectTrack(nx::CPU& c) { ret(c, ext(c.x[0])->unselect_track(int32_t(c.x[1]))); }
// Un solo Extractor alimenta a los dos decoders: el juego mira de que pista es la proxima muestra
// (GetTrackIndexForAvailableData) y la manda al decoder que corresponde. Si la proxima es de video, el hilo de video
// tiene que despertar aunque su decoder no haya cambiado, o el de audio queda esperando detras de ella (el audio
// salia a tirones). Cada vez que avanza el Extractor se avisa "entrada disponible" SOLO al decoder de la pista de la
// proxima muestra (avisar a todos dejo el audio mudo: el juego parece leer la muestra al despertar, sea de quien sea).
void wake_track_input(nx::CPU& c, Extractor* e);

void Extractor_SeekTo(nx::CPU& c) {
    Extractor* e = ext(c.x[0]);
    Status s = e->seek_to(int64_t(c.x[1]));
    wake_track_input(c, e);
    ret(c, s);
}
void Extractor_GetTrackIndexForAvailableData(nx::CPU& c) {
    ret(c, ext(c.x[0])->track_index_for_available_data(reinterpret_cast<uint64_t*>(c.x[1])));
}
void Extractor_Read(nx::CPU& c) {
    Extractor* e = ext(c.x[0]);
    HostBuffer* b = buf(c.x[1]);
    size_t got = 0;
    Status s = e->read(b->base, b->capacity, &got);
    if (s == kOk) {
        b->offset = 0;
        b->size = int32_t(got);
        int64_t t = 0;
        if (e->sample_time(&t) == kOk) b->i64 = t;  // TODO: confirmar si el juego lee el tiempo de aqui
    }
    ret(c, s);
}
void Extractor_Advance(nx::CPU& c) {
    Extractor* e = ext(c.x[0]);
    Status s = e->advance();
    wake_track_input(c, e);
    ret(c, s);
}
void Extractor_GetSampleTime(nx::CPU& c) {
    ret(c, ext(c.x[0])->sample_time(reinterpret_cast<int64_t*>(c.x[1])));
}
void Extractor_GetSampleMetaData(nx::CPU& c) { ret(c, ext(c.x[0])->sample_metadata(md(c.x[1]))); }
void Extractor_GetBufferedRange(nx::CPU& c) {
    ret(c, ext(c.x[0])->buffered_range(reinterpret_cast<int64_t*>(c.x[1]), reinterpret_cast<int64_t*>(c.x[2])));
}

NX_HLE("_ZN5movie9ExtractorC1ENS_13ContainerTypeE", Extractor_ctor);
NX_HLE("_ZN5movie9ExtractorD2Ev", Extractor_D2);
NX_HLE("_ZN5movie9ExtractorD0Ev", Extractor_D0);
NX_HLE("_ZN5movie9Extractor13SetDataSourceEPKc", Extractor_SetDataSource_path);
NX_HLE("_ZN5movie9Extractor13SetDataSourceEPKvi", Extractor_SetDataSource_mem);
NX_HLE("_ZN5movie9Extractor13SetDataSourceEPNS_18ClientStreamReaderEPKc", Extractor_SetDataSource_reader);
NX_HLE("_ZN5movie9Extractor13GetTrackCountEPi", Extractor_GetTrackCount);
NX_HLE("_ZN5movie9Extractor21GetTrackConfigurationEiPNS_9MediaDataE", Extractor_GetTrackConfiguration);
NX_HLE("_ZN5movie9Extractor11SelectTrackEi", Extractor_SelectTrack);
NX_HLE("_ZN5movie9Extractor13UnselectTrackEi", Extractor_UnselectTrack);
NX_HLE("_ZN5movie9Extractor6SeekToEl", Extractor_SeekTo);
NX_HLE("_ZN5movie9Extractor29GetTrackIndexForAvailableDataEPm", Extractor_GetTrackIndexForAvailableData);
NX_HLE("_ZN5movie9Extractor4ReadEPNS_6BufferE", Extractor_Read);
NX_HLE("_ZN5movie9Extractor7AdvanceEv", Extractor_Advance);
NX_HLE("_ZN5movie9Extractor13GetSampleTimeEPl", Extractor_GetSampleTime);
NX_HLE("_ZN5movie9Extractor17GetSampleMetaDataEPNS_9MediaDataE", Extractor_GetSampleMetaData);
NX_HLE("_ZN5movie9Extractor16GetBufferedRangeEPlS1_", Extractor_GetBufferedRange);

// ============================================================================================== movie::Decoder

DecoderSlot* make_slot(uint32_t fmt = 0) {
    auto* sl = new DecoderSlot;
    // outputFormat 3: el frame va a una NVNtexture del juego, como RGBA (apply_movie_texture.py)
    sl->d = std::make_unique<Decoder>(DecoderKind::Video, fmt == 3 ? VideoFormat::RGBA : VideoFormat::NV12);
    return sl;
}
void Decoder_ctor(nx::CPU& c) {
    // Decoder(DecoderType, DecoderOutputFormat, DecoderMode, int). TODO: mapear los enums cuando sepamos sus valores.
    uint32_t type = uint32_t(c.x[1]), fmt = uint32_t(c.x[2]), mode = uint32_t(c.x[3]), extra = uint32_t(c.x[4]);
    nx::log("movie: Decoder(type=%u, outputFormat=%u, mode=%u, %d)", type, fmt, mode, int32_t(extra));
    *reinterpret_cast<uint64_t*>(c.x[0]) = decoder_vtable();
    DecoderSlot* sl = make_slot(fmt);
    sl->type = type, sl->format = fmt, sl->mode = mode;
    slot8<DecoderSlot>(c.x[0]) = sl;
}
void forget_slot(DecoderSlot* sl);
void Decoder_D2(nx::CPU& c) {
    forget_slot(dslot(c.x[0]));
    delete dslot(c.x[0]);
    slot8<DecoderSlot>(c.x[0]) = nullptr;
}
void Decoder_D0(nx::CPU& c) {
    uint64_t obj = c.x[0];
    Decoder_D2(c);
    guest_delete(c, obj);
}
void Decoder_Configure(nx::CPU& c) {
    MediaDataStore* f = md(c.x[1]);
    const std::string* mime = f->find<std::string>("mime");
    nx::log("movie: Decoder::Configure(mime=%s, %zu claves, pista %d)", mime ? mime->c_str() : "?", f->count(), f->track);
    dslot(c.x[0])->track = f->track;
    ret(c, dec(c.x[0])->configure(*f));
}
// movie::DecoderEvents (sacado de DecoderImpl::Send*ToClient en subsdk1): 4 nn::os::EventType*.
constexpr uint64_t kEvInput = 0x00, kEvOutput = 0x08, kEvFormat = 0x10, kEvError = 0x18;

// nn::os::SignalEvent del sdk sobre el evento en `events + off` (si el juego lo dio), sin tocar los registros.
void signal_event(nx::CPU& c, uint64_t events, uint64_t off) {
    if (!events) return;
    uint64_t ev = *reinterpret_cast<uint64_t*>(events + off);
    if (!ev) return;
    static uint64_t fn = nx::find_symbol("_ZN2nn2os11SignalEventEPNS0_9EventTypeE");
    if (!fn) nx::fatal("movie: nn::os::SignalEvent no encontrado");
    uint64_t saved[31];
    std::memcpy(saved, c.x, sizeof saved);
    c.x[0] = ev;
    nx::guest_call(c, fn);
    std::memcpy(c.x, saved, sizeof saved);
}

// Avisa al juego lo que corresponda despues de un cambio: entrada libre, cambio de formato (una vez, antes del
// primer frame) y salida lista.
//
// Importante: cada metodo del Decoder del host toma su propio mutex y lo suelta al volver, asi que aqui primero se
// decide que avisar y SignalEvent se llama sin ningun lock tomado. Un SignalEvent despierta a otro hilo del juego que
// entra de inmediato a este mismo Decoder; si siguieramos dentro del lock (o sin lock, como antes) se pisan.
void notify(nx::CPU& c, uint64_t obj, bool try_output) {
    DecoderSlot* sl = dslot(obj);
    uint64_t events = sl->events.load();
    if (!events) return;
    bool output = try_output && sl->d->pump();  // primero decodificar: eso puede liberar lugar en la entrada
    bool input = sl->d->has_free_input();
    bool format = output && !sl->format_signaled.exchange(true);
    if (input) signal_event(c, events, kEvInput);
    if (format) signal_event(c, events, kEvFormat);
    if (output) signal_event(c, events, kEvOutput);
}

// Decoders con eventos registrados (para wake_all_inputs).
std::mutex g_slots_m;
std::vector<DecoderSlot*> g_slots;

void forget_slot(DecoderSlot* sl) {
    std::lock_guard<std::mutex> lk(g_slots_m);
    g_slots.erase(std::remove(g_slots.begin(), g_slots.end(), sl), g_slots.end());
}

void wake_track_input(nx::CPU& c, Extractor* e) {
    uint64_t track = 0;
    if (e->track_index_for_available_data(&track) != kOk) return;  // fin del archivo
    DecoderSlot* target = nullptr;
    {
        std::lock_guard<std::mutex> lk(g_slots_m);
        for (DecoderSlot* sl : g_slots)
            if (sl->track.load() == int(track)) target = sl;
    }
    if (!target) return;
    uint64_t events = target->events.load();
    if (events && target->d->has_free_input()) signal_event(c, events, kEvInput);
}

// Diagnostico (NX_MOVIE_TRACE): cada 2 s, el estado de las colas de cada decoder, para ver donde se traba.
void start_watchdog() {
    if (!movie_trace()) return;
    static std::once_flag once;
    std::call_once(once, [] {
        std::thread([] {
            for (;;) {
                std::this_thread::sleep_for(std::chrono::seconds(2));
                {
                    std::lock_guard<std::mutex> lk(g_slots_m);
                    for (DecoderSlot* sl : g_slots)
                        nx::log("movie: estado %s, pista %d", sl->d->debug_state().c_str(), sl->track.load());
                }
                auto now = std::chrono::steady_clock::now();
                std::lock_guard<std::mutex> lk(g_last_m);
                int k = 0;
                for (auto& [id, l] : g_last) {
                    long long ms = std::chrono::duration_cast<std::chrono::milliseconds>(now - l.when).count();
                    nx::log("movie: hilo %d: %llu llamadas, la ultima hace %lld ms: %s(obj %llx) -> %s", k++,
                            (unsigned long long)l.calls, ms, l.name.c_str(), (unsigned long long)l.obj,
                            status_name(l.status));
                }
            }
        }).detach();
    });
}

void Decoder_RegisterClientEvents(nx::CPU& c) {
    start_watchdog();
    uint64_t ev = c.x[1];
    dslot(c.x[0])->events = ev;
    {
        std::lock_guard<std::mutex> lk(g_slots_m);
        if (std::find(g_slots.begin(), g_slots.end(), dslot(c.x[0])) == g_slots.end()) g_slots.push_back(dslot(c.x[0]));
    }
    if (ev) {
        const auto* e = reinterpret_cast<const uint64_t*>(ev);
        nx::log("movie: Decoder::RegisterClientEvents: entrada %llx, salida %llx, formato %llx, error %llx",
                (unsigned long long)e[0], (unsigned long long)e[1], (unsigned long long)e[2],
                (unsigned long long)e[3]);
    }
    ret(c, kOk);
}
void Decoder_GetOutputFormat(nx::CPU& c) { ret(c, dec(c.x[0])->output_format(md(c.x[1]))); }
void Decoder_Start(nx::CPU& c) {
    uint64_t obj = c.x[0];
    Status s = dec(obj)->start();
    if (s == kOk) notify(c, obj, false);  // los buffers de entrada ya estan libres
    ret(c, s);
}
void Decoder_Stop(nx::CPU& c) { ret(c, dec(c.x[0])->stop()); }
void Decoder_Reset(nx::CPU& c) { ret(c, dec(c.x[0])->reset()); }
void Decoder_Flush(nx::CPU& c) {
    uint64_t obj = c.x[0];
    Status s = dec(obj)->flush();
    notify(c, obj, false);
    ret(c, s);
}
void Decoder_AcquireInputBufferIndex(nx::CPU& c) {
    auto* index = reinterpret_cast<int32_t*>(c.x[1]);
    // El segundo int* es cuantos indices MAS hay libres: el juego vuelve a llamar esa cantidad de veces seguidas
    // (main+eebe8: bucle sobre [sp+4] en el manejador del evento de entrada).
    auto* more = reinterpret_cast<int32_t*>(c.x[2]);
    int idx = -1;
    Decoder* d = dec(c.x[0]);
    Status s = d->acquire_input_index(&idx);
    if (index) *index = idx;
    if (more) *more = s == kOk ? d->free_input_count() : 0;
    ret(c, s);
}
void Decoder_GetInputBuffer(nx::CPU& c) {
    size_t cap = 0;
    uint8_t* p = dec(c.x[0])->input_buffer(int32_t(c.x[1]), &cap);
    if (!p) return ret(c, kInvalidArgument);
    buf(c.x[2])->point_to(p, cap, 0, true);
    ret(c, kOk);
}
void Decoder_SendInputBufferForDecode(nx::CPU& c) {
    // (int index, int offset, int size, long timeUs, uint flags)
    uint64_t obj = c.x[0];
    Status s = dec(obj)->send_input(int32_t(c.x[1]), int32_t(c.x[2]), int32_t(c.x[3]), int64_t(c.x[4]),
                                    uint32_t(c.x[5]));
    if (s == kOk) notify(c, obj, true);  // la muestra se decodifica ya; si sale algo, se avisa
    ret(c, s);
}
void Decoder_AcquireOutputBufferIndex(nx::CPU& c) {
    // (int* index, long* timeUs, uint* flags, int* mas). El cuarto es cuantas salidas MAS hay listas: el juego vuelve
    // a llamar esa cantidad de veces (main+eed24). Antes escribiamos ahi el tamano en bytes del buffer, y el juego
    // repetia la llamada miles de veces por cada frame (los millones de TryAgain del log).
    Decoder* d = dec(c.x[0]);
    int idx = -1;
    int64_t t = 0;
    uint32_t fl = 0;
    const uint64_t t_call = mono_ns();
    Status s = d->acquire_output_index(&idx, &t, &fl);
    if (s == kOk && movie_timing() && dslot(c.x[0])->format == 3 && !(fl & kFlagEndOfStream)) {
        FrameTiming ft;
        ft.pts_us = t;
        ft.acquired_at = mono_ns();
        ft.acquire_call_ns = ft.acquired_at - t_call;
        d->output_timing(idx, &ft.decode_ns, &ft.convert_ns, &ft.key);
        std::lock_guard<std::mutex> lk(g_timing_m);
        g_timing[d].held[idx] = ft;
    }
    if (s == kOk && movie_timing() && dslot(c.x[0])->format == 1 && !(fl & kFlagEndOfStream))
        nx::log("movie/a: pts %lld ms en t=%.0f ms", (long long)(t / 1000), since_start_ms(mono_ns()));
    if (s == kOk) {
        if (c.x[1]) *reinterpret_cast<int32_t*>(c.x[1]) = idx;
        if (c.x[2]) *reinterpret_cast<int64_t*>(c.x[2]) = t;
        if (c.x[3]) *reinterpret_cast<uint32_t*>(c.x[3]) = fl;
    }
    if (c.x[4]) *reinterpret_cast<int32_t*>(c.x[4]) = s == kOk ? d->ready_output_count() : 0;
    uint64_t obj = c.x[0];
    if (s == kOk) notify(c, obj, true);  // si quedan mas salidas listas, que el juego siga sacando
    ret(c, s);
}
// ---- outputFormat 3: el frame va a la NVNtexture del juego (apply_movie_texture.py) ----------------------------
// El juego pasa un Buffer de 8 bytes que apunta a su NVNtexture (0xC0 bytes, this+0x32f8 + idx*0xC0). El original
// (CopyOutputBufferToClientTextureAndRelease) copia el frame dentro; aquí queda como un TexelWrite del framework,
// el mismo camino que nvnTextureWriteTexels: se aplica a la imagen Vulkan antes del siguiente uso.
nx::nvn::Texture* find_client_texture(HostBuffer* b, uint64_t* guest) {
    if (!b || !b->base) return nullptr;
    if (b->capacity >= 8) {  // el puntero guardado en el Buffer
        uint64_t p = *reinterpret_cast<const uint64_t*>(b->base);
        if (auto* t = p ? nx::nvn::get<nx::nvn::Texture>(p) : nullptr) return *guest = p, t;
    }
    uint64_t p = reinterpret_cast<uint64_t>(b->base);  // o el Buffer apunta a la textura misma
    if (auto* t = nx::nvn::get<nx::nvn::Texture>(p)) return *guest = p, t;
    return nullptr;
}

Status write_frame_to_texture(Decoder* d, int index, uint64_t guest_buf) {
    size_t sz = 0;
    uint8_t* rgba = d->output_buffer(index, &sz);
    int w = 0, h = 0;
    d->output_dimensions(&w, &h);
    if (!rgba || w <= 0 || h <= 0 || sz < size_t(w) * h * 4) return kInvalidArgument;
    uint64_t guest = 0;
    nx::nvn::Texture* t = find_client_texture(buf(guest_buf), &guest);
    static std::atomic<int> missing{0};
    if (!t) {
        if (missing.fetch_add(1) < 5)
            nx::log("movie: GetOutputBuffer (formato 3): no encontre la NVNtexture del Buffer %llx (base %p, %zu bytes)",
                    (unsigned long long)guest_buf, (void*)buf(guest_buf)->base, buf(guest_buf)->capacity);
        return kOk;  // el frame se pierde, pero el juego sigue
    }
    uint32_t bw = 1, bh = 1, bytes = 0;
    nx::nvn::format_block(t->format, bw, bh, bytes);
    static std::mutex logged_lock;
    static std::map<uint64_t, bool> logged;
    {
        std::lock_guard lk(logged_lock);
        if (!logged[guest]) {
            logged[guest] = true;
            nx::log("movie: video %dx%d -> NVNtexture %llx (%ux%u, formato NVN %#x, %u bytes/texel)%s", w, h,
                    (unsigned long long)guest, t->width, t->height, t->format, bytes,
                    bytes == 4 && bw == 1 && bh == 1 ? "" : ": formato no soportado, el frame no se escribe");
        }
    }
    if (bytes != 4 || bw != 1 || bh != 1) return kOk;
    uint32_t cw = std::min<uint32_t>(uint32_t(w), t->width), ch = std::min<uint32_t>(uint32_t(h), t->height);
    if (!cw || !ch) return kOk;
    nx::nvn::Texture::TexelWrite tw;
    tw.level = 0, tw.x = 0, tw.y = 0, tw.z = 0, tw.w = cw, tw.h = ch, tw.d = 1;
    tw.data.resize(size_t(cw) * ch * 4);
    for (uint32_t y = 0; y < ch; y++) std::memcpy(tw.data.data() + size_t(y) * cw * 4, rgba + size_t(y) * w * 4, cw * 4);
    std::lock_guard lk(nx::nvn::objects_lock());
    // Un frame que cubre la textura entera reemplaza a los que no alcanzaron a aplicarse (si el juego no la dibuja,
    // no se acumulan 8 MB por frame).
    if (cw >= t->width && ch >= t->height) t->writes.clear();
    else if (t->writes.size() > 8) t->writes.erase(t->writes.begin());
    t->writes.push_back(std::move(tw));
    t->has_writes.store(true, std::memory_order_release);
    return kOk;
}

void fill_output(Decoder* d, int index, uint64_t guest_buf, Status* s) {
    size_t sz = 0;
    uint8_t* p = d->output_buffer(index, &sz);
    if (!p && sz) {
        *s = kInvalidArgument;
        return;
    }
    // Si el juego paso un Buffer con memoria propia que alcanza, se COPIA ahi (el juego reutiliza sus buffers y lee
    // despues de liberar el indice). Si no tiene memoria (Buffer()), o es chico, se le presta la del decoder.
    HostBuffer* b = buf(guest_buf);
    bool copy = !b->borrowed && b->base && b->capacity >= sz;
    static std::atomic<int> logged{0};
    if (movie_trace() && logged.fetch_add(1) < 6)
        nx::log("movie: GetOutputBuffer(%d): %zu bytes, buffer del juego base %p capacidad %zu -> %s", index, sz,
                (void*)b->base, b->capacity, copy ? "copia" : "prestado");
    if (copy) {
        if (sz) std::memcpy(b->base, p, sz);
        b->offset = 0;
        b->size = int32_t(sz);
    } else {
        b->point_to(p, sz, int32_t(sz), true);
    }
    *s = kOk;
}
void timing_shown(uint64_t obj, int idx) {
    if (!movie_timing() || dslot(obj)->format != 3) return;
    std::lock_guard<std::mutex> lk(g_timing_m);
    auto& vt = g_timing[dec(obj)];
    auto it = vt.held.find(idx);
    if (it != vt.held.end() && !it->second.shown) it->second.shown = true, it->second.shown_at = mono_ns();
}
void timing_release(uint64_t obj, int idx) {
    if (!movie_timing() || dslot(obj)->format != 3) return;
    std::lock_guard<std::mutex> lk(g_timing_m);
    auto& vt = g_timing[dec(obj)];
    auto it = vt.held.find(idx);
    if (it == vt.held.end()) return;
    const FrameTiming f = it->second;
    vt.held.erase(it);
    const uint64_t now = mono_ns(), work = f.decode_ns + f.convert_ns;
    double gap_ms = -1;
    if (f.shown) {
        if (vt.last_shown_at) {
            uint64_t g = f.shown_at - vt.last_shown_at;
            gap_ms = double(g) / 1e6;
            vt.max_gap_ns = std::max(vt.max_gap_ns, g);
        }
        vt.last_shown_at = f.shown_at;
        vt.shown++;
    } else {
        vt.dropped++;
    }
    vt.frames++;
    vt.sum_work_ns += work;
    vt.max_work_ns = std::max(vt.max_work_ns, work);
    nx::log("movie/t: t=%.0f pts %lld ms (+%lld)%s dec %.1f conv %.1f acquire %.1f ms -> %s %.1f ms despues%s%s",
            since_start_ms(f.acquired_at), (long long)(f.pts_us / 1000), vt.last_pts >= 0 ? (long long)((f.pts_us - vt.last_pts) / 1000) : 0LL,
            f.key ? " KEY" : "", double(f.decode_ns) / 1e6, double(f.convert_ns) / 1e6, double(f.acquire_call_ns) / 1e6,
            f.shown ? "mostrado" : "DESCARTADO (soltado sin mostrar)",
            double((f.shown ? f.shown_at : now) - f.acquired_at) / 1e6, gap_ms >= 0 ? ", entre mostrados " : "",
            gap_ms >= 0 ? (std::to_string(int(gap_ms + 0.5)) + " ms").c_str() : "");
    vt.last_pts = f.pts_us;
    if (vt.frames % 60 == 0) {
        nx::log("movie/t: resumen 60 frames: %llu mostrados, %llu descartados, dec+conv media %.1f max %.1f ms, "
                "mayor hueco entre mostrados %.1f ms",
                (unsigned long long)vt.shown, (unsigned long long)vt.dropped, double(vt.sum_work_ns) / 60 / 1e6,
                double(vt.max_work_ns) / 1e6, double(vt.max_gap_ns) / 1e6);
        vt.shown = vt.dropped = vt.max_work_ns = vt.sum_work_ns = vt.max_gap_ns = 0;
    }
}

void Decoder_GetOutputBuffer(nx::CPU& c) {
    timing_shown(c.x[0], int32_t(c.x[1]));
    Status s;
    if (dslot(c.x[0])->format == 3) s = write_frame_to_texture(dec(c.x[0]), int32_t(c.x[1]), c.x[2]);
    else fill_output(dec(c.x[0]), int32_t(c.x[1]), c.x[2], &s);
    ret(c, s);
}
void Decoder_GetOutputBuffer_fmt(nx::CPU& c) {
    timing_shown(c.x[0], int32_t(c.x[1]));
    Status s;
    if (dslot(c.x[0])->format == 3) s = write_frame_to_texture(dec(c.x[0]), int32_t(c.x[1]), c.x[2]);
    else fill_output(dec(c.x[0]), int32_t(c.x[1]), c.x[2], &s);
    if (s == kOk && c.x[3]) dec(c.x[0])->output_format(md(c.x[3]));
    ret(c, s);
}
void Decoder_GetOutputBufferFormat(nx::CPU& c) { ret(c, dec(c.x[0])->output_format(md(c.x[2]))); }
// GetOutputBufferSize(int* size, int* count), como DecoderImpl::GetOutputBufferSize (subsdk1+0x11f2d4):
// solo en DecoderMode 1 (si no, 0x1006); count = 1; size segun DecoderOutputFormat: 1 (PCM) canales * 0x1000,
// 4 (NV12) w*h + 2*(w/2)*(h/2), 2 (ARGB) w*h*4 (0x7e9000 sin dimensiones), otro 0x2000.
void Decoder_GetOutputBufferSize(nx::CPU& c) {
    DecoderSlot* sl = dslot(c.x[0]);
    auto* size = reinterpret_cast<int32_t*>(c.x[1]);
    auto* count = reinterpret_cast<int32_t*>(c.x[2]);
    if (!size || !count) return ret(c, Status(0x100b));
    if (sl->mode != 1) {
        nx::log("movie: Decoder::GetOutputBufferSize -> 0x1006 (mode %u)", sl->mode);
        return ret(c, Status(0x1006));
    }
    *count = 1;
    int w = 0, h = 0;
    sl->d->output_dimensions(&w, &h);
    int32_t bytes = 0x2000;
    if (sl->format == 1) {
        MediaDataStore f;
        sl->d->output_format(&f);
        const int32_t* ch = f.find<int32_t>("channel-count");
        bytes = (ch && *ch > 0 ? *ch : 2) << 12;
    } else if (sl->format == 4) {
        bytes = w && h ? w * h + 2 * (w / 2) * (h / 2) : 0x1fa400;
    } else if (sl->format == 2) {
        bytes = w && h ? w * h * 4 : 0x7e9000;
    }
    *size = bytes;
    nx::log("movie: Decoder::GetOutputBufferSize -> %d bytes, 1 buffer (formato %u)", bytes, sl->format);
    ret(c, kOk);
}
void Decoder_ReleaseOutputBufferIndex(nx::CPU& c) {
    uint64_t obj = c.x[0];
    timing_release(obj, int32_t(c.x[1]));
    Status s = dec(obj)->release_output_index(int32_t(c.x[1]));
    notify(c, obj, true);  // hay un slot de salida libre: puede salir el siguiente frame
    ret(c, s);
}
void Decoder_GetLastError(nx::CPU& c) { ret(c, dec(c.x[0])->last_error()); }

NX_HLE("_ZN5movie7DecoderC1ENS_11DecoderTypeENS_19DecoderOutputFormatENS_11DecoderModeEi", Decoder_ctor);
NX_HLE("_ZN5movie7DecoderD2Ev", Decoder_D2);
NX_HLE("_ZN5movie7DecoderD0Ev", Decoder_D0);
NX_HLE("_ZN5movie7Decoder9ConfigureEPNS_9MediaDataE", Decoder_Configure);
NX_HLE("_ZN5movie7Decoder20RegisterClientEventsEPNS_13DecoderEventsE", Decoder_RegisterClientEvents);
NX_HLE("_ZN5movie7Decoder15GetOutputFormatEPNS_9MediaDataE", Decoder_GetOutputFormat);
NX_HLE("_ZN5movie7Decoder5StartEv", Decoder_Start);
NX_HLE("_ZN5movie7Decoder4StopEv", Decoder_Stop);
NX_HLE("_ZN5movie7Decoder5ResetEv", Decoder_Reset);
NX_HLE("_ZN5movie7Decoder5FlushEv", Decoder_Flush);
NX_HLE("_ZN5movie7Decoder23AcquireInputBufferIndexEPiS1_", Decoder_AcquireInputBufferIndex);
NX_HLE("_ZN5movie7Decoder14GetInputBufferEiPNS_6BufferE", Decoder_GetInputBuffer);
NX_HLE("_ZN5movie7Decoder24SendInputBufferForDecodeEiiilj", Decoder_SendInputBufferForDecode);
NX_HLE("_ZN5movie7Decoder24AcquireOutputBufferIndexEPiPlPjS1_", Decoder_AcquireOutputBufferIndex);
NX_HLE("_ZN5movie7Decoder15GetOutputBufferEiPNS_6BufferE", Decoder_GetOutputBuffer);
NX_HLE("_ZN5movie7Decoder19GetOutputBufferSizeEPiS1_", Decoder_GetOutputBufferSize);
NX_HLE("_ZN5movie7Decoder24ReleaseOutputBufferIndexEi", Decoder_ReleaseOutputBufferIndex);
NX_HLE("_ZN5movie7Decoder12GetLastErrorEv", Decoder_GetLastError);
NX_HLE("_ZN5movie7Decoder15GetOutputBufferEiPNS_6BufferEPNS_9MediaDataE", Decoder_GetOutputBuffer_fmt);
NX_HLE("_ZN5movie7Decoder21GetOutputBufferFormatEiPNS_9MediaDataE", Decoder_GetOutputBufferFormat);

}  // namespace