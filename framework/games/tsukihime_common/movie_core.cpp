// movie_core.cpp - ver movie_core.h
#include "movie_core.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <atomic>  // AP_MOVIE_HW
#include <thread>  // AP_MOVIE_HW_WAIT

#include <nx/runtime.h>  // AP_MOVIE_HW: nx::log

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavcodec/bsf.h>
#include <libavformat/avformat.h>
#include <libavutil/opt.h>
#include <libswresample/swresample.h>
#include <libswscale/swscale.h>
}

namespace movie_host {

const char* status_name(Status s) {
    switch (s) {
        case kOk: return "Ok";
        case kEndOfStream: return "EndOfStream";
        case kTryAgain: return "TryAgain";
        case kFormatChanged: return "FormatChanged";
        case kInvalidArgument: return "InvalidArgument";
        case kInvalidState: return "InvalidState";
        case kUnsupported: return "Unsupported";
        case kIoError: return "IoError";
        default: return "UnknownError";
    }
}

static constexpr AVRational kMicros{1, 1000000};

struct CodecParams {
    AVCodecParameters* par = avcodec_parameters_alloc();
    ~CodecParams() { avcodec_parameters_free(&par); }
};

static void set_extradata(AVCodecParameters* par, const std::vector<uint8_t>& bytes) {
    av_freep(&par->extradata);
    par->extradata_size = 0;
    if (bytes.empty()) return;
    par->extradata = static_cast<uint8_t*>(av_mallocz(bytes.size() + AV_INPUT_BUFFER_PADDING_SIZE));
    std::memcpy(par->extradata, bytes.data(), bytes.size());
    par->extradata_size = int(bytes.size());
}

// avcC (mp4) -> SPS y PPS en Annex-B, como los csd-0 / csd-1 de Android.
static bool avcc_to_annexb(const uint8_t* p, size_t n, std::vector<uint8_t>* sps, std::vector<uint8_t>* pps) {
    static const uint8_t sc[4] = {0, 0, 0, 1};
    if (n < 7 || p[0] != 1) return false;
    size_t i = 5;
    for (int pass = 0; pass < 2; pass++) {
        if (i >= n) return false;
        int count = pass == 0 ? (p[i] & 0x1F) : p[i];
        i++;
        std::vector<uint8_t>* out = pass == 0 ? sps : pps;
        for (int k = 0; k < count; k++) {
            if (i + 2 > n) return false;
            size_t len = (size_t(p[i]) << 8) | p[i + 1];
            i += 2;
            if (i + len > n) return false;
            out->insert(out->end(), sc, sc + 4);
            out->insert(out->end(), p + i, p + i + len);
            i += len;
        }
    }
    return true;
}

// ================================================================================================= Extractor

struct Extractor::Impl {
    mutable std::recursive_mutex m;  // el juego llama desde varios hilos
    std::string data;
    int64_t pos = 0;
    // AP_MOVIE_STREAM: a reader in place of `data`; reads go through one read-ahead chunk
    std::function<int64_t(uint64_t, void*, size_t)> rd;
    uint64_t rsize = 0;
    bool stream = false;
    std::vector<uint8_t> ra;
    uint64_t ra_off = 0;
    static size_t ra_chunk() {
        static const size_t n = [] {
            const char* e = std::getenv("NX_MOVIE_IO_KB");
            long v = e ? std::strtol(e, nullptr, 10) : 4096;
            if (v < 64 || v > 65536) v = 4096;
            return size_t(v) << 10;
        }();
        return n;
    }
    int stream_read(uint8_t* buf, int n) {
        if (pos < 0 || uint64_t(pos) >= rsize) return AVERROR_EOF;
        if (uint64_t(pos) < ra_off || uint64_t(pos) >= ra_off + ra.size()) {
            const size_t want = size_t(std::min<uint64_t>(ra_chunk(), rsize - uint64_t(pos)));
            ra.resize(want);
            const int64_t got = rd(uint64_t(pos), ra.data(), want);
            if (got <= 0) {
                ra.clear();
                return AVERROR(EIO);
            }
            ra.resize(size_t(got));
            ra_off = uint64_t(pos);
        }
        const size_t k = size_t(std::min<uint64_t>(uint64_t(n), ra_off + ra.size() - uint64_t(pos)));
        std::memcpy(buf, ra.data() + (uint64_t(pos) - ra_off), k);
        pos += int64_t(k);
        return int(k);
    }
    int64_t stream_seek(int64_t off, int whence) {
        if (whence == AVSEEK_SIZE) return int64_t(rsize);
        int64_t base = (whence & 3) == SEEK_CUR ? pos : (whence & 3) == SEEK_END ? int64_t(rsize) : 0;
        int64_t np = base + off;
        if (np < 0 || np > int64_t(rsize)) return -1;
        return pos = np;
    }
    AVIOContext* io = nullptr;
    AVFormatContext* fmt = nullptr;
    std::vector<bool> selected;
    std::vector<AVBSFContext*> bsf;  // h264_mp4toannexb por stream de video H.264
    AVPacket* raw = av_packet_alloc();
    AVPacket* cur = av_packet_alloc();
    bool have = false, eof = false;
    int64_t off_us = 0, last_us = 0;  // AP_TITLE_FADE: time added after replace_memory; last time given to the game

    ~Impl() {
        for (auto* b : bsf) av_bsf_free(&b);
        if (fmt) avformat_close_input(&fmt);
        if (io) {
            av_freep(&io->buffer);
            avio_context_free(&io);
        }
        av_packet_free(&raw);
        av_packet_free(&cur);
    }

    static int read_cb(void* o, uint8_t* buf, int n) {
        auto* s = static_cast<Impl*>(o);
        if (s->stream) return s->stream_read(buf, n);  // AP_MOVIE_STREAM
        int64_t left = int64_t(s->data.size()) - s->pos;
        if (left <= 0) return AVERROR_EOF;
        int k = int(std::min<int64_t>(n, left));
        std::memcpy(buf, s->data.data() + s->pos, k);
        s->pos += k;
        return k;
    }
    static int64_t seek_cb(void* o, int64_t off, int whence) {
        auto* s = static_cast<Impl*>(o);
        if (s->stream) return s->stream_seek(off, whence);  // AP_MOVIE_STREAM
        if (whence == AVSEEK_SIZE) return int64_t(s->data.size());
        int64_t base = (whence & 3) == SEEK_CUR ? s->pos : (whence & 3) == SEEK_END ? int64_t(s->data.size()) : 0;
        int64_t np = base + off;
        if (np < 0 || np > int64_t(s->data.size())) return -1;
        return s->pos = np;
    }

