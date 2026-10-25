// NVN programs (docs/NVN.md, N5): the shader stages the game binds. Each stage arrives as Maxwell code at a GPU
// address in one of its memory pools, plus the NVN control section (docs/nvn/SHADERS.md). The code was translated
// to SPIR-V offline (docs/nvn/TRANSLATOR.md); nvn_shaders.cpp finds each stage's by the hash of its code block.
//
// nvnProgramSetShaders(NVNprogram*, int count, const NVNshaderData*): {NVNbufferAddress code; const void* control}
// per stage (0x10 bytes); the Ext variant's entries have a third, zero word (0x18 bytes).
#include <cstdlib>
#include <string>
#include <vector>

#include "nvn.h"
#include "nvn_shaders.h"

namespace nx::nvn {

namespace {

struct Program : Object {
    std::vector<const ShaderInfo*> shaders;  // a stage with no translation is left out (and logged once)
};

void ProgramInitialize(CPU& c) {  // NVNboolean (NVNprogram*, NVNdevice*)
    make<Program>(c.x[0]);
    c.x[0] = 1;
}

void ProgramFinalize(CPU& c) { destroy(c.x[0]); }

void set_shaders(CPU& c, size_t stride) {
    std::vector<const ShaderInfo*> shaders;
    for (int k = 0; k < int(c.x[1]); k++) {
        uint64_t entry = c.x[2] + uint64_t(k) * stride;
        uint64_t code = ld<uint64_t>(entry), control = ld<uint64_t>(entry + 8);
        uint64_t offset = 0;
        MemoryPool* pool = pool_at_gpu(code, &offset);
        if (!pool || !control) {
            log("nvn: program %llx stage %d: code %llx control %llx not found", (unsigned long long)c.x[0], k,
                (unsigned long long)code, (unsigned long long)control);
            continue;
        }
        if (const ShaderInfo* s = shader_for(pool->memory + offset, control)) shaders.push_back(s);
    }
    static const bool trace = std::getenv("NX_TRACE_PROGRAMS") != nullptr;  // each program's stages when set
    if (trace) {
        std::string line;
        for (const ShaderInfo* s : shaders) line += " " + s->hash;
        log("nvn: program %llx set:%s", (unsigned long long)c.x[0], line.c_str());
    }
    if (auto* p = get<Program>(c.x[0])) p->shaders = std::move(shaders);
    c.x[0] = 1;
}

void ProgramSetShaders(CPU& c) { set_shaders(c, 0x10); }
void ProgramSetShadersExt(CPU& c) { set_shaders(c, 0x18); }

}  // namespace

const std::vector<const ShaderInfo*>* program_shaders(uint64_t program) {
    auto* p = get<Program>(program);
    return p ? &p->shaders : nullptr;
}

NX_HLE("nvnProgramInitialize", ProgramInitialize);
NX_HLE("nvnProgramFinalize", ProgramFinalize);
NX_HLE("nvnProgramSetShaders", ProgramSetShaders);
NX_HLE("nvnProgramSetShadersExt", ProgramSetShadersExt);

}  // namespace nx::nvn
