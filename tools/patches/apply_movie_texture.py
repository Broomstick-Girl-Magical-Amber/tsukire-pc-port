#!/usr/bin/env python3
"""apply_movie_texture.py - video output of movie::Decoder to the game's NVN texture (outputFormat 3).

Tsukihime creates the video decoder with Decoder(type=1, outputFormat=3, mode=2). In that mode the game does not get
pixels: in GetOutputBuffer it passes an 8-byte movie::Buffer pointing to its NVNtexture (0xC0 bytes, at
this+0x32f8 + idx*0xC0, double buffer), and the original (CopyOutputBufferToClientTextureAndRelease) copies the frame
into that texture. Our HLE lent it the NV12 in the Buffer: the frame never reached the texture
(TM_CI.mp4 looked white, the menu background titlebg1.mp4 frozen).

Changes in games/tsukihime_common/hle_movie.cpp:
  - Decoder(type, 3, mode): the host decoder delivers RGBA (VideoFormat::RGBA, swscale) instead of NV12.
  - GetOutputBuffer with format 3: finds the NVNtexture* (the pointer kept in the Buffer, or the Buffer itself) and
    leaves the frame as a framework Texture::TexelWrite, the same path as nvnTextureWriteTexels: it is applied
    to the Vulkan image before the next use. A frame that covers the whole texture discards the earlier
    pending ones. The game's Buffer stays intact.
  - Log (once per texture): address, size and NVN format; warnings if the texture is not found or if the
    format is not 4 bytes per texel.

Usage: python apply_movie_texture.py [framework root]   (default: <root>\\framework, derived from the location of this script)
Idempotent; all or nothing; keeps CRLF.
"""
import os
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else str(__import__("pathlib").Path(__file__).resolve().parents[2] / "framework")
PATH = os.path.join(ROOT, "games", "tsukihime_common", "hle_movie.cpp")
MARK = "write_frame_to_texture"

TO_TEXTURE = r'''// ---- outputFormat 3: el frame va a la NVNtexture del juego (apply_movie_texture.py) ----------------------------
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

'''

EDITS = [
    ('#include "movie_core.h"\n',
     '#include "movie_core.h"\n#include "nvn/nvn.h"  // Texture, get<>, objects_lock, format_block (apply_movie_texture.py)\n'),
    # The decoder is created after reading the format: RGBA for format 3.
    ("DecoderSlot* make_slot() {\n"
     "    auto* sl = new DecoderSlot;\n"
     "    sl->d = std::make_unique<Decoder>(DecoderKind::Video, VideoFormat::NV12);\n"
     "    return sl;\n"
     "}\n",
     "DecoderSlot* make_slot(uint32_t fmt = 0) {\n"
     "    auto* sl = new DecoderSlot;\n"
     "    // outputFormat 3: el frame va a una NVNtexture del juego, como RGBA (apply_movie_texture.py)\n"
     "    sl->d = std::make_unique<Decoder>(DecoderKind::Video, fmt == 3 ? VideoFormat::RGBA : VideoFormat::NV12);\n"
     "    return sl;\n"
     "}\n"),
    ("    DecoderSlot* sl = make_slot();\n"
     "    sl->type = type, sl->format = fmt, sl->mode = mode;\n",
     "    DecoderSlot* sl = make_slot(fmt);\n"
     "    sl->type = type, sl->format = fmt, sl->mode = mode;\n"),
    ("void fill_output(Decoder* d, int index, uint64_t guest_buf, Status* s) {\n",
     TO_TEXTURE + "void fill_output(Decoder* d, int index, uint64_t guest_buf, Status* s) {\n"),
    ("void Decoder_GetOutputBuffer(nx::CPU& c) {\n"
     "    Status s;\n"
     "    fill_output(dec(c.x[0]), int32_t(c.x[1]), c.x[2], &s);\n"
     "    ret(c, s);\n"
     "}\n"
     "void Decoder_GetOutputBuffer_fmt(nx::CPU& c) {\n"
     "    Status s;\n"
     "    fill_output(dec(c.x[0]), int32_t(c.x[1]), c.x[2], &s);\n",
     "void Decoder_GetOutputBuffer(nx::CPU& c) {\n"
     "    Status s;\n"
     "    if (dslot(c.x[0])->format == 3) s = write_frame_to_texture(dec(c.x[0]), int32_t(c.x[1]), c.x[2]);\n"
     "    else fill_output(dec(c.x[0]), int32_t(c.x[1]), c.x[2], &s);\n"
     "    ret(c, s);\n"
     "}\n"
     "void Decoder_GetOutputBuffer_fmt(nx::CPU& c) {\n"
     "    Status s;\n"
     "    if (dslot(c.x[0])->format == 3) s = write_frame_to_texture(dec(c.x[0]), int32_t(c.x[1]), c.x[2]);\n"
     "    else fill_output(dec(c.x[0]), int32_t(c.x[1]), c.x[2], &s);\n"),
]


def main():
    raw = open(PATH, "rb").read().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    if MARK in text:
        print("hle_movie.cpp: already applied")
        return
    for old, new in EDITS:
        n = text.count(old)
        if n != 1:
            sys.exit(f"hle_movie.cpp: 1 match was expected and there are {n} for:\n{old}\nNothing was changed.")
        text = text.replace(old, new)
    with open(PATH, "wb") as f:
        f.write((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    print(f"hle_movie.cpp: {len(EDITS)} change(s). Rebuild: cmake --build build\\tsukihime")


if __name__ == "__main__":
    main()