    int64_t to_us(const AVPacket* pkt) const {
        int64_t ts = pkt->pts != AV_NOPTS_VALUE ? pkt->pts : pkt->dts;
        if (ts == AV_NOPTS_VALUE) return 0;
        return av_rescale_q(ts, fmt->streams[pkt->stream_index]->time_base, kMicros) + off_us;
    }

    // Deja en `cur` la siguiente muestra de una pista seleccionada.
    void fill() {
        have = false;
        av_packet_unref(cur);
        while (!eof) {
            int r = av_read_frame(fmt, raw);
            if (r < 0) {
                eof = true;
                break;
            }
            int si = raw->stream_index;
            if (si < 0 || si >= int(selected.size()) || !selected[si]) {
                av_packet_unref(raw);
                continue;
            }
            if (bsf[si]) {
                if (av_bsf_send_packet(bsf[si], raw) < 0) {
                    av_packet_unref(raw);
                    continue;
                }
                if (av_bsf_receive_packet(bsf[si], cur) < 0) continue;  // EAGAIN: necesita otro paquete
                cur->stream_index = si;
            } else {
                av_packet_move_ref(cur, raw);
            }
            have = true;
            return;
        }
    }
    void ensure() {
        if (!have && !eof) fill();
    }
};

Extractor::Extractor() : p_(new Impl) {}
Extractor::~Extractor() = default;

// AP_MOVIE_STREAM (apply_movie_stream.py): the demuxer reads the file through `reader` instead of from memory.
Status Extractor::open_stream(uint64_t size, std::function<int64_t(uint64_t, void*, size_t)> reader) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (p_->fmt) return kInvalidState;
    if (!reader || size == 0) return kInvalidArgument;
    p_->rd = std::move(reader);
    p_->rsize = size;
    p_->stream = true;
    return open_memory(std::string());  // the AVIO callbacks go to the reader while p_->stream is set
}

Status Extractor::open_memory(std::string data) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (p_->fmt) return kInvalidState;
    p_->data = std::move(data);
    const int kBuf = 1 << 16;
    auto* buf = static_cast<uint8_t*>(av_malloc(kBuf));
    p_->io = avio_alloc_context(buf, kBuf, 0, p_.get(), &Impl::read_cb, nullptr, &Impl::seek_cb);
    p_->fmt = avformat_alloc_context();
    p_->fmt->pb = p_->io;
    if (avformat_open_input(&p_->fmt, nullptr, nullptr, nullptr) < 0) return kIoError;  // libera fmt si falla
    if (avformat_find_stream_info(p_->fmt, nullptr) < 0) return kIoError;
    unsigned n = p_->fmt->nb_streams;
    p_->selected.assign(n, false);
    p_->bsf.assign(n, nullptr);
    for (unsigned i = 0; i < n; i++) {
        AVStream* st = p_->fmt->streams[i];
        st->discard = AVDISCARD_ALL;
        if (st->codecpar->codec_id == AV_CODEC_ID_H264 && st->codecpar->extradata_size && st->codecpar->extradata[0] == 1) {
            const AVBitStreamFilter* f = av_bsf_get_by_name("h264_mp4toannexb");
            AVBSFContext* b = nullptr;
            if (f && av_bsf_alloc(f, &b) == 0) {
                avcodec_parameters_copy(b->par_in, st->codecpar);
                b->time_base_in = st->time_base;
                if (av_bsf_init(b) == 0) p_->bsf[i] = b;
                else av_bsf_free(&b);
            }
        }
    }
    return kOk;
}

// AP_TITLE_LIVE (apply_title_live.py)
Status Extractor::replace_memory(std::string data, int64_t base_us) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->fmt) return kInvalidState;
    Impl n;  // temporary demuxer: it is validated before touching the one that is playing
    n.data = std::move(data);
    const int kBuf = 1 << 16;
    n.io = avio_alloc_context(static_cast<uint8_t*>(av_malloc(kBuf)), kBuf, 0, &n, &Impl::read_cb, nullptr, &Impl::seek_cb);
    n.fmt = avformat_alloc_context();
    n.fmt->pb = n.io;
    if (avformat_open_input(&n.fmt, nullptr, nullptr, nullptr) < 0) return kIoError;
    if (avformat_find_stream_info(n.fmt, nullptr) < 0) return kIoError;
    if (n.fmt->nb_streams != p_->fmt->nb_streams) return kInvalidArgument;
    for (unsigned i = 0; i < n.fmt->nb_streams; i++) {
        const AVCodecParameters* a = p_->fmt->streams[i]->codecpar;
        const AVCodecParameters* b = n.fmt->streams[i]->codecpar;
        if (a->codec_type != b->codec_type || a->codec_id != b->codec_id || a->width != b->width || a->height != b->height ||
            a->sample_rate != b->sample_rate)
            return kInvalidArgument;
    }
    n.bsf.assign(n.fmt->nb_streams, nullptr);
    for (unsigned i = 0; i < n.fmt->nb_streams; i++) {
        AVStream* st = n.fmt->streams[i];
        st->discard = p_->selected[i] ? AVDISCARD_DEFAULT : AVDISCARD_ALL;
        if (st->codecpar->codec_id == AV_CODEC_ID_H264 && st->codecpar->extradata_size && st->codecpar->extradata[0] == 1) {
            const AVBitStreamFilter* f = av_bsf_get_by_name("h264_mp4toannexb");
            AVBSFContext* b = nullptr;
            if (f && av_bsf_alloc(f, &b) == 0) {
                avcodec_parameters_copy(b->par_in, st->codecpar);
                b->time_base_in = st->time_base;
                if (av_bsf_init(b) == 0) n.bsf[i] = b;
                else av_bsf_free(&b);
            }
        }
    }
    // everything fits: the old demuxer is freed and the new one (and its data) moves in
    for (auto*& b : p_->bsf) av_bsf_free(&b);
    avformat_close_input(&p_->fmt);
    av_freep(&p_->io->buffer);
    avio_context_free(&p_->io);
    p_->data = std::move(n.data);
    p_->pos = n.pos;
    p_->io = n.io;
    p_->io->opaque = p_.get();
    p_->fmt = n.fmt;
    p_->bsf = std::move(n.bsf);
    n.io = nullptr, n.fmt = nullptr, n.bsf.clear();
    av_packet_unref(p_->cur);
    av_packet_unref(p_->raw);
    p_->have = false;
    p_->eof = false;
    // AP_TITLE_FADE: the new video goes on from the time the old one had reached (30 fps)
    const int64_t before = base_us >= 0 ? base_us : p_->last_us;  // AP_TITLE_SYNC
    p_->off_us = 0;
    p_->fill();
    if (p_->have) p_->off_us = before + 33333 - p_->to_us(p_->cur);
    return kOk;
}

