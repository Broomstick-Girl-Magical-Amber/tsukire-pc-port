#!/usr/bin/env python3
"""apply_audio_out.py - complete AudioOut in the framework audio HLE (the Tsukihime videos need it).

The framework AudioOut HLE was written for a game with FMOD (48 kHz, stereo, s16): it did not have
OpenDefaultAudioOut without an event or GetAudioOutSampleFormat / SampleRate / ChannelCount / BufferDataSize /
PlayedSampleCount. Tsukihime opens an AudioOut for the audio of the videos and asks for the format; without HLE the call
went to the real sdk (IPC to audout:u, a stub), returned garbage and nn::audio::GetSampleByteSize aborted.

Also, if the game already uses the AudioRenderer (Tsukihime and Wonder), the device belongs to the render thread. Sending
the AudioOut buffers to the device as well would put them one after the other in the same queue (not mixed). That is why
in that case the AudioOut buffers are MIXED in each 5 ms frame of the renderer (audio_out_mix) and released when
they finished playing. This way the device clock sets the pace of the video (PlayedSampleCount).
Without a renderer (Hades) everything stays the same: the player thread sends the buffers to the device.

Requires apply_audio_sdk.py. Keeps CRLF, is idempotent and updates a previous version already applied.
Usage (from <root>\\framework):  private\\venv\\Scripts\\python ..\\tools\\patches\\apply_audio_out.py
"""
import sys
from pathlib import Path

AUDIO = Path("runtime/src/hle_audio.cpp")
MIX_START = "bool renderer_running() {"

BLOCK_START = "constexpr uint64_t kBytesPerSecond = 48000 * 2 * 2;"
BLOCK_END = "void ListAudioIns(CPU& c)"

