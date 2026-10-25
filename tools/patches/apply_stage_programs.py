#!/usr/bin/env python3
"""apply_stage_programs.py - NVN programs bound per stage (BindProgram with stage bits).

NVN binds programs per stage: nvnCommandBufferBindProgram(cmd, program, stageBits). Tsukihime binds a
program embedded in the exe for the vertex (a quad) and then a program from a .bnsh that only has a
fragment, with stageBits = FRAGMENT. The runtime kept a single program (g_state.program = the last one), so
the vertex was lost and half of the draws came out "no vertex shader".

Changes:
  - nvn_draw_state.h:   State::stage_program[kStages] (program bound at each stage).
  - nvn_draw_state.cpp: BindProgram spreads c.mask (the stage bits, 1 << NVN stage) over stage_program.
  - nvn_draw.cpp:       bound_shaders(): vertex from the program of stage 0, fragment from the one of stage 1
                        (compute: stage 5). The same program in every stage (Wonder) goes the usual way,
                        copying nothing.

Usage: python apply_stage_programs.py [framework root]   (default: <root>\\framework, derived from the location of this script)
Idempotent: if it is already applied it changes nothing.
"""
import os
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else str(__import__("pathlib").Path(__file__).resolve().parents[2] / "framework")
NVN = os.path.join(ROOT, "runtime", "src", "nvn")
MARK = "stage_program"

EDITS = {
    "nvn_draw_state.h": [(
        "    uint64_t program = 0;\n",
        "    uint64_t program = 0;  // the last bound (any stages)\n"
        "    uint64_t stage_program[kStages] = {};  // BindProgram per stage bit (1 << NVN stage): apply_stage_programs.py\n",
    )],
    "nvn_draw_state.cpp": [(
        "        case Cmd::BindProgram: s.program = c.a[0]; break;\n",
        "        case Cmd::BindProgram: {  // stage bits: 1 << NVN stage (vertex 1, fragment 2, ..., compute 0x20)\n"
        "            s.program = c.a[0];\n"
        "            uint32_t bits = c.mask ? c.mask : (1u << kStages) - 1;\n"
        "            for (int k = 0; k < kStages; k++)\n"
        "                if (bits & (1u << k)) s.stage_program[k] = c.a[0];\n"
        "            break;\n"
        "        }\n",
    )],
    "nvn_draw.cpp": [
        (
            "void dispatch(VkCommandBuffer cb, const Cmd& c) {\n",
            "// The stages a draw or dispatch uses. NVN binds programs per stage (BindProgram's stage bits): the vertex\n"
            "// stage may come from one program and the fragment stage from another (Tsukihime: fragment-only programs\n"
            "// from .bnsh files over a vertex program embedded in main). One program in both stages is returned as is.\n"
            "// apply_stage_programs.py\n"
            "static const std::vector<const ShaderInfo*>* bound_shaders(bool compute) {\n"
            "    const State& s = g_state;\n"
            "    if (compute) return program_shaders(s.stage_program[5] ? s.stage_program[5] : s.program);\n"
            "    uint64_t vp = s.stage_program[0], fp = s.stage_program[1];\n"
            "    if (!vp && !fp) return program_shaders(s.program);\n"
            "    if (vp == fp) return program_shaders(vp);\n"
            "    static std::vector<const ShaderInfo*> mixed;  // replay thread only; read before the next draw\n"
            "    mixed.clear();\n"
            "    auto take = [&](uint64_t p, ShaderStage want) {\n"
            "        if (const auto* sh = p ? program_shaders(p) : nullptr)\n"
            "            for (const ShaderInfo* x : *sh)\n"
            "                if (x->stage == want) mixed.push_back(x);\n"
            "    };\n"
            "    take(vp, ShaderStage::Vertex);\n"
            "    take(fp, ShaderStage::Fragment);\n"
            "    return &mixed;\n"
            "}\n"
            "\n"
            "void dispatch(VkCommandBuffer cb, const Cmd& c) {\n",
        ),
        (
            "    if (off) return;\n"
            "    const auto* shaders = program_shaders(g_state.program);\n",
            "    if (off) return;\n"
            "    const auto* shaders = bound_shaders(true);\n",
        ),
        (
            "    if (g_state.rasterizer_discard || !g_state.render_enable) return false;  // nothing reaches the targets\n"
            "    const auto* shaders = program_shaders(g_state.program);\n",
            "    if (g_state.rasterizer_discard || !g_state.render_enable) return false;  // nothing reaches the targets\n"
            "    const auto* shaders = bound_shaders(false);\n",
        ),
    ],
}


def main():
    plans = []
    for name, edits in EDITS.items():
        path = os.path.join(NVN, name)
        raw = open(path, "rb").read().decode("utf-8")
        crlf = "\r\n" in raw
        text = raw.replace("\r\n", "\n")
        if MARK in text:
            print(f"{name}: already applied")
            continue
        for old, new in edits:
            n = text.count(old)
            if n != 1:
                sys.exit(f"{name}: 1 match was expected and there are {n} for:\n{old}\nNo file was changed.")
            text = text.replace(old, new)
        plans.append((path, name, text.replace("\n", "\r\n") if crlf else text, len(edits)))
    # Everything checked: only now it is written (all or nothing).
    for path, name, text, n in plans:
        with open(path, "wb") as f:
            f.write(text.encode("utf-8"))
        print(f"{name}: {n} change(s)")
    if plans:
        print("Done. Rebuild: cmake --build build\\tsukihime")


if __name__ == "__main__":
    main()