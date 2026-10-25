// hle_carga.cpp - Decodificacion nativa de las cargas del juego (Tsukihime, las dos ediciones). AP_CARGA
// (archivo nuevo: se copia a games/tsukihime_common; apply_native_loading.py enlaza libjpeg-turbo y zlib).
//
// Vale para las dos ediciones (layouts del SDK 17.5.4 de la global y del 11.4.3 de la japonesa, vistos en sus sdk).
// Los stall de las escenas (claude/tsukihime-efectos.md) venian de codigo del sdk recompilado:
//   nn::image::JpegDecoder::Decode       124-160 ms por imagen de 17,7 MB (libjpeg recompilado)
//   nn::util::StreamingDecompressGzip    ~82 MB/s (zlib recompilado)
//   nn::image::PngDecoder::Decode        ~56 ms por 9 MB (libpng 1.6.37 recompilado)
// Aqui se hacen con libjpeg-turbo, libpng y zlib nativos, con la misma semantica que el sdk (sacada de sdk.bin, SDK 17.5.4;
// ver el comentario de cada funcion). El resto de JpegDecoder (constructor, SetImageData, Analyze, GetAnalyzedDimension)
// sigue siendo el real: Decode lee del objeto lo que dejo Analyze.
//
// Variables:
//   NX_TSUKI_CARGA=0      todo vuelve a la funcion real del sdk (como antes)
//   NX_TSUKI_CARGA=check  el juego recibe lo de la funcion REAL, y ademas se decodifica en nativo sobre un buffer
//                         aparte y se compara byte a byte (log "carga/check"). Para verificar antes de confiar.
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <setjmp.h>
#include <atomic>
#include <chrono>
#include <thread>
#include <string>
#include <unordered_map>
#include <vector>

#include <jpeglib.h>
#include <png.h>
#include <zlib.h>

#ifndef TSUKI_CARGA_SOLO_NUCLEO
#include "kernel.h"  // CPU, log, call, find_symbol, NX_HLE
#endif