Status Extractor::track_count(int* n) const {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->fmt) return kInvalidState;
    *n = int(p_->fmt->nb_streams);
    return kOk;
}

Status Extractor::track_configuration(int track, MediaDataStore* out) const {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->fmt) return kInvalidState;
    if (track < 0 || track >= int(p_->fmt->nb_streams)) return kInvalidArgument;
    AVStream* st = p_->fmt->streams[track];
    const AVCodecParameters* par = st->codecpar;
    out->clear();
    out->track = track;
    auto cp = std::make_shared<CodecParams>();
    avcodec_parameters_copy(cp->par, par);

    int64_t dur = st->duration != AV_NOPTS_VALUE ? av_rescale_q(st->duration, st->time_base, kMicros)
                                                 : (p_->fmt->duration != AV_NOPTS_VALUE ? p_->fmt->duration : 0);
    out->set<int64_t>("durationUs", dur);

    if (par->codec_type == AVMEDIA_TYPE_VIDEO) {
        out->set<std::string>("mime", par->codec_id == AV_CODEC_ID_H264 ? "video/avc" : avcodec_get_name(par->codec_id));
        out->set<int32_t>("width", par->width);
        out->set<int32_t>("height", par->height);
        if (st->avg_frame_rate.num && st->avg_frame_rate.den)
            out->set<int32_t>("frame-rate", int32_t(av_q2d(st->avg_frame_rate) + 0.5));
        std::vector<uint8_t> sps, pps;
        if (par->codec_id == AV_CODEC_ID_H264 && avcc_to_annexb(par->extradata, size_t(par->extradata_size), &sps, &pps)) {
            out->set<MediaDataStore::Bytes>("csd-0", sps);
            out->set<MediaDataStore::Bytes>("csd-1", pps);
            std::vector<uint8_t> annexb(sps);
            annexb.insert(annexb.end(), pps.begin(), pps.end());
            set_extradata(cp->par, annexb);  // las muestras salen en Annex-B: el decoder debe esperar lo mismo
        }
    } else if (par->codec_type == AVMEDIA_TYPE_AUDIO) {
        out->set<std::string>("mime", par->codec_id == AV_CODEC_ID_AAC ? "audio/mp4a-latm" : avcodec_get_name(par->codec_id));
        out->set<int32_t>("sample-rate", par->sample_rate);
        out->set<int32_t>("channel-count", par->ch_layout.nb_channels);
        if (par->extradata_size)
            out->set<MediaDataStore::Bytes>("csd-0", MediaDataStore::Bytes(par->extradata, par->extradata + par->extradata_size));
    } else {
        out->set<std::string>("mime", "application/octet-stream");
    }
    out->codec = cp;
    return kOk;
}

Status Extractor::select_track(int track) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->fmt) return kInvalidState;
    if (track < 0 || track >= int(p_->selected.size())) return kInvalidArgument;
    p_->selected[track] = true;
    p_->fmt->streams[track]->discard = AVDISCARD_DEFAULT;
    return kOk;
}

Status Extractor::unselect_track(int track) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->fmt) return kInvalidState;
    if (track < 0 || track >= int(p_->selected.size())) return kInvalidArgument;
    p_->selected[track] = false;
    p_->fmt->streams[track]->discard = AVDISCARD_ALL;
    if (p_->have && p_->cur->stream_index == track) p_->fill();
    return kOk;
}

Status Extractor::seek_to(int64_t time_us) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->fmt) return kInvalidState;
    if (av_seek_frame(p_->fmt, -1, av_rescale_q(time_us, kMicros, AV_TIME_BASE_Q), AVSEEK_FLAG_BACKWARD) < 0)
        return kIoError;
    p_->off_us = 0;  // AP_TITLE_FADE: a seek is an absolute time again
    for (auto* b : p_->bsf)
        if (b) av_bsf_flush(b);
    p_->eof = false;
    p_->fill();
    return kOk;
}

Status Extractor::track_index_for_available_data(uint64_t* track) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->fmt) return kInvalidState;
    p_->ensure();
    if (!p_->have) return kEndOfStream;
    *track = uint64_t(p_->cur->stream_index);
    return kOk;
}

Status Extractor::read(uint8_t* dst, size_t capacity, size_t* size) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->fmt) return kInvalidState;
    p_->ensure();
    if (!p_->have) return kEndOfStream;
    *size = size_t(p_->cur->size);
    if (*size > capacity) return kInvalidArgument;
    std::memcpy(dst, p_->cur->data, *size);
    return kOk;
}

Status Extractor::advance() {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->fmt) return kInvalidState;
    p_->ensure();
    if (!p_->have) return kEndOfStream;
    p_->fill();
    return p_->have ? kOk : kEndOfStream;
}

Status Extractor::sample_time(int64_t* time_us) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->fmt) return kInvalidState;
    p_->ensure();
    if (!p_->have) return kEndOfStream;
    *time_us = p_->to_us(p_->cur);
    p_->last_us = *time_us;  // AP_TITLE_FADE
    return kOk;
}

Status Extractor::sample_metadata(MediaDataStore* out) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->fmt) return kInvalidState;
    p_->ensure();
    if (!p_->have) return kEndOfStream;
    out->clear();
    out->set<int64_t>("timeUs", p_->to_us(p_->cur));
    out->set<int32_t>("is-sync-frame", (p_->cur->flags & AV_PKT_FLAG_KEY) ? 1 : 0);
    out->set<int32_t>("track-index", p_->cur->stream_index);
    return kOk;
}

Status Extractor::buffered_range(int64_t* start_us, int64_t* end_us) const {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->fmt) return kInvalidState;
    *start_us = 0;  // el archivo completo esta en memoria
    *end_us = p_->fmt->duration != AV_NOPTS_VALUE ? p_->fmt->duration : 0;
    return kOk;
}

// ================================================================================================= Decoder