BLOCK = r'''constexpr uint64_t kBytesPerSecond = 48000 * 2 * 2;
constexpr uint32_t kMaxInFlight = 4;  // buffers queued in the device (4 x ~21 ms)

// Our AudioOutBuffer layout (only our HLE reads it): {void* data; u64 capacity; u64 size}. s16 samples, interleaved.
// One AudioOut at a time (the games so far open one).
struct Out {
    std::mutex lock;
    std::condition_variable cv;
    std::deque<uint64_t> queued, released;
    std::shared_ptr<EventState> event = std::make_shared<EventState>();
    bool started = false;
    uint32_t rate = 48000, channels = 2;
    uint64_t played = 0;  // sample frames played since the AudioOut was opened
    uint64_t offset = 0;  // bytes of queued.front() already mixed (renderer mode)
    double phase = 0;     // resampling position between two source frames (renderer mode)
};
Out g_out;

// When the game runs an AudioRenderer the device belongs to the render thread: AudioOut buffers are mixed into its
// 5 ms frames (audio_out_mix, next to render_submit) instead of being queued on the device after its blocks.
bool renderer_running();

void release(void* ctx) {
    std::lock_guard lk(g_out.lock);
    g_out.released.push_back(reinterpret_cast<uint64_t>(ctx));
    g_out.event->signaled = true;
    notify_all();
    g_out.cv.notify_all();
}

// The platform device has one completion callback: AudioOut buffers are guest addresses, the renderer's blocks
// live in g_render (below) and go back to its free list.
void render_block_done(void* ctx);
bool render_owns(void* ctx);

void device_done(void* ctx) {
    if (render_owns(ctx))
        render_block_done(ctx);
    else
        release(ctx);
}

bool device_open() {
    static const bool ok = plat::audio_open(device_done);
    return ok;
}

void player() {
    bool device = device_open();
    log("audio: %s", device ? "device output (48 kHz stereo s16)" : "no audio device, timer pacing");
    using clock = std::chrono::steady_clock;
    auto next = clock::now();
    for (;;) {
        uint64_t buf = 0, size = 0;
        {
            std::unique_lock lk(g_out.lock);
            auto ready = [&] {
                return g_out.started && !g_out.queued.empty() && (!device || plat::audio_queued() < kMaxInFlight);
            };
            g_out.cv.wait_for(lk, std::chrono::milliseconds(5), ready);
            if (!ready() || renderer_running()) continue;  // renderer mode: audio_out_mix plays them
            buf = g_out.queued.front();
            g_out.queued.pop_front();
            size = ld<uint64_t>(buf + 16);
            g_out.played += size / (2 * g_out.channels);
        }
        if (device && plat::audio_submit(reinterpret_cast<const void*>(ld<uint64_t>(buf)), size,
                                         reinterpret_cast<void*>(buf)))
            continue;
        next = std::max(next, clock::now() - std::chrono::milliseconds(50));
        next += std::chrono::microseconds(size * 1000000 / kBytesPerSecond);
        std::this_thread::sleep_until(next);
        release(reinterpret_cast<void*>(buf));
    }
}

void InitializeAudioOutParameter(CPU& c) {  // {u32 sample_rate; u16 channels; u16 reserved}
    st<uint32_t>(c.x[0], 0);
    st<uint16_t>(c.x[0] + 4, 0);
    st<uint16_t>(c.x[0] + 6, 0);
}

void open_out(uint64_t out, uint64_t param) {
    uint32_t rate = param ? ld<uint32_t>(param) : 0;
    uint32_t channels = param ? ld<uint16_t>(param + 4) : 0;
    {
        std::lock_guard lk(g_out.lock);
        g_out.rate = rate ? rate : 48000;
        g_out.channels = channels == 1 ? 1 : 2;  // 0 = default (stereo); the device is stereo anyway
        g_out.queued.clear();
        g_out.released.clear();
        g_out.event->signaled = false;
        g_out.started = false;
        g_out.played = g_out.offset = 0;
        g_out.phase = 0;
    }
    log("audio: AudioOut opened, %u Hz, %u channels, s16", g_out.rate, g_out.channels);
    st<uint64_t>(out, 0xA0D10);  // opaque
    static std::once_flag once;
    std::call_once(once, [] { std::thread(player).detach(); });
}

void OpenDefaultAudioOut(CPU& c) {  // (AudioOut*, os::SystemEvent*, const AudioOutParameter&)
    uint64_t out = c.x[0], sysev = c.x[1], param = c.x[2];
    // Give the guest a system event backed by our kernel event: nn::os::AttachSystemEvent(type, read, managed,
    // write, managed, clear mode = manual).
    auto w = std::make_shared<KEvent>();
    auto r = std::make_shared<KEvent>();
    w->state = r->state = g_out.event;
    uint32_t hr = handle_add(r), hw = handle_add(w);
    uint64_t attach = find_symbol("_ZN2nn2os17AttachSystemEventEPNS0_15SystemEventTypeEjbjbNS0_14EventClearModeE");
    uint64_t saved[8];
    for (int k = 0; k < 8; k++) saved[k] = c.x[k];
    c.x[0] = sysev;
    c.x[1] = hr;
    c.x[2] = 1;
    c.x[3] = hw;
    c.x[4] = 1;
    c.x[5] = 1;  // manual clear
    call(c, attach);
    for (int k = 0; k < 8; k++) c.x[k] = saved[k];
    open_out(out, param);
    c.x[0] = 0;
}

void OpenDefaultAudioOutNoEvent(CPU& c) {  // (AudioOut*, const AudioOutParameter&): the game polls instead
    open_out(c.x[0], c.x[1]);
    c.x[0] = 0;
}

void StartAudioOut(CPU& c) {
    std::lock_guard lk(g_out.lock);
    log("audio: AudioOut started (%zu buffers queued)", g_out.queued.size());
    g_out.started = true;
    g_out.cv.notify_all();
    c.x[0] = 0;
}

void StopAudioOut(CPU&) {
    std::lock_guard lk(g_out.lock);
    g_out.started = false;
}

void CloseAudioOut(CPU&) {
    std::lock_guard lk(g_out.lock);
    g_out.started = false;
    g_out.queued.clear();
    g_out.released.clear();
    g_out.offset = 0;
}

void SetAudioOutBufferInfo(CPU& c) {  // (AudioOutBuffer*, void* data, size_t capacity, size_t size)
    st<uint64_t>(c.x[0], c.x[1]);
    st<uint64_t>(c.x[0] + 8, c.x[2]);
    st<uint64_t>(c.x[0] + 16, c.x[3]);
}

void GetAudioOutBufferDataPointer(CPU& c) { c.x[0] = ld<uint64_t>(c.x[0]); }
void GetAudioOutBufferDataSize(CPU& c) { c.x[0] = ld<uint64_t>(c.x[0] + 16); }

void GetAudioOutSampleRate(CPU& c) { c.x[0] = g_out.rate; }
void GetAudioOutChannelCount(CPU& c) { c.x[0] = g_out.channels; }
void GetAudioOutSampleFormat(CPU& c) { c.x[0] = 2; }  // nn::audio::SampleFormat_PcmInt16
void GetAudioOutPlayedSampleCount(CPU& c) {
    std::lock_guard lk(g_out.lock);
    c.x[0] = g_out.played;
}

void AppendAudioOutBuffer(CPU& c) {  // (AudioOut*, AudioOutBuffer*)
    std::lock_guard lk(g_out.lock);
    static uint64_t appended = 0;
    if (++appended == 1 || appended % 1000 == 0)
        log("audio: AudioOut buffer #%llu appended (%llu bytes, started %d, %llu frames played)",
            (unsigned long long)appended, (unsigned long long)ld<uint64_t>(c.x[1] + 16), int(g_out.started),
            (unsigned long long)g_out.played);
    g_out.queued.push_back(c.x[1]);
    g_out.cv.notify_all();
    c.x[0] = 1;  // bool: appended
}

void GetReleasedAudioOutBuffer(CPU& c) {
    std::lock_guard lk(g_out.lock);
    if (g_out.released.empty()) {
        c.x[0] = 0;
        return;
    }
    c.x[0] = g_out.released.front();
    g_out.released.pop_front();
    if (g_out.released.empty()) g_out.event->signaled = false;
}

'''