namespace tsuki_carga {

// ============================================================================================== JPEG (nucleo)
// Lo que hace el sdk (JpegDecoder::Decode -> sdk+4be20 en el SDK 17, sdk+27ed30 en el 11; formato 0xe = RGBA8,
// denominador 1):
//   out_color_space JCS_RGB, scale 1/1, dct_method JDCT_ISLOW, do_fancy_upsampling FALSE, do_block_smoothing FALSE;
//   exige output_width/height = los de Analyze y 3 componentes (si no, -32);
//   fila r en out + r*stride*4, stride = ancho redondeado hacia arriba a `alignment` pixeles; escribe solo `ancho`
//   pixeles por fila (el relleno no se toca), alfa 0xff. Error de libjpeg -> -32 (JpegStatus WrongFormat).
constexpr int kJpegOk = 0, kJpegWrongFormat = -32;

struct JpegErr {
    jpeg_error_mgr mgr;
    jmp_buf jb;
};
static void jpeg_err_exit(j_common_ptr ci) { longjmp(reinterpret_cast<JpegErr*>(ci->err)->jb, 1); }
static void jpeg_no_msg(j_common_ptr, int) {}
static void jpeg_no_out(j_common_ptr) {}

uint32_t jpeg_stride(uint32_t width, int alignment) {
    uint32_t a = uint32_t(alignment), r = width % a;
    return r == 0 ? width : width + a - r;
}

int decode_jpeg_rgba(const uint8_t* data, size_t size, uint32_t width, uint32_t height, int alignment, uint8_t* out) {
    const uint32_t stride = jpeg_stride(width, alignment);
    jpeg_decompress_struct ci;
    JpegErr err;
    ci.err = jpeg_std_error(&err.mgr);
    err.mgr.error_exit = jpeg_err_exit;
    err.mgr.emit_message = jpeg_no_msg;
    err.mgr.output_message = jpeg_no_out;
    std::vector<JSAMPROW> rows(8);
    if (setjmp(err.jb)) {
        jpeg_destroy_decompress(&ci);
        return kJpegWrongFormat;
    }
    jpeg_create_decompress(&ci);
    jpeg_mem_src(&ci, data, (unsigned long)size);
    if (jpeg_read_header(&ci, TRUE) != JPEG_HEADER_OK) {
        jpeg_destroy_decompress(&ci);
        return kJpegWrongFormat;
    }
#ifdef JCS_EXTENSIONS
    ci.out_color_space = JCS_EXT_RGBA;  // libjpeg-turbo: RGB con alfa 0xff, directo a la fila del juego
#else
#error "hace falta libjpeg-turbo (JCS_EXT_RGBA)"
#endif
    ci.scale_num = 1;
    ci.scale_denom = 1;
    ci.dct_method = JDCT_ISLOW;
    ci.do_fancy_upsampling = FALSE;
    ci.do_block_smoothing = FALSE;
    jpeg_start_decompress(&ci);
    if (ci.output_width != width || ci.output_height != height || ci.output_components != 4) {  // RGBA = las 3 de RGB del sdk + alfa
        jpeg_destroy_decompress(&ci);
        return kJpegWrongFormat;
    }
    while (ci.output_scanline < ci.output_height) {
        JDIMENSION y = ci.output_scanline, n = std::min<JDIMENSION>(8, ci.output_height - y);
        for (JDIMENSION k = 0; k < n; k++) rows[k] = out + size_t(uint64_t(y + k) * stride) * 4;
        jpeg_read_scanlines(&ci, rows.data(), n);
    }
    bool ok = jpeg_finish_decompress(&ci);
    jpeg_destroy_decompress(&ci);
    return ok ? kJpegOk : kJpegWrongFormat;
}

// ============================================================================================== PNG (nucleo)
// Lo que hace el sdk (libpng 1.6.37; sdk+71930 en Analyze, nn::image::detail::Transform sdk+71430, Decode sdk+71a8c):
//   png_read_info; Transform(formato de salida); png_read_update_info; en Decode, filas en out + y*stride (stride =
//   tercer argumento de Decode) y png_read_image (sin png_read_end).
// PixelFormat (GetBitDepth/GetChannelCount, sdk+722f0/72320): 1-4 paleta 1/2/4/8 bits, 5-9 gris 1/2/4/8/16,
// 10/11 RGB 8/16, 12/13 gris+alfa 8/16, 14/15 RGBA 8/16. Aqui solo 5..15 (el sdk siempre pasa la paleta a RGB).
static const int kPngBits[16] = {0, 1, 2, 4, 8, 1, 2, 4, 8, 16, 8, 16, 8, 16, 8, 16};
static const int kPngChannels[16] = {0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 3, 3, 2, 2, 4, 4};

uint64_t png_row_stride_min(int fmt, uint32_t width) {  // nn::image::detail::GetPngRowStride
    return (uint64_t(uint32_t(kPngBits[fmt] * kPngChannels[fmt]) * width) + 7) >> 3;
}

struct PngMem {
    const uint8_t* p;
    size_t n, off;
};
struct PngErr {
    jmp_buf jb;
};
static void png_err_fn(png_structp png, png_const_charp) { longjmp(static_cast<PngErr*>(png_get_error_ptr(png))->jb, 1); }
static void png_warn_fn(png_structp, png_const_charp) {}
static void png_read_mem(png_structp png, png_bytep dst, png_size_t len) {
    auto* m = static_cast<PngMem*>(png_get_io_ptr(png));
    if (len > m->n - m->off) png_error(png, "fin de datos");
    std::memcpy(dst, m->p + m->off, len);
    m->off += len;
}

// nn::image::detail::Transform (sdk+71430), mismas llamadas en el mismo orden.
static void png_transform(png_structp png, png_infop info, int fmt) {
    const int bits = png_get_bit_depth(png, info), color = png_get_color_type(png, info);
    const bool to_gray = (0x19f0u >> (fmt - 1)) & 1, no_alpha = (0x7ffu >> (fmt - 1)) & 1;
    const int out_bits = kPngBits[fmt];
    if (color == PNG_COLOR_TYPE_PALETTE) png_set_palette_to_rgb(png);
    if (!to_gray) {
        if (!(color & 2)) png_set_gray_to_rgb(png);
    } else if (color & 2) {
        png_set_rgb_to_gray(png, 1, -1.0, -1.0);
    }
    const bool trns = png_get_valid(png, info, PNG_INFO_tRNS) != 0;
    if (no_alpha) {
        if ((color & 4) || trns) png_set_strip_alpha(png);
    } else if (!(color & 4)) {
        if (trns) png_set_tRNS_to_alpha(png);
        else png_set_add_alpha(png, 0xffff, PNG_FILLER_AFTER);
    }
    if (out_bits == 8) {
        if (bits == 16) png_set_scale_16(png);
        else if (bits < 8) png_set_expand(png);
    } else if (out_bits == 16) {
        if (bits < 16) png_set_expand_16(png);
        png_set_swap(png);
    }
}

// 0 = listo; -1 = no se pudo (el que llama usa la funcion real del sdk).
int decode_png(const uint8_t* data, size_t size, int fmt, uint32_t width, uint32_t height, uint64_t stride, uint8_t* out) {
    if (fmt < 5 || fmt > 15) return -1;
    PngErr err;
    PngMem mem{data, size, 0};
    png_structp png = png_create_read_struct(PNG_LIBPNG_VER_STRING, &err, png_err_fn, png_warn_fn);
    if (!png) return -1;
    png_infop info = png_create_info_struct(png);
    std::vector<png_bytep> rows;
    if (!info || setjmp(err.jb)) {
        png_destroy_read_struct(&png, info ? &info : nullptr, nullptr);
        return -1;
    }
    png_set_read_fn(png, &mem, png_read_mem);
    png_read_info(png, info);
    png_transform(png, info, fmt);
    png_read_update_info(png, info);
    if (png_get_image_width(png, info) != width || png_get_image_height(png, info) != height ||
        png_get_rowbytes(png, info) != png_row_stride_min(fmt, width) || stride < png_get_rowbytes(png, info)) {
        png_destroy_read_struct(&png, &info, nullptr);
        return -1;
    }
    rows.resize(height);
    for (uint32_t y = 0; y < height; y++) rows[y] = out + uint64_t(y) * stride;
    png_read_image(png, rows.data());
    png_destroy_read_struct(&png, &info, nullptr);
    return 0;
}

// ============================================================================================== zlib (nucleo)
// Lo que hace el sdk (StreamingDecompressZlib/Gzip -> sdk+818c0; Initialize*Context: inflateInit2 con 15 (zlib) o
// 31 (gzip), z_stream en ctx+0x10, ctx+0x80 = terminado):
//   terminado -> *outSize = *inConsumed = 0, true.
//   avail_in/avail_out = inSize/outCap truncados a 32 bits; r = inflate(Z_NO_FLUSH).
//   r == Z_OK -> *outSize = producido, *inConsumed = consumido, true.
//   r == Z_STREAM_END -> terminado; inflateEnd; mismas salidas; true.
//   r == Z_NEED_DICT, Z_STREAM_ERROR, Z_DATA_ERROR, Z_MEM_ERROR, Z_BUF_ERROR o Z_VERSION_ERROR -> false (sin salidas).
//   otro (Z_ERRNO) -> el sdk aborta; aqui false.
struct Stream {
    z_stream zs{};
    bool live = false, done = false;
    int wbits = 15;
    ~Stream() {
        if (live) inflateEnd(&zs);
    }
    bool reset(int bits) {
        done = false;
        wbits = bits;
        if (live) return inflateReset2(&zs, bits) == Z_OK;
        std::memset(&zs, 0, sizeof zs);
        live = inflateInit2(&zs, bits) == Z_OK;
        return live;
    }
    // Devuelve el bool del sdk; out_size/in_used solo se escriben si devuelve true.
    bool step(uint64_t* out_size, uint64_t* in_used, uint8_t* out, uint64_t out_cap, const uint8_t* in, uint64_t in_size) {
        if (done) {
            *out_size = 0, *in_used = 0;
            return true;
        }
        if (!live) return false;
        const uInt ai = uInt(in_size), ao = uInt(out_cap);
        zs.next_in = const_cast<Bytef*>(in);
        zs.avail_in = ai;
        zs.next_out = out;
        zs.avail_out = ao;
        int r = inflate(&zs, Z_NO_FLUSH);
        if (r != Z_OK && r != Z_STREAM_END) return false;
        *out_size = uint64_t(uint32_t(ao - zs.avail_out));
        *in_used = uint64_t(uint32_t(ai - zs.avail_in));
        if (r == Z_STREAM_END) {
            done = true;
            inflateEnd(&zs);
            live = false;
        }
        return true;
    }
};

}  // namespace tsuki_carga