// Muestras comprimidas que un decoder acepta sin decodificar (despues el juego recibe TryAgain / sin entrada libre).
// 64, no 16: en los mp4 del juego el audio y el video van intercalados en bloques de ~1 s (30 frames de video). Con 16
// el video llenaba su cupo antes de que el Extractor llegara al bloque de audio siguiente; el reloj del juego (que
// sigue al audio) se frenaba y despues saltaba ~0.5 s, y el juego descartaba 11-18 frames seguidos cada segundo
// (medido con NX_MOVIE_TIMING: 16 -> 16-33 de 60 frames mostrados; 64 -> 60 de 60, sin tirones).
// NX_MOVIE_PENDING=<n> (4..512) lo cambia.
size_t pending_limit() {
    static const size_t n = [] {
        const char* e = std::getenv("NX_MOVIE_PENDING");
        long v = e ? std::strtol(e, nullptr, 10) : 64;
        if (v < 4 || v > 512) v = 64;
        if (e) std::fprintf(stderr, "movie: NX_MOVIE_PENDING = %ld\n", v);
        return size_t(v);
    }();
    return n;
}

// AP_MOVIE_AUDIO_QUEUE: audio decoders queue up to NX_MOVIE_AUDIO_PENDING samples (default 1024, 64..8192). At the
// start of a movie the game holds every audio output until the video gives its first picture, and the leading audio
// block of the mp4 has to be queued before the feeder reaches the first video sample; with the video limit (64) the
// audio filled up first and the movie stood still until the game's ~5 s timeout.
size_t pending_limit_for(DecoderKind kind) {
    if (kind != DecoderKind::Audio) return pending_limit();
    static const size_t n = [] {
        const char* e = std::getenv("NX_MOVIE_AUDIO_PENDING");
        long v = e && *e ? std::strtol(e, nullptr, 10) : 1024;
        if (v < 64 || v > 8192) v = 1024;
        if (e && *e) std::fprintf(stderr, "movie: NX_MOVIE_AUDIO_PENDING = %ld\n", v);
        return size_t(v);
    }();
    return n;
}

// AP_TITLE_SMOOTH (apply_title_smooth.py): one closed H.264 decoder kept (flushed) for the next start() with the same
// parameters, so the game's stop / configure / start of the title video does not call avcodec_open2 again.
static std::mutex g_park_m;
static AVCodecContext* g_parked = nullptr;
static bool park_matches(const AVCodecContext* c, const AVCodecParameters* p) {
    return c && p && c->codec_id == p->codec_id && c->width == p->width && c->height == p->height &&
           c->extradata_size == p->extradata_size &&
           (p->extradata_size == 0 || std::memcmp(c->extradata, p->extradata, size_t(p->extradata_size)) == 0);
}

// AP_MOVIE_HW (apply_movie_hw.py): H.264 video through the device's hardware decoder (FFmpeg h264_mediacodec, NDK).
// Android: on unless NX_MOVIE_HW=0. Other systems: off (there is no MediaCodec).
static std::atomic<bool> g_hw_bad{false};  // a hardware decoder failed: software for the rest of the session
static bool movie_hw_wanted() {
    static const bool on = [] {
#ifdef __ANDROID__
        const char* e = std::getenv("NX_MOVIE_HW");
        const bool v = !(e && std::strcmp(e, "0") == 0);
        if (!v) nx::log("movie: NX_MOVIE_HW=0: videos decoded in software");
        return v;
#else
        return false;
#endif
    }();
    return on;
}
// AP_MOVIE_HW_WAIT: how long one call may wait for a MediaCodec picture (NX_MOVIE_HW_WAIT_MS, 0..200, default 40).
static int movie_hw_wait_ms() {
    static const int ms = [] {
        const char* e = std::getenv("NX_MOVIE_HW_WAIT_MS");
        long v = e && *e ? std::strtol(e, nullptr, 10) : 40;
        if (v < 0 || v > 200) v = 40;
        if (e && *e) nx::log("movie: NX_MOVIE_HW_WAIT_MS = %ld", v);
        return int(v);
    }();
    return ms;
}
static bool movie_hw_stats() {  // AP_MOVIE_HW_WAIT: NX_MOVIE_TIMING=1 also logs MediaCodec once a second
    static const bool on = [] {
        const char* e = std::getenv("NX_MOVIE_TIMING");
        return e && std::strcmp(e, "0") != 0;
    }();
    return on;
}
// AP_MOVIE_HW_SOLO: NX_MOVIE_HW_PARK=1 keeps parking MediaCodec decoders (default: freed, one alive at a time).
static bool movie_hw_park() {
    static const bool on = [] {
        const char* e = std::getenv("NX_MOVIE_HW_PARK");
        bool v = e && std::strcmp(e, "0") != 0;
        if (v) nx::log("movie: NX_MOVIE_HW_PARK=1 (MediaCodec decoders are parked)");
        return v;
    }();
    return on;
}
static bool is_hw_ctx(const AVCodecContext* c) {
    return c && c->codec && c->codec->name && std::strcmp(c->codec->name, "h264_mediacodec") == 0;
}
static AVCodecContext* open_hw_h264(const AVCodecParameters* par) {
    const AVCodec* hc = avcodec_find_decoder_by_name("h264_mediacodec");
    if (!hc) {
        static std::atomic<bool> said{false};
        if (!said.exchange(true)) nx::log("movie: this FFmpeg has no h264_mediacodec: videos decoded in software");
        return nullptr;
    }
    AVCodecContext* c = avcodec_alloc_context3(hc);
    if (!c) return nullptr;
    if (avcodec_parameters_to_context(c, par) < 0) {
        avcodec_free_context(&c);
        return nullptr;
    }
    c->pkt_timebase = kMicros;
    c->flags |= AV_CODEC_FLAG_LOW_DELAY;  // AP_MOVIE_HW_WAIT: "low-latency" where MediaCodec has it
    AVDictionary* opts = nullptr;
    av_dict_set(&opts, "ndk_codec", "1", 0);  // NDK AMediaCodec, no Java VM needed
    const int r = avcodec_open2(c, hc, &opts);
    av_dict_free(&opts);
    if (r < 0) {
        char e[96];
        av_strerror(r, e, sizeof e);
        nx::log("movie: MediaCodec did not open for %dx%d H.264 (%s): software decoder", par->width, par->height, e);
        avcodec_free_context(&c);
        return nullptr;
    }
    nx::log("movie: video %dx%d decoded by MediaCodec (hardware); NX_MOVIE_HW=0 for software", par->width, par->height);
    return c;
}

struct Decoder::Impl {
    mutable std::recursive_mutex m;  // hilos de entrada y de salida del juego, a la vez
    DecoderKind kind;
    VideoFormat vfmt;
    AVCodecParameters* par = nullptr;  // de configure()
    AVCodecContext* ctx = nullptr;
    SwsContext* sws = nullptr;
    SwrContext* swr = nullptr;
    AVFrame* frame = av_frame_alloc();
    AVPacket* pkt = av_packet_alloc();