MIX_ANCHOR = "// Called by the render thread after each 5 ms frame."
MIX = r'''bool renderer_running() {
    std::lock_guard lk(g_renderers_lock);
    return !g_renderers.empty();
}

// Renderer mode: adds the next 5 ms of the AudioOut's queue (resampled to 48 kHz, nearest frame) to `mix` and gives
// back each buffer once it has been played. Called by render_submit under g_render.lock.
bool audio_out_mix(int32_t* mix) {
    std::lock_guard lk(g_out.lock);
    if (!g_out.started || g_out.queued.empty()) return false;
    const uint32_t ch = g_out.channels, frame_bytes = 2 * ch;
    const double step = double(g_out.rate) / 48000.0;
    bool any = false, freed = false;
    for (int s = 0; s < kFrame; s++) {
        uint64_t b = 0;
        while (!g_out.queued.empty()) {
            b = g_out.queued.front();
            if (g_out.offset + frame_bytes <= ld<uint64_t>(b + 16)) break;
            g_out.queued.pop_front();  // played: back to the game
            g_out.released.push_back(b);
            g_out.offset = 0;
            freed = true;
            b = 0;
        }
        if (!b) break;  // the game is late: the rest of this frame stays silent
        auto p = reinterpret_cast<const int16_t*>(ld<uint64_t>(b) + g_out.offset);
        static bool heard = false;
        if (!heard && (p[0] || (ch > 1 && p[1]))) heard = true, log("audio: first AudioOut sound mixed");
        mix[2 * s] += p[0];
        mix[2 * s + 1] += ch > 1 ? p[1] : p[0];
        any = true;
        for (g_out.phase += step; g_out.phase >= 1.0; g_out.phase -= 1.0) {
            g_out.offset += frame_bytes;
            g_out.played++;
        }
    }
    if (!g_out.queued.empty() && g_out.offset + frame_bytes > ld<uint64_t>(g_out.queued.front() + 16)) {
        g_out.released.push_back(g_out.queued.front());
        g_out.queued.pop_front();
        g_out.offset = 0;
        freed = true;
    }
    if (freed) {
        g_out.event->signaled = true;
        notify_all();
        g_out.cv.notify_all();
    }
    return any;
}

'''