#ifndef TSUKI_CARGA_SOLO_NUCLEO

namespace nx {

namespace {

using namespace tsuki_carga;

// ---- tiempos: un resumen por segundo con actividad (log "carga/s"), y cada llamada de 20 ms o mas ----
uint64_t now_ns() {
    return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now().time_since_epoch()).count());
}
struct Acc {
    std::atomic<uint64_t> calls{0}, ns{0}, max_ns{0}, bytes{0};
    void add(uint64_t dt, uint64_t b) {
        calls.fetch_add(1, std::memory_order_relaxed);
        ns.fetch_add(dt, std::memory_order_relaxed);
        bytes.fetch_add(b, std::memory_order_relaxed);
        uint64_t m = max_ns.load(std::memory_order_relaxed);
        while (dt > m && !max_ns.compare_exchange_weak(m, dt, std::memory_order_relaxed)) {}
    }
};
Acc g_jpeg, g_png, g_zlib;
double ms(uint64_t ns) { return double(ns) / 1e6; }
void reporter() {
    for (;;) {
        std::this_thread::sleep_for(std::chrono::seconds(1));
        uint64_t jc = g_jpeg.calls.exchange(0), jn = g_jpeg.ns.exchange(0), jm = g_jpeg.max_ns.exchange(0);
        uint64_t jb = g_jpeg.bytes.exchange(0);
        uint64_t zc = g_zlib.calls.exchange(0), zn = g_zlib.ns.exchange(0), zm = g_zlib.max_ns.exchange(0);
        uint64_t zb = g_zlib.bytes.exchange(0);
        uint64_t pc = g_png.calls.exchange(0), pn = g_png.ns.exchange(0), pm = g_png.max_ns.exchange(0);
        uint64_t pb = g_png.bytes.exchange(0);
        if (!jc && !zc && !pc) continue;
        log("carga/s: jpeg %llu (%.1f ms, max %.1f, %.1f MB) | png %llu (%.1f ms, max %.1f, %.1f MB) | zlib+gzip %llu "
            "(%.1f ms, max %.1f, %.1f MB)",
            (unsigned long long)jc, ms(jn), ms(jm), double(jb) / 1048576.0, (unsigned long long)pc, ms(pn), ms(pm),
            double(pb) / 1048576.0, (unsigned long long)zc, ms(zn), ms(zm), double(zb) / 1048576.0);
    }
}
void measure(Acc& a, const char* what, uint64_t t0, uint64_t bytes) {
    static std::once_flag once;
    std::call_once(once, [] { std::thread(reporter).detach(); });
    uint64_t dt = now_ns() - t0;
    a.add(dt, bytes);
    if (dt >= 20'000'000) log("carga: %s %.1f ms (%.2f MB)", what, ms(dt), double(bytes) / 1048576.0);
}