    struct Input {
        std::vector<uint8_t> mem = std::vector<uint8_t>(kInputCapacity);
        bool free = true;
    };
    struct Output {
        std::vector<uint8_t> mem;
        size_t size = 0;
        int64_t time_us = 0;
        uint32_t flags = 0;
        bool free = true;  // false: en la cola de listos o en manos del juego
        uint64_t decode_ns = 0, convert_ns = 0;  // NX_MOVIE_TIMING (output_timing)
    };
    Input in[kInputSlots];
    Output out[kOutputSlots];
    std::deque<AVPacket*> pending;  // entradas copiadas, aun sin mandar al decoder
    std::deque<int> ready;          // salidas listas para AcquireOutputBufferIndex
    bool started = false, eos_in = false, drain_sent = false, eos_out = false;
    int64_t last_out_us = -1;  // AP_TITLE_SYNC: time of the last output the game acquired
    int width = 0, height = 0;
    uint64_t work_ns = 0;  // tiempo en avcodec_send_packet / receive_frame desde la ultima salida guardada
    bool hw = false;                // AP_MOVIE_HW: ctx is the hardware decoder
    bool hw_out = false;            // AP_MOVIE_HW: it already gave a picture
    std::deque<AVPacket*> hw_sent;  // AP_MOVIE_HW: packets it took before its first picture (sent again on a fallback)
    int hw_inflight = 0;   // AP_MOVIE_HW_WAIT: packets given to MediaCodec whose picture has not come out yet
    int hw_lat = -1;       // AP_MOVIE_HW_WAIT: packets it keeps before giving a picture (-1: not known yet)
    uint64_t hw_open_ns = 0, hw_rep_ns = 0;  // AP_MOVIE_HW_WAIT: when it was opened; last stats line
    int hw_n_in = 0, hw_n_out = 0, hw_n_wait = 0, hw_n_gaveup = 0, hw_tot_in = 0, hw_pre_gaveup = 0;
    uint64_t hw_wait_ns = 0;
    // AP_MOVIE_HW_WAIT: MediaCodec finishes a picture a few ms after taking the packet, outside the game's calls, and the
    // game is only told from inside them. If it holds more than its latency, a picture is on its way: wait for it here,
    // 1 ms at a time, up to movie_hw_wait_ms() per call. Before the first picture the latency is unknown: wait too.
    bool hw_should_wait() const {  // before the first picture: at most 3 waits that give up (then it needs input)
        return hw_inflight > 0 && (hw_lat < 0 ? hw_pre_gaveup < 3 : (hw_inflight > hw_lat || drain_sent));
    }
    bool hw_wait(uint64_t& deadline) {
        const uint64_t now = now_ns();
        if (!deadline) {
            deadline = now + uint64_t(movie_hw_wait_ms()) * 1000000ull;
            hw_n_wait++;
        }
        if (now >= deadline) {
            hw_n_gaveup++;
            if (hw_lat < 0) hw_pre_gaveup++;
            if (hw_lat >= 0) hw_inflight = hw_lat;  // what is left is its latency: do not wait for it again
            return false;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
        hw_wait_ns += now_ns() - now;
        return true;
    }
    void hw_report() {  // NX_MOVIE_TIMING: once a second
        if (!movie_hw_stats()) return;
        const uint64_t now = now_ns();
        if (!hw_rep_ns) hw_rep_ns = now;
        if (now - hw_rep_ns < 1000000000ull) return;
        nx::log("movie/hw: %.1f s: %d packets in, %d pictures out, %d waits (%.0f ms, %d gave up), in flight %d, latency %d, pending %zu",
                double(now - hw_rep_ns) / 1e9, hw_n_in, hw_n_out, hw_n_wait, double(hw_wait_ns) / 1e6, hw_n_gaveup,
                hw_inflight, hw_lat, pending.size());
        hw_rep_ns = now;
        hw_n_in = hw_n_out = hw_n_wait = hw_n_gaveup = 0;
        hw_wait_ns = 0;
    }
    void hw_forget() {
        for (auto* q : hw_sent) av_packet_free(&q);
        hw_sent.clear();
    }
    static uint64_t now_ns() {
        return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                            std::chrono::steady_clock::now().time_since_epoch()).count());
    }

    explicit Impl(DecoderKind k, VideoFormat f) : kind(k), vfmt(f) {}
    ~Impl() {
        clear_queues();
        close();
        avcodec_parameters_free(&par);
        av_frame_free(&frame);
        av_packet_free(&pkt);
    }
    void close() {
        hw_forget();  // AP_MOVIE_HW
        if (ctx && is_hw_ctx(ctx) && g_hw_bad) avcodec_free_context(&ctx);  // AP_MOVIE_HW: not kept
        if (ctx && is_hw_ctx(ctx) && !movie_hw_park()) avcodec_free_context(&ctx);  // AP_MOVIE_HW_SOLO: not parked
        if (ctx && kind == DecoderKind::Video && ctx->codec_id == AV_CODEC_ID_H264 && avcodec_is_open(ctx)) {  // AP_TITLE_SMOOTH
            avcodec_flush_buffers(ctx);
            std::lock_guard<std::mutex> lk(g_park_m);
            avcodec_free_context(&g_parked);
            g_parked = ctx;
            ctx = nullptr;
        }
        avcodec_free_context(&ctx);
        sws_freeContext(sws);
        sws = nullptr;
        swr_free(&swr);
        started = false;
    }
    void clear_queues() {
        for (auto* q : pending) av_packet_free(&q);
        pending.clear();
        ready.clear();
        for (auto& i : in) i.free = true;
        for (auto& o : out) o.free = true;
        hw_forget();  // AP_MOVIE_HW
        hw_inflight = 0;  // AP_MOVIE_HW_WAIT
        eos_in = drain_sent = eos_out = false;
    }
    int free_output() const {
        for (int i = 0; i < kOutputSlots; i++)
            if (out[i].free) return i;
        return -1;
    }

    Status open() {
        if (!par) return kInvalidState;
        const AVCodec* codec = avcodec_find_decoder(par->codec_id);
        if (!codec) return kUnsupported;
        {  // AP_TITLE_SMOOTH
            std::lock_guard<std::mutex> lk(g_park_m);
            if (g_parked && park_matches(g_parked, par)) {
                ctx = g_parked;
                g_parked = nullptr;
            }
        }
        hw = hw_out = false;  // AP_MOVIE_HW
        hw_inflight = 0, hw_lat = -1, hw_tot_in = 0, hw_pre_gaveup = 0, hw_open_ns = now_ns(), hw_rep_ns = 0;  // AP_MOVIE_HW_WAIT
        if (ctx && is_hw_ctx(ctx) && g_hw_bad) avcodec_free_context(&ctx);  // AP_MOVIE_HW
        if (ctx) {
            ctx->pkt_timebase = kMicros;
            hw = is_hw_ctx(ctx);  // AP_MOVIE_HW
            started = true;
            return kOk;
        }
        if (kind == DecoderKind::Video && par->codec_id == AV_CODEC_ID_H264 && movie_hw_wanted() && !g_hw_bad) {  // AP_MOVIE_HW
            {  // AP_MOVIE_HW_SOLO: one MediaCodec video decoder at a time
                std::lock_guard<std::mutex> lk(g_park_m);
                if (g_parked && is_hw_ctx(g_parked)) {
                    avcodec_free_context(&g_parked);
                    if (movie_hw_stats()) nx::log("movie/hw: freed the parked MediaCodec decoder before opening a new one");
                }
            }
            const uint64_t hw_t0 = now_ns();  // AP_MOVIE_HW_SOLO
            ctx = open_hw_h264(par);
            if (movie_hw_stats()) nx::log("movie/hw: opening MediaCodec took %.0f ms", (now_ns() - hw_t0) / 1e6);  // AP_MOVIE_HW_SOLO
            if (ctx) {
                hw = true;
                started = true;
                return kOk;
            }
        }
        return open_sw(codec);
    }
    Status open_sw(const AVCodec* codec) {  // AP_MOVIE_HW: the software decoder (what open() always did)
        ctx = avcodec_alloc_context3(codec);
        if (avcodec_parameters_to_context(ctx, par) < 0) return kInvalidArgument;
        ctx->pkt_timebase = kMicros;
        if (avcodec_open2(ctx, codec, nullptr) < 0) return kUnsupported;
        started = true;
        return kOk;
    }
    // AP_MOVIE_HW: the hardware decoder failed: software from here on (and for the rest of the session). Before its
    // first picture its packets go back to the front of the queue, so the software decoder starts from the same place.
    bool hw_fallback(const char* why) {
        if (!hw) return false;
        nx::log("movie: MediaCodec failed (%s): software decoder from now on%s", why,
                hw_out ? "" : ", its packets are decoded again");
        g_hw_bad = true;
        avcodec_free_context(&ctx);
        hw = false;
        drain_sent = false;
        const AVCodec* sc = avcodec_find_decoder(par->codec_id);
        if (!sc || open_sw(sc) != kOk) {
            hw_forget();
            return false;
        }
        if (!hw_out) {
            while (!hw_sent.empty()) {
                pending.push_front(hw_sent.back());
                hw_sent.pop_back();
            }
        }
        hw_forget();
        return true;
    }

    // Convierte el frame decodificado al slot de salida `o`.
    Status store(int o) {
        Output& dst = out[o];
        const uint64_t t_store = now_ns();
        dst.decode_ns = work_ns;
        work_ns = 0;
        int64_t ts = frame->best_effort_timestamp != AV_NOPTS_VALUE ? frame->best_effort_timestamp : frame->pts;
        dst.time_us = ts == AV_NOPTS_VALUE ? 0 : ts;  // pkt_timebase = microsegundos
        dst.flags = (frame->flags & AV_FRAME_FLAG_KEY) ? kFlagKeyFrame : 0;
        if (kind == DecoderKind::Video) {
            int w = frame->width, h = frame->height;
            width = w;
            height = h;
            AVPixelFormat target = vfmt == VideoFormat::NV12 ? AV_PIX_FMT_NV12 : AV_PIX_FMT_RGBA;
            sws = sws_getCachedContext(sws, w, h, AVPixelFormat(frame->format), w, h, target, SWS_BILINEAR,
                                       nullptr, nullptr, nullptr);
            if (!sws) return kUnsupported;
            uint8_t* planes[4] = {};
            int strides[4] = {};
            if (target == AV_PIX_FMT_NV12) {
                dst.size = size_t(w) * h + size_t(w) * ((h + 1) / 2);
                dst.mem.resize(dst.size);
                planes[0] = dst.mem.data();
                planes[1] = dst.mem.data() + size_t(w) * h;
                strides[0] = strides[1] = w;
            } else {
                dst.size = size_t(w) * h * 4;
                dst.mem.resize(dst.size);
                planes[0] = dst.mem.data();
                strides[0] = w * 4;
            }
            sws_scale(sws, frame->data, frame->linesize, 0, h, planes, strides);
            dst.convert_ns = now_ns() - t_store;
        } else {
            if (!swr) {
                swr_alloc_set_opts2(&swr, &frame->ch_layout, AV_SAMPLE_FMT_S16, frame->sample_rate, &frame->ch_layout,
                                    AVSampleFormat(frame->format), frame->sample_rate, 0, nullptr);
                if (!swr || swr_init(swr) < 0) return kUnsupported;
            }
            int ch = frame->ch_layout.nb_channels;
            dst.mem.resize(size_t(frame->nb_samples) * ch * 2);
            uint8_t* o8 = dst.mem.data();
            int n = swr_convert(swr, &o8, frame->nb_samples, const_cast<const uint8_t**>(frame->extended_data),
                                frame->nb_samples);
            if (n < 0) return kUnknownError;
            dst.size = size_t(n) * ch * 2;
        }
        dst.free = false;
        ready.push_back(o);
        return kOk;
    }

    // Avanza la decodificacion hasta tener una salida lista (o no poder mas por ahora).
    Status produce() {
        if (!ctx || !started) return kInvalidState;
        uint64_t hw_deadline = 0;  // AP_MOVIE_HW_WAIT: one wait per call
        if (hw) hw_report();
        while (ready.empty()) {
            int o = free_output();
            if (o < 0) return kTryAgain;  // el juego todavia no libera salidas
            uint64_t t0 = now_ns();
            int r = avcodec_receive_frame(ctx, frame);
            work_ns += now_ns() - t0;
            if (r == 0) {
                Status s = store(o);
                av_frame_unref(frame);
                if (s != kOk) {
                    if (hw && hw_fallback("its frames could not be converted")) continue;  // AP_MOVIE_HW
                    return s;
                }
                if (hw) {  // AP_MOVIE_HW_WAIT
                    if (hw_lat < 0) {
                        hw_lat = hw_inflight > 0 ? hw_inflight - 1 : 0;
                        nx::log("movie/hw: first picture after %d packets, %.0f ms after opening; latency %d", hw_tot_in,
                                double(now_ns() - hw_open_ns) / 1e6, hw_lat);
                    }
                    if (hw_inflight > 0) hw_inflight--;
                    hw_n_out++;
                }
                if (hw && !hw_out) {  // AP_MOVIE_HW: it works, nothing to send again any more
                    hw_out = true;
                    hw_forget();
                }
                continue;
            }
            if (r == AVERROR(EAGAIN)) {
                if (!pending.empty()) {
                    AVPacket* q = pending.front();
                    uint64_t t1 = now_ns();
                    int sr = avcodec_send_packet(ctx, q);
                    work_ns += now_ns() - t1;
                    if (sr == AVERROR(EAGAIN)) {  // primero hay que sacar frames
                        if (hw) {  // AP_MOVIE_HW: full and no picture yet
                            if (hw_should_wait() && hw_wait(hw_deadline)) continue;  // AP_MOVIE_HW_WAIT
                            return kTryAgain;  // the game asks again
                        }
                        continue;
                    }
                    if (sr < 0 && sr != AVERROR_EOF && hw && hw_fallback("it refused a packet")) continue;  // AP_MOVIE_HW
                    pending.pop_front();
                    if (hw && sr >= 0) hw_inflight++, hw_n_in++, hw_tot_in++;  // AP_MOVIE_HW_WAIT
                    if (hw && !hw_out && sr >= 0) {  // AP_MOVIE_HW: kept until its first picture
                        hw_sent.push_back(q);
                        if (hw_sent.size() >= 120 && hw_fallback("no picture after 120 packets")) continue;
                        continue;
                    }
                    av_packet_free(&q);
                    continue;  // si sr < 0 la muestra estaba corrupta: se descarta
                }
                if (eos_in && !drain_sent) {
                    avcodec_send_packet(ctx, nullptr);
                    drain_sent = true;
                    continue;
                }
                if (hw && hw_should_wait() && hw_wait(hw_deadline)) continue;  // AP_MOVIE_HW_WAIT
                return kTryAgain;
            }
            if (r == AVERROR_EOF) {
                if (eos_out) return kEndOfStream;
                Output& e = out[o];
                e.size = 0;
                e.flags = kFlagEndOfStream;
                e.time_us = 0;
                e.free = false;
                ready.push_back(o);
                eos_out = true;
                continue;
            }
            if (hw && hw_fallback("decode error")) continue;  // AP_MOVIE_HW
            return kUnknownError;
        }
        return kOk;
    }
};

