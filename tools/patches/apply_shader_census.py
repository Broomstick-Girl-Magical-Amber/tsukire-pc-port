"""apply_shader_census.py - which shaders a scene really draws with, and skipping compute shaders (AP_SHADER_CENSUS).

Why: to find the shader behind a rendering fault seen on one GPU only (small black squares on PowerVR), the shaders
a scene actually uses have to be known and then left out one group at a time. NX_TRACE_PROGRAMS only logs programs
when the game makes them, not which ones a scene draws with, and NX_SKIP_FS covers fragment shaders only.

What changes (runtime/src/nvn/nvn_draw.cpp, every platform; nothing happens unless a variable is set):
  - NX_SHADER_CENSUS=1 (environment or config.txt): every 300 frames, one line per shader the replay used in those
    frames, with how many draws or dispatches used it:
      census: frames 1200..1499: fs 49add6de02fe8180 x1520 (vs 554a344a98ceb613)
      census: frames 1200..1499: cs 0f52fa518a4e4560 x900
    A fragment shader is listed with the vertex shader of its first draw in the window. Scenes are told apart by
    comparing the windows: the menu's against the scene's.
  - NX_SKIP_CS=<hash>,<hash>: dispatches with those compute shaders are dropped, as NX_SKIP_FS does for draws.
    Logged once: "census: dispatches with compute shaders <list> are dropped (NX_SKIP_CS)".
  - ../tools/recomp_tsukihime.ps1: its $Markers row, after the row of apply_android_frame_rate.py.

Idempotent (marker AP_SHADER_CENSUS), all or nothing, keeps CRLF and BOM, refuses if an anchor is missing;
--remove leaves the files byte for byte as they were.

Usage (in <root>):
  python tools\\patches\\apply_shader_census.py            then rebuild (the Android library: a new Android pack)
  python tools\\patches\\apply_shader_census.py --remove
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_SHADER_CENSUS"

DRAW = "runtime/src/nvn/nvn_draw.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

HELPERS_OLD = """void dispatch(VkCommandBuffer cb, const Cmd& c) {
    static const bool off = std::getenv("NX_NO_COMPUTE") != nullptr;  // debug: skip compute dispatches
    if (off) return;
"""
HELPERS_NEW = """// AP_SHADER_CENSUS (apply_shader_census.py): NX_SHADER_CENSUS=1 logs, every 300 frames, the shaders the replay used
// in them (fs with the vs of its first draw, cs) and how often; NX_SKIP_CS=<hash>,<hash> drops dispatches with those
// compute shaders. Both for finding the shader behind a fault seen on one GPU only. Replay thread only.
namespace {
struct CensusEntry {
    uint64_t uses = 0;
    std::string vs;
};
std::map<std::string, CensusEntry> g_census_fs, g_census_cs;
uint64_t g_census_from = ~0ull;

bool census_on() {
    static const bool on = [] {
        const char* e = std::getenv("NX_SHADER_CENSUS");
        return e && *e && *e != '0';
    }();
    return on;
}

void census_flush(uint64_t frame) {
    if (g_census_from == ~0ull) g_census_from = frame;
    if (frame < g_census_from + 300) return;
    for (const auto& [hash, e] : g_census_fs)
        log("census: frames %llu..%llu: fs %s x%llu (vs %s)", (unsigned long long)g_census_from,
            (unsigned long long)(frame - 1), hash.c_str(), (unsigned long long)e.uses, e.vs.c_str());
    for (const auto& [hash, e] : g_census_cs)
        log("census: frames %llu..%llu: cs %s x%llu", (unsigned long long)g_census_from, (unsigned long long)(frame - 1),
            hash.c_str(), (unsigned long long)e.uses);
    g_census_fs.clear();
    g_census_cs.clear();
    g_census_from = frame;
}

void census_draw(const ShaderInfo* vs, const ShaderInfo* fs) {
    if (!census_on() || !fs) return;
    census_flush(g_frame.load(std::memory_order_relaxed));
    CensusEntry& e = g_census_fs[fs->hash];
    if (!e.uses++ && vs) e.vs = vs->hash;
}

// false: the dispatch is dropped (NX_SKIP_CS)
bool census_dispatch(const ShaderInfo* cs) {
    static const std::string skip = std::getenv("NX_SKIP_CS") ? std::getenv("NX_SKIP_CS") : "";
    static bool told = false;
    if (!skip.empty() && !told) {
        told = true;
        log("census: dispatches with compute shaders %s are dropped (NX_SKIP_CS)", skip.c_str());
    }
    if (census_on()) {
        census_flush(g_frame.load(std::memory_order_relaxed));
        g_census_cs[cs->hash].uses++;
    }
    return skip.empty() || skip.find(cs->hash) == std::string::npos;
}
}  // namespace

void dispatch(VkCommandBuffer cb, const Cmd& c) {
    static const bool off = std::getenv("NX_NO_COMPUTE") != nullptr;  // debug: skip compute dispatches
    if (off) return;
"""

CS_OLD = """    if (!cs) return;
    uint32_t x = uint32_t(c.i[0]), y = uint32_t(c.i[1]), z = uint32_t(c.i[2]);
"""
CS_NEW = """    if (!cs) return;
    if (!census_dispatch(cs)) return;  // AP_SHADER_CENSUS: NX_SHADER_CENSUS / NX_SKIP_CS
    uint32_t x = uint32_t(c.i[0]), y = uint32_t(c.i[1]), z = uint32_t(c.i[2]);
"""

FS_OLD = """    if (!debug_shader_overrides(shaders, fs, swapped)) return false;
"""
FS_NEW = """    if (!debug_shader_overrides(shaders, fs, swapped)) return false;
    census_draw(vs, fs);  // AP_SHADER_CENSUS: NX_SHADER_CENSUS
"""

INC_OLD = """#include <set>
"""
INC_NEW = """#include <set>
#include <map>  // AP_SHADER_CENSUS
"""

RECO_OLD = """    @('both', 'framework\\runtime\\src\\platform_android.cpp', 'AP_FRAME_RATE', 'apply_android_frame_rate.py', $false),
"""
RECO_NEW = """    @('both', 'framework\\runtime\\src\\platform_android.cpp', 'AP_FRAME_RATE', 'apply_android_frame_rate.py', $false),
    @('both', 'framework\\runtime\\src\\nvn\\nvn_draw.cpp', 'AP_SHADER_CENSUS', 'apply_shader_census.py', $false),
"""

EDITS = [
    (DRAW, INC_OLD, INC_NEW),
    (DRAW, HELPERS_OLD, HELPERS_NEW),
    (DRAW, CS_OLD, CS_NEW),
    (DRAW, FS_OLD, FS_NEW),
    (RECO, RECO_OLD, RECO_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)