REPLACEMENTS = [
    ("    RenderOut& o = g_render;\n    if (!o.mixed) return;",
     "    RenderOut& o = g_render;\n    if (audio_out_mix(o.mix)) o.mixed = true;  // the AudioOut (movies) on top of the renderer\n"
     "    if (!o.mixed) return;"),
]

REG_ANCHOR = 'NX_HLE("_ZN2nn5audio25GetReleasedAudioOutBufferEPNS0_8AudioOutE", GetReleasedAudioOutBuffer);'
REGS = '''
NX_HLE("_ZN2nn5audio19OpenDefaultAudioOutEPNS0_8AudioOutERKNS0_17AudioOutParameterE", OpenDefaultAudioOutNoEvent);
NX_HLE("_ZN2nn5audio25GetAudioOutBufferDataSizeEPKNS0_14AudioOutBufferE", GetAudioOutBufferDataSize);
NX_HLE("_ZN2nn5audio21GetAudioOutSampleRateEPKNS0_8AudioOutE", GetAudioOutSampleRate);
NX_HLE("_ZN2nn5audio23GetAudioOutChannelCountEPKNS0_8AudioOutE", GetAudioOutChannelCount);
NX_HLE("_ZN2nn5audio23GetAudioOutSampleFormatEPKNS0_8AudioOutE", GetAudioOutSampleFormat);
NX_HLE("_ZN2nn5audio28GetAudioOutPlayedSampleCountEPKNS0_8AudioOutE", GetAudioOutPlayedSampleCount);'''


def load(path):
    raw = path.read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n"), "\r\n" in raw


def save(path, text, crlf):
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def main():
    if not AUDIO.exists():
        sys.exit(f"cannot find {AUDIO}: run this from <root>\\framework")
    text, crlf = load(AUDIO)
    if "execution_mode_offset" not in text:
        sys.exit("apply_audio_sdk.py is missing: apply it first")
    before = text
    # 1) the AudioOut block (original or from a previous version of this script)
    a, b = text.find(BLOCK_START), text.find(BLOCK_END)
    if a < 0 or b < 0 or b < a:
        sys.exit("did not find the AudioOut block (kBytesPerSecond .. ListAudioIns); the file changed")
    text = text[:a] + BLOCK + text[b:]
    # 2) the mix, next to render_submit
    if text.count(MIX_ANCHOR) != 1:
        sys.exit("did not find (exactly once) the render_submit anchor")
    m = text.find(MIX_START)
    if m >= 0:
        text = text[:m] + text[text.find(MIX_ANCHOR):]
    text = text.replace(MIX_ANCHOR, MIX + MIX_ANCHOR)
    # 3) registrations and call from render_submit (only once)
    if REGS.strip().splitlines()[0] not in text:
        if text.count(REG_ANCHOR) != 1:
            sys.exit("did not find (exactly once) the registration of GetReleasedAudioOutBuffer")
        text = text.replace(REG_ANCHOR, REG_ANCHOR + REGS)
    for old, new in REPLACEMENTS:
        if new in text:
            continue
        if text.count(old) != 1:
            sys.exit(f"did not find (exactly once):\n{old}")
        text = text.replace(old, new)
    if text == before:
        print(f"{AUDIO}: already up to date, nothing to do")
        return
    save(AUDIO, text, crlf)
    print(f"{AUDIO}: complete AudioOut + mix over the renderer ({'CRLF' if crlf else 'LF'})")


if __name__ == "__main__":
    main()