enum class Modo { Nativo, Real, Check };
Modo modo() {
    static const Modo m = [] {
        const char* e = std::getenv("NX_TSUKI_CARGA");
        Modo r = Modo::Nativo;
        if (e && std::strcmp(e, "0") == 0) r = Modo::Real;
        else if (e && std::strcmp(e, "check") == 0) r = Modo::Check;
        log("carga: jpeg/zlib %s (NX_TSUKI_CARGA=0 o check)",
            r == Modo::Nativo ? "nativos" : r == Modo::Real ? "del sdk" : "del sdk + comparacion con nativos");
        return r;
    }();
    return m;
}

// ---- check: contadores y primeras diferencias ----
std::mutex g_check_m;
uint64_t g_check_ok = 0, g_check_one = 0, g_check_bad = 0;
// one: solo diferencias de +-1 (libjpeg-turbo frente al IJG 9d del sdk: IDCT/color; esperado, no se ve)
void check_result(const char* what, bool same, const std::string& detail, bool one = false) {
    std::lock_guard lk(g_check_m);
    (same ? g_check_ok : one ? g_check_one : g_check_bad)++;
    if (!same && !one && g_check_bad <= 20) log("carga/check: %s DISTINTO: %s", what, detail.c_str());
    if (one && g_check_one <= 5) log("carga/check: %s solo +-1: %s", what, detail.c_str());
    uint64_t n = g_check_ok + g_check_one + g_check_bad;
    if (n == 1 || n % 50 == 0)
        log("carga/check: %llu iguales, %llu solo +-1, %llu distintos", (unsigned long long)g_check_ok,
            (unsigned long long)g_check_one, (unsigned long long)g_check_bad);
}