Decoder::Decoder(DecoderKind kind, VideoFormat vfmt) : p_(new Impl(kind, vfmt)) {}
Decoder::~Decoder() = default;

Status Decoder::configure(const MediaDataStore& f) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    p_->close();
    p_->clear_queues();
    avcodec_parameters_free(&p_->par);
    p_->par = avcodec_parameters_alloc();
    if (f.codec) {
        avcodec_parameters_copy(p_->par, f.codec->par);
    } else {
        // Formato armado a mano por el juego: reconstruir desde mime + csd-*.
        const std::string* mime = f.find<std::string>("mime");
        if (!mime) return last_error_ = kInvalidArgument;
        std::vector<uint8_t> extra;
        if (const auto* c0 = f.find<MediaDataStore::Bytes>("csd-0")) extra = *c0;
        if (const auto* c1 = f.find<MediaDataStore::Bytes>("csd-1")) extra.insert(extra.end(), c1->begin(), c1->end());
        if (*mime == "video/avc") {
            p_->par->codec_type = AVMEDIA_TYPE_VIDEO;
            p_->par->codec_id = AV_CODEC_ID_H264;
            if (const auto* w = f.find<int32_t>("width")) p_->par->width = *w;
            if (const auto* h = f.find<int32_t>("height")) p_->par->height = *h;
        } else if (*mime == "audio/mp4a-latm") {
            p_->par->codec_type = AVMEDIA_TYPE_AUDIO;
            p_->par->codec_id = AV_CODEC_ID_AAC;
            if (const auto* sr = f.find<int32_t>("sample-rate")) p_->par->sample_rate = *sr;
            if (const auto* cc = f.find<int32_t>("channel-count")) av_channel_layout_default(&p_->par->ch_layout, *cc);
        } else {
            return last_error_ = kUnsupported;
        }
        set_extradata(p_->par, extra);
    }
    // El tipo real sale del formato, no del DecoderType del constructor (enum aun sin confirmar).
    p_->kind = p_->par->codec_type == AVMEDIA_TYPE_AUDIO ? DecoderKind::Audio : DecoderKind::Video;
    p_->width = p_->par->width;
    p_->height = p_->par->height;
    return last_error_ = kOk;
}

