// movie_core.h - Reimplementacion en host (FFmpeg) de la API movie:: de multimedia.nss.
//
// No depende del framework: aqui vive la logica (demux, decodificacion, colas de buffers).
// hle_movie.cpp la conecta con el codigo recompilado (vtables, objetos del guest).
//
// Modelo (igual que Android MediaExtractor/MediaCodec, en el que se basa multimedia.nss):
//   Extractor: abre un mp4, entrega muestras en Annex-B (H.264) o crudas (AAC), con csd-0/csd-1.
//   Decoder:   colas de buffers de entrada y salida con indices; el juego empuja y saca.
//
// Valores marcados TODO son supuestos (convencion Android) a confirmar contra subsdk1.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <map>
#include <memory>
#include <string>
#include <variant>
#include <vector>

namespace movie_host {

// ---- codigos de estado ------------------------------------------------------------------------------------------
// TODO: confirmar con movie::StatusToString (subsdk1). 0 = ok es casi seguro; el resto son placeholders.
enum Status : int32_t {
    kOk = 0,
    kEndOfStream = 0x9001,      // TODO
    kTryAgain = 0x9002,         // TODO: no hay buffer disponible ahora (INFO_TRY_AGAIN_LATER en Android)
    kFormatChanged = 0x9003,    // TODO: INFO_OUTPUT_FORMAT_CHANGED
    kInvalidArgument = 0x9004,  // TODO
    kInvalidState = 0x9005,     // TODO
    kUnsupported = 0x9006,      // TODO
    kIoError = 0x9007,          // TODO
    kUnknownError = 0x90FF,     // TODO
};
const char* status_name(Status s);

// Flags de buffer (convencion Android MediaCodec). TODO confirmar.
constexpr uint32_t kFlagKeyFrame = 1;
constexpr uint32_t kFlagCodecConfig = 2;
constexpr uint32_t kFlagEndOfStream = 4;

// ---- MediaData: diccionario clave -> valor (como android::AMessage) ---------------------------------------------
struct CodecParams;  // AVCodecParameters + time_base, opaco

class MediaDataStore {
public:
    using Rect = std::array<int32_t, 4>;
    using Bytes = std::vector<uint8_t>;
    using Value = std::variant<int32_t, int64_t, float, double, uint64_t, std::string, void*, Rect, Bytes>;

    void clear() { values_.clear(); codec.reset(); track = -1; }
    size_t count() const { return values_.size(); }
    bool contains(const std::string& k) const { return values_.count(k) != 0; }

    template <class T> void set(const std::string& k, T v) { values_[k] = Value(std::move(v)); }
    template <class T> const T* find(const std::string& k) const {
        auto it = values_.find(k);
        return it == values_.end() ? nullptr : std::get_if<T>(&it->second);
    }
    const std::map<std::string, Value>& all() const { return values_; }

    // Oculto para el juego: parametros exactos del stream, para que Decoder::Configure no dependa de csd-*.
    std::shared_ptr<const CodecParams> codec;
    // Oculto: de que pista del Extractor salio este formato (GetTrackConfiguration); -1 si no salio de ahi.
    int track = -1;

private:
    std::map<std::string, Value> values_;
};

// ---- Extractor --------------------------------------------------------------------------------------------------
class Extractor {
public:
    Extractor();
    ~Extractor();
    Extractor(const Extractor&) = delete;
    Extractor& operator=(const Extractor&) = delete;

    Status open_memory(std::string data);  // toma el contenido del archivo completo
    Status track_count(int* n) const;
    Status track_configuration(int track, MediaDataStore* out) const;
    Status select_track(int track);
    Status unselect_track(int track);
    Status seek_to(int64_t time_us);
    Status track_index_for_available_data(uint64_t* track);
    // Copia la muestra actual a dst. Si no cabe devuelve kInvalidArgument y *size con lo necesario.
    Status read(uint8_t* dst, size_t capacity, size_t* size);
    Status advance();
    Status sample_time(int64_t* time_us);
    Status sample_metadata(MediaDataStore* out);
    Status buffered_range(int64_t* start_us, int64_t* end_us) const;

private:
    struct Impl;
    std::unique_ptr<Impl> p_;
};

// ---- Decoder ----------------------------------------------------------------------------------------------------
enum class DecoderKind { Video, Audio };
enum class VideoFormat { NV12, RGBA };  // TODO: mapear desde movie::DecoderOutputFormat

class Decoder {
public:
    static constexpr int kInputSlots = 8;
    static constexpr int kOutputSlots = 4;
    static constexpr size_t kInputCapacity = 2u << 20;  // 2 MiB por muestra comprimida

    Decoder(DecoderKind kind, VideoFormat vfmt);
    ~Decoder();
    Decoder(const Decoder&) = delete;
    Decoder& operator=(const Decoder&) = delete;

    Status configure(const MediaDataStore& format);
    Status start();
    Status stop();
    Status reset();
    Status flush();

    Status acquire_input_index(int* index);
    uint8_t* input_buffer(int index, size_t* capacity);
    Status send_input(int index, int offset, int size, int64_t time_us, uint32_t flags);

    Status acquire_output_index(int* index, int64_t* time_us, uint32_t* flags);
    uint8_t* output_buffer(int index, size_t* size);
    Status release_output_index(int index);

    // Para el modo con eventos: decodifica lo pendiente hasta tener una salida lista (si se puede) y dice si hay
    // salidas esperando AcquireOutputBufferIndex.
    bool pump();
    bool has_free_input() const;
    int free_input_count() const;  // slots de entrada libres (para el 2o int* de AcquireInputBufferIndex)
    int ready_output_count() const;
    std::string debug_state() const;  // una linea con el estado de las colas, para el diagnostico periodico  // salidas listas esperando (para el 4o int* de AcquireOutputBufferIndex)

    Status output_format(MediaDataStore* out) const;
    void output_dimensions(int* width, int* height) const;
    // NX_MOVIE_TIMING: lo que costo producir la salida `index` (FFmpeg send/receive desde la salida anterior, y la
    // conversion de color) y si es keyframe. false si el indice no esta en uso.
    bool output_timing(int index, uint64_t* decode_ns, uint64_t* convert_ns, bool* key) const;
    Status last_error() const { return last_error_; }

private:
    struct Impl;
    std::unique_ptr<Impl> p_;
    Status last_error_ = kOk;
};

// ---- utilidades -------------------------------------------------------------------------------------------------
// Como android getNextNALUnit: avanza *data/*size al siguiente NAL (Annex-B) y devuelve su inicio y largo.
Status next_nal_unit(const uint8_t** data, size_t* size, const uint8_t** nal_start, size_t* nal_size);

}  // namespace movie_host