// ---- JpegDecoder::Decode ----
// Dos versiones del sdk (mismo resultado; libjpeg-turbo da lo mismo que el IJG 8c del SDK 11 y +-1 frente al IJG 9d
// del SDK 17):
//   global, SDK 17.5.4: Decode(out, size, alignment) -> sdk+4be20. Objeto: +0x10 estado (2 = analizado), +0x18 datos,
//     +0x20 tamano, +0x28 denominador, +0x2c formato, +0x30 ancho, +0x34 alto.
//   japonesa, SDK 11.4.3: Decode(out, size, alignment, workBuffer, workSize) -> sdk+27ed30. Objeto: +8 estado,
//     +0x10 datos, +0x18 tamano, +0x20 denominador, +0x24 formato, +0x28 ancho, +0x2c alto. Exige workSize >
//     ancho*24 (8 filas RGB); el resto del buffer es la memoria de libjpeg.
// En las dos: formato 0xe (RGBA8; main no importa SetPixelFormat), denominador 1. Si el nativo falla, se llama a la
// real (da el codigo de error exacto del sdk).
const char kJpegDecode17[] = "_ZN2nn5image11JpegDecoder6DecodeEPvmi";
const char kJpegDecode11[] = "_ZN2nn5image11JpegDecoder6DecodeEPvmiS2_m";

struct JpegLayout {
    uint32_t state, data, size, denom, format, width, height;
    bool work_buffer;  // SDK 11: x4/x5 = buffer de trabajo
};
constexpr JpegLayout kJpeg17{0x10, 0x18, 0x20, 0x28, 0x2c, 0x30, 0x34, false};
constexpr JpegLayout kJpeg11{0x08, 0x10, 0x18, 0x20, 0x24, 0x28, 0x2c, true};

void jpeg_decode(CPU& c, uint64_t real, const JpegLayout& L) {
    const uint64_t obj = c.x[0];
    auto* out = reinterpret_cast<uint8_t*>(c.x[1]);
    const uint64_t out_size = c.x[2];
    const int alignment = int32_t(c.x[3]);
    const uint64_t work_size = L.work_buffer ? c.x[5] : ~0ull;
    const auto* o = reinterpret_cast<const uint8_t*>(obj);
    int32_t state, denom, format;
    uint32_t w, h;
    uint64_t data, size;
    std::memcpy(&state, o + L.state, 4), std::memcpy(&data, o + L.data, 8), std::memcpy(&size, o + L.size, 8);
    std::memcpy(&denom, o + L.denom, 4), std::memcpy(&format, o + L.format, 4);
    std::memcpy(&w, o + L.width, 4), std::memcpy(&h, o + L.height, 4);
    const bool usable = state >= 2 && denom == 1 && format == 0xe && alignment > 0 && out && data && w && h &&
                        work_size > uint64_t(w) * 24;
    const uint64_t t0 = now_ns();
    if (!usable || modo() == Modo::Real || !real) {
        static int warned = 0;
        if (!usable && warned++ < 5)
            log("carga: jpeg Decode por el sdk (estado %d, denominador %d, formato %#x, alineacion %d)", state, denom,
                format, alignment);
        if (real) call(c, real);
        if (usable) measure(g_jpeg, "jpeg (sdk)", t0, uint64_t(w) * h * 4);
        return;
    }
    const uint32_t stride = jpeg_stride(w, alignment);
    const size_t span = size_t(uint64_t(h - 1) * stride) * 4 + size_t(w) * 4;
    if (span > out_size) {  // el sdk no lo revisa; aqui se deja el caso raro a la funcion real
        call(c, real);
        return;
    }
    if (modo() == Modo::Nativo) {
        if (decode_jpeg_rgba(reinterpret_cast<const uint8_t*>(data), size, w, h, alignment, out) != kJpegOk) {
            static int failed = 0;
            if (failed++ < 5) log("carga: jpeg nativo fallo (%ux%u): por el sdk", w, h);
            call(c, real);
            return;
        }
        c.x[0] = 0;
        measure(g_jpeg, "jpeg", t0, uint64_t(w) * h * 4);
        return;
    }
    // check: el juego recibe la salida real; la nativa va a un buffer aparte
    std::vector<uint8_t> mine(span);
    int r_mine = decode_jpeg_rgba(reinterpret_cast<const uint8_t*>(data), size, w, h, alignment, mine.data());
    call(c, real);
    int r_real = int32_t(c.x[0]);
    uint64_t diff = 0, maxd = 0;
    for (uint32_t y = 0; y < h && r_real == 0; y++) {
        const uint8_t* a = out + size_t(uint64_t(y) * stride) * 4;
        const uint8_t* b = mine.data() + size_t(uint64_t(y) * stride) * 4;
        for (uint32_t k = 0; k < w * 4; k++)
            if (a[k] != b[k]) diff++, maxd = std::max<uint64_t>(maxd, uint64_t(std::abs(int(a[k]) - int(b[k]))));
    }
    char d[160];
    std::snprintf(d, sizeof d, "jpeg %ux%u alin %d: sdk %d nativo %d, %llu bytes distintos (max %llu)", w, h, alignment,
                  r_real, r_mine, (unsigned long long)diff, (unsigned long long)maxd);
    check_result("jpeg", r_real == r_mine && diff == 0, d, r_real == r_mine && maxd == 1);
}
void JpegDecode17(CPU& c) {
    static const uint64_t real = find_symbol(kJpegDecode17);
    jpeg_decode(c, real, kJpeg17);
}
void JpegDecode11(CPU& c) {
    static const uint64_t real = find_symbol(kJpegDecode11);
    jpeg_decode(c, real, kJpeg11);
}