Status Decoder::start() {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (p_->started) return kOk;
    return last_error_ = p_->open();
}

Status Decoder::flush() {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    p_->clear_queues();
    if (p_->ctx) avcodec_flush_buffers(p_->ctx);
    if (p_->swr) swr_free(&p_->swr);
    return kOk;
}

Status Decoder::stop() {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    flush();
    p_->close();
    return kOk;
}

Status Decoder::reset() {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    stop();
    avcodec_parameters_free(&p_->par);
    p_->width = p_->height = 0;
    last_error_ = kOk;
    return kOk;
}

Status Decoder::acquire_input_index(int* index) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->started) return kInvalidState;
    if (p_->eos_in || p_->pending.size() >= pending_limit_for(p_->kind)) return kTryAgain;  // AP_MOVIE_AUDIO_QUEUE
    for (int i = 0; i < kInputSlots; i++)
        if (p_->in[i].free) {
            p_->in[i].free = false;
            *index = i;
            return kOk;
        }
    return kTryAgain;
}

uint8_t* Decoder::input_buffer(int index, size_t* capacity) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (index < 0 || index >= kInputSlots) return nullptr;
    *capacity = p_->in[index].mem.size();
    return p_->in[index].mem.data();
}

Status Decoder::send_input(int index, int offset, int size, int64_t time_us, uint32_t flags) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->started) return kInvalidState;
    if (index < 0 || index >= kInputSlots || p_->in[index].free) return kInvalidArgument;
    auto& slot = p_->in[index];
    if (offset < 0 || size < 0 || size_t(offset) + size_t(size) > slot.mem.size()) return kInvalidArgument;
    bool config_only = (flags & kFlagCodecConfig) && p_->kind == DecoderKind::Audio;  // el ASC ya va en extradata
    if (size > 0 && !config_only) {
        AVPacket* q = av_packet_alloc();
        av_new_packet(q, size);
        std::memcpy(q->data, slot.mem.data() + offset, size_t(size));
        q->pts = q->dts = time_us;
        if (flags & kFlagKeyFrame) q->flags |= AV_PKT_FLAG_KEY;
        p_->pending.push_back(q);
    }
    if (flags & kFlagEndOfStream) p_->eos_in = true;
    slot.free = true;  // ya se copio: el slot vuelve a estar disponible
    return kOk;
}

Status Decoder::acquire_output_index(int* index, int64_t* time_us, uint32_t* flags) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->started) return kInvalidState;
    Status s = p_->produce();
    if (p_->ready.empty()) return s == kOk ? kTryAgain : s;
    int o = p_->ready.front();
    p_->ready.pop_front();
    *index = o;
    *time_us = p_->out[o].time_us;
    *flags = p_->out[o].flags;
    if (p_->kind == DecoderKind::Video) p_->last_out_us = p_->out[o].time_us;  // AP_TITLE_SYNC
    return kOk;
}

uint8_t* Decoder::output_buffer(int index, size_t* size) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (index < 0 || index >= kOutputSlots || p_->out[index].free) return nullptr;
    *size = p_->out[index].size;
    return p_->out[index].mem.data();
}

Status Decoder::release_output_index(int index) {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (index < 0 || index >= kOutputSlots) return kInvalidArgument;
    p_->out[index].free = true;
    return kOk;
}

bool Decoder::output_timing(int index, uint64_t* decode_ns, uint64_t* convert_ns, bool* key) const {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (index < 0 || index >= kOutputSlots || p_->out[index].free) return false;
    *decode_ns = p_->out[index].decode_ns;
    *convert_ns = p_->out[index].convert_ns;
    *key = (p_->out[index].flags & kFlagKeyFrame) != 0;
    return true;
}

bool Decoder::pump() {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->started) return false;
    if (p_->ready.empty()) p_->produce();
    return !p_->ready.empty();
}

// AP_TITLE_SYNC (apply_title_sync.py)
void Decoder::drop_pending() {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    for (auto* q : p_->pending) av_packet_free(&q);
    p_->pending.clear();
    if (p_->ctx) avcodec_flush_buffers(p_->ctx);
    p_->drain_sent = false;
    p_->hw_forget();  // AP_MOVIE_HW
    p_->hw_inflight = 0;  // AP_MOVIE_HW_WAIT
}
bool Decoder::is_video() const { return p_->kind == DecoderKind::Video; }
int64_t Decoder::last_output_us() const {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    return p_->last_out_us;
}

bool Decoder::has_free_input() const {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->started || p_->eos_in || p_->pending.size() >= pending_limit_for(p_->kind)) return false;  // AP_MOVIE_AUDIO_QUEUE
    for (const auto& i : p_->in)
        if (i.free) return true;
    return false;
}

std::string Decoder::debug_state() const {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    int free_in = 0, out_free = 0, out_game = 0;
    for (const auto& i : p_->in) free_in += i.free ? 1 : 0;
    for (const auto& o : p_->out) out_free += o.free ? 1 : 0;
    out_game = kOutputSlots - out_free - int(p_->ready.size());
    char b[200];
    std::snprintf(b, sizeof b, "%s started %d, entrada libre %d/%d, pendientes %zu, salida: listas %zu, en el juego %d, libres %d, eos in/out %d/%d",
                  p_->kind == DecoderKind::Audio ? "audio" : "video", int(p_->started), free_in, kInputSlots,
                  p_->pending.size(), p_->ready.size(), out_game, out_free, int(p_->eos_in), int(p_->eos_out));
    return b;
}

int Decoder::free_input_count() const {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    if (!p_->started || p_->eos_in || p_->pending.size() >= pending_limit_for(p_->kind)) return 0;  // AP_MOVIE_AUDIO_QUEUE
    int n = 0;
    for (const auto& i : p_->in) n += i.free ? 1 : 0;
    return n;
}

int Decoder::ready_output_count() const {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    return int(p_->ready.size());
}

Status Decoder::output_format(MediaDataStore* o) const {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    o->clear();
    if (p_->kind == DecoderKind::Video) {
        o->set<std::string>("mime", "video/raw");
        o->set<int32_t>("width", p_->width);
        o->set<int32_t>("height", p_->height);
        o->set<int32_t>("stride", p_->vfmt == VideoFormat::NV12 ? p_->width : p_->width * 4);
        o->set<int32_t>("slice-height", p_->height);
        // TODO: el valor numerico que espera el juego (Android: 21 = NV12 / YUV420SemiPlanar).
        o->set<int32_t>("color-format", p_->vfmt == VideoFormat::NV12 ? 21 : 0x7F000789);
    } else {
        o->set<std::string>("mime", "audio/raw");
        int sr = p_->ctx ? p_->ctx->sample_rate : (p_->par ? p_->par->sample_rate : 0);
        int ch = p_->ctx ? p_->ctx->ch_layout.nb_channels : (p_->par ? p_->par->ch_layout.nb_channels : 0);
        o->set<int32_t>("sample-rate", sr);
        o->set<int32_t>("channel-count", ch);
        o->set<int32_t>("pcm-encoding", 2);  // Android ENCODING_PCM_16BIT
    }
    return kOk;
}

void Decoder::output_dimensions(int* width, int* height) const {
    std::lock_guard<std::recursive_mutex> lk(p_->m);
    *width = p_->width;
    *height = p_->height;
}

// ================================================================================================= utilidades

static size_t start_code_at(const uint8_t* p, size_t n, size_t i) {
    if (i + 3 <= n && p[i] == 0 && p[i + 1] == 0 && p[i + 2] == 1) return 3;
    if (i + 4 <= n && p[i] == 0 && p[i + 1] == 0 && p[i + 2] == 0 && p[i + 3] == 1) return 4;
    return 0;
}

Status next_nal_unit(const uint8_t** data, size_t* size, const uint8_t** nal_start, size_t* nal_size) {
    const uint8_t* p = *data;
    size_t n = *size;
    size_t i = 0, sc = 0;
    while (i < n && !(sc = start_code_at(p, n, i))) i++;
    if (i >= n) return kEndOfStream;  // TODO: Android devuelve -EAGAIN
    size_t begin = i + sc, end = begin;
    while (end < n && !start_code_at(p, n, end)) end++;
    *nal_start = p + begin;
    *nal_size = end - begin;
    *data = p + end;
    *size = n - end;
    return kOk;
}

}  // namespace movie_host