// ---- PngDecoder::Decode ----
// Objeto (sdk+715e0 / 716f4 / 71798): +8 estado (3 = analizado), +0x10 datos, +0x18 tamano, +0x20 formato pedido
// (SetOutputPixelFormat; 0 = el natural), +0x24 formato natural, +0x28 ancho, +0x2c alto, +0x60 PngStreamAccessor,
// +0x80 contexto {png_struct*, png_info*} de libpng del sdk. Decode(out, outSize, stride): lo que hace el sdk despues
// de leer la imagen (sdk+71ae8): png_destroy_read_struct(&ctx->png, &ctx->info, NULL), PngStreamAccessor::Finalize,
// estado 4, resultado 0. Aqui se decodifica en nativo y despues se llaman esas dos funciones reales para liberar lo
// que dejo Analyze.
const char kPngDecode[] = "_ZN2nn5image10PngDecoder6DecodeEPvmm";

void PngDecode(CPU& c) {
    static const uint64_t real = find_symbol(kPngDecode);
    static const uint64_t destroy = find_symbol("nndetailImagePng_png_destroy_read_struct");
    static const uint64_t finalize = find_symbol("_ZN2nn5image6detail17PngStreamAccessor8FinalizeEv");
    const uint64_t obj = c.x[0];
    auto* out = reinterpret_cast<uint8_t*>(c.x[1]);
    const uint64_t out_size = c.x[2], stride = c.x[3];
    auto* o = reinterpret_cast<uint8_t*>(obj);
    int32_t state, req, nat;
    uint32_t w, h;
    uint64_t data, size, ctx;
    std::memcpy(&state, o + 0x8, 4), std::memcpy(&data, o + 0x10, 8), std::memcpy(&size, o + 0x18, 8);
    std::memcpy(&req, o + 0x20, 4), std::memcpy(&nat, o + 0x24, 4), std::memcpy(&w, o + 0x28, 4);
    std::memcpy(&h, o + 0x2c, 4), std::memcpy(&ctx, o + 0x80, 8);
    const int fmt = req ? req : nat;
    const uint64_t t0 = now_ns();
    const uint64_t rowbytes = (fmt >= 5 && fmt <= 15) ? png_row_stride_min(fmt, w) : 0;
    const bool usable = state == 3 && rowbytes && out && data && ctx && w && h && stride >= rowbytes &&
                        (uint64_t(h) - 1) * stride + rowbytes <= out_size && destroy && finalize && real;
    if (!usable || modo() == Modo::Real) {
        static int warned = 0;
        if (!usable && real && warned++ < 5)
            log("carga: png Decode por el sdk (estado %d, formato %d, %ux%u, stride %llu)", state, fmt, w, h,
                (unsigned long long)stride);
        if (real) call(c, real);
        if (usable) measure(g_png, "png (sdk)", t0, uint64_t(h) * rowbytes);
        return;
    }
    if (modo() == Modo::Nativo) {
        if (decode_png(reinterpret_cast<const uint8_t*>(data), size, fmt, w, h, stride, out) != 0) {
            static int failed = 0;
            if (failed++ < 5) log("carga: png nativo fallo (formato %d, %ux%u): por el sdk", fmt, w, h);
            call(c, real);
            return;
        }
        // Lo que libera el sdk al terminar (ver arriba), con sus propias funciones.
        c.x[0] = ctx, c.x[1] = ctx + 8, c.x[2] = 0;
        call(c, destroy);
        c.x[0] = obj + 0x60;
        call(c, finalize);
        const int32_t done = 4;
        std::memcpy(o + 0x8, &done, 4);
        c.x[0] = 0;
        measure(g_png, "png", t0, uint64_t(h) * rowbytes);
        return;
    }
    // check: el juego recibe la salida real; la nativa va a un buffer aparte
    std::vector<uint8_t> mine(size_t((uint64_t(h) - 1) * stride + rowbytes));
    int r_mine = decode_png(reinterpret_cast<const uint8_t*>(data), size, fmt, w, h, stride, mine.data());
    call(c, real);
    int r_real = int32_t(c.x[0]);
    uint64_t diff = 0;
    for (uint32_t y = 0; y < h && r_real == 0 && r_mine == 0; y++)
        for (uint64_t k = 0; k < rowbytes; k++) diff += out[y * stride + k] != mine[y * stride + k];
    char d[160];
    std::snprintf(d, sizeof d, "png %ux%u formato %d stride %llu: sdk %d nativo %d, %llu bytes distintos", w, h, fmt,
                  (unsigned long long)stride, r_real, r_mine, (unsigned long long)diff);
    check_result("png", r_real == 0 && r_mine == 0 && diff == 0, d);
}

// ---- StreamingDecompressZlib/Gzip ----
const char kInitZlib[] = "_ZN2nn4util40InitializeStreamingDecompressZlibContextEPNS0_30StreamingDecompressZlibContextE";
const char kInitGzip[] = "_ZN2nn4util40InitializeStreamingDecompressGzipContextEPNS0_30StreamingDecompressZlibContextE";
const char kStreamZlib[] = "_ZN2nn4util23StreamingDecompressZlibEPmS1_PvmPKvmPNS0_30StreamingDecompressZlibContextE";
const char kStreamGzip[] = "_ZN2nn4util23StreamingDecompressGzipEPmS1_PvmPKvmPNS0_30StreamingDecompressZlibContextE";

std::mutex g_streams_m;
std::unordered_map<uint64_t, std::shared_ptr<Stream>> g_streams;  // por contexto del guest

std::shared_ptr<Stream> stream_for(uint64_t ctx) {
    std::lock_guard lk(g_streams_m);
    auto it = g_streams.find(ctx);
    return it == g_streams.end() ? nullptr : it->second;
}

void init_common(CPU& c, uint64_t real, int wbits) {
    const uint64_t ctx = c.x[0];
    // La real se llama siempre: deja el contexto del guest como el sdk lo espera (y en check lo usa). Es barata.
    if (real) call(c, real);
    if (modo() == Modo::Real) return;
    std::shared_ptr<Stream> s;
    {
        std::lock_guard lk(g_streams_m);
        auto& slot = g_streams[ctx];
        if (!slot) slot = std::make_shared<Stream>();
        s = slot;
        // Contextos que el juego abandona sin terminar (no hay Finalize en la API): limite de memoria.
        if (g_streams.size() > 512) {
            for (auto it = g_streams.begin(); it != g_streams.end();)
                it = (it->first != ctx && !it->second->live) ? g_streams.erase(it) : std::next(it);
            static int warned = 0;
            if (g_streams.size() > 512 && warned++ < 3) log("carga: %zu contextos zlib abiertos", g_streams.size());
        }
    }
    if (!s->reset(wbits)) log("carga: inflateInit2(%d) fallo para el contexto %llx", wbits, (unsigned long long)ctx);
}
void InitZlib(CPU& c) {
    static const uint64_t real = find_symbol(kInitZlib);
    init_common(c, real, 15);
}
void InitGzip(CPU& c) {
    static const uint64_t real = find_symbol(kInitGzip);
    init_common(c, real, 31);
}

void stream_common(CPU& c, uint64_t real, const char* what) {
    auto* out_size = reinterpret_cast<uint64_t*>(c.x[0]);
    auto* in_used = reinterpret_cast<uint64_t*>(c.x[1]);
    auto* out = reinterpret_cast<uint8_t*>(c.x[2]);
    const uint64_t out_cap = c.x[3];
    const auto* in = reinterpret_cast<const uint8_t*>(c.x[4]);
    const uint64_t in_size = c.x[5];
    const uint64_t ctx = c.x[6];
    std::shared_ptr<Stream> s = modo() == Modo::Real ? nullptr : stream_for(ctx);
    const uint64_t t0 = now_ns();
    if (!s) {  // NX_TSUKI_CARGA=0, o un contexto cuyo Initialize no vimos
        if (real) call(c, real);
        if (real && (c.x[0] & 0xff) && out_size) measure(g_zlib, what, t0, *out_size);
        return;
    }
    if (modo() == Modo::Nativo) {
        bool r = s->step(out_size, in_used, out, out_cap, in, in_size);
        c.x[0] = r ? 1 : 0;
        measure(g_zlib, what, t0, r ? *out_size : 0);
        return;
    }
    // check: el juego recibe lo del sdk; el nativo avanza su propio stream sobre un buffer aparte
    std::vector<uint8_t> mine(static_cast<size_t>(static_cast<uint32_t>(out_cap)));
    uint64_t m_out = ~0ull, m_in = ~0ull;
    bool r_mine = s->step(&m_out, &m_in, mine.data(), uint32_t(out_cap), in, in_size);
    call(c, real);
    bool r_real = (c.x[0] & 0xff) != 0;
    bool same = r_real == r_mine;
    if (same && r_real) same = *out_size == m_out && *in_used == m_in && std::memcmp(out, mine.data(), size_t(m_out)) == 0;
    char d[200];
    std::snprintf(d, sizeof d, "%s: sdk %d (sale %llu, consume %llu) nativo %d (sale %llu, consume %llu)", what, r_real,
                  r_real ? (unsigned long long)*out_size : 0ull, r_real ? (unsigned long long)*in_used : 0ull, r_mine,
                  r_mine ? (unsigned long long)m_out : 0ull, r_mine ? (unsigned long long)m_in : 0ull);
    check_result(what, same, d);
}
void StreamZlib(CPU& c) {
    static const uint64_t real = find_symbol(kStreamZlib);
    stream_common(c, real, "zlib");
}
void StreamGzip(CPU& c) {
    static const uint64_t real = find_symbol(kStreamGzip);
    stream_common(c, real, "gzip");
}

}  // namespace

NX_HLE("_ZN2nn5image11JpegDecoder6DecodeEPvmi", JpegDecode17);       // global (SDK 17)
NX_HLE("_ZN2nn5image11JpegDecoder6DecodeEPvmiS2_m", JpegDecode11);  // japonesa (SDK 11)
NX_HLE("_ZN2nn5image10PngDecoder6DecodeEPvmm", PngDecode);
NX_HLE("_ZN2nn4util40InitializeStreamingDecompressZlibContextEPNS0_30StreamingDecompressZlibContextE", InitZlib);
NX_HLE("_ZN2nn4util40InitializeStreamingDecompressGzipContextEPNS0_30StreamingDecompressZlibContextE", InitGzip);
NX_HLE("_ZN2nn4util23StreamingDecompressZlibEPmS1_PvmPKvmPNS0_30StreamingDecompressZlibContextE", StreamZlib);
NX_HLE("_ZN2nn4util23StreamingDecompressGzipEPmS1_PvmPKvmPNS0_30StreamingDecompressZlibContextE", StreamGzip);

}  // namespace nx

#endif  // TSUKI_CARGA_SOLO_NUCLEO