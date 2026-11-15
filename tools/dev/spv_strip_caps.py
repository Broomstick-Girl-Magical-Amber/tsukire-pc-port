#!/usr/bin/env python3
"""spv_strip_caps.py - TEST TOOL: copies of the translated shaders without the capabilities they declare but never use.

Usage (in <root>):
  python tools\\dev\\spv_strip_caps.py data\\tsuki\\spirv _vk11_test\\spirv

For every <hash>.spv of the input folder it writes, in the output folder, the same module without:
  - OpCapability FragmentShaderPixelInterlockEXT / SampleInterlockEXT / ShadingRateInterlockEXT and
    OpExtension "SPV_EXT_fragment_shader_interlock", only if the module has no interlock instruction or execution mode;
  - OpCapability GroupNonUniform* (Vote, Ballot, Shuffle, ...), only if the module has no subgroup instruction and no
    subgroup built-in.
Only words of the header section are removed (no result ids change), so the module stays valid. A module that really
uses one of them is copied unchanged and listed. results.tsv and other files are copied as they are.
The input folder is never written. The output folder must not exist yet (no mixing with an old run).
"""
import os
import shutil
import struct
import sys

MAGIC = 0x07230203
OP_EXTENSION, OP_EXECUTION_MODE, OP_CAPABILITY, OP_DECORATE = 10, 16, 17, 71
OP_FUNCTION = 54
INTERLOCK_CAPS = {5363, 5372, 5378}  # FragmentShaderSampleInterlockEXT, ShadingRateInterlockEXT, FragmentShaderPixelInterlockEXT
INTERLOCK_EXT = "SPV_EXT_fragment_shader_interlock"
INTERLOCK_OPS = {5364, 5365}
INTERLOCK_MODES = {5366, 5367, 5368, 5369, 5370, 5371}
SUBGROUP_CAPS = {61, 62, 63, 64, 65, 66, 67, 68}  # GroupNonUniform, ...Vote, Arithmetic, Ballot, Shuffle, ShuffleRelative, Clustered, Quad
SUBGROUP_OPS = set(range(333, 367)) | {4421, 4422, 4428, 4429, 4430, 4432}
SUBGROUP_BUILTINS = {36, 37, 38, 39, 40, 41, 4416, 4417, 4418, 4419, 4420}


def literal_string(words):
    raw = b"".join(struct.pack("<I", w) for w in words)
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def strip(raw):
    """Returns (new bytes, removed names, kept-because-used names) or None if it is not little-endian SPIR-V."""
    if len(raw) < 20 or len(raw) % 4 or struct.unpack_from("<I", raw)[0] != MAGIC:
        return None
    words = list(struct.unpack("<%dI" % (len(raw) // 4), raw))
    uses_interlock = uses_subgroup = False
    spans = []  # (start, count, op, first operand word or string)
    i = 5
    while i < len(words):
        op, count = words[i] & 0xFFFF, words[i] >> 16
        if count == 0 or i + count > len(words):
            return None
        if op in INTERLOCK_OPS or (op == OP_EXECUTION_MODE and count >= 3 and words[i + 2] in INTERLOCK_MODES):
            uses_interlock = True
        if op in SUBGROUP_OPS or (op == OP_DECORATE and count >= 4 and words[i + 2] == 11 and words[i + 3] in SUBGROUP_BUILTINS):
            uses_subgroup = True
        if op == OP_CAPABILITY and count >= 2:
            spans.append((i, count, op, words[i + 1]))
        elif op == OP_EXTENSION:
            spans.append((i, count, op, literal_string(words[i + 1:i + count])))
        i += count
    drop, removed, kept = set(), [], []
    for start, count, op, what in spans:
        if op == OP_CAPABILITY and what in INTERLOCK_CAPS or op == OP_EXTENSION and what == INTERLOCK_EXT:
            (kept if uses_interlock else removed).append(str(what))
            if not uses_interlock:
                drop.add(start)
        elif op == OP_CAPABILITY and what in SUBGROUP_CAPS:
            (kept if uses_subgroup else removed).append(str(what))
            if not uses_subgroup:
                drop.add(start)
    if not drop:
        return raw, removed, kept
    out, i = words[:5], 5
    while i < len(words):
        count = words[i] >> 16
        if i not in drop:
            out.extend(words[i:i + count])
        i += count
    return struct.pack("<%dI" % len(out), *out), removed, kept


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, dst = argv[1], argv[2]
    if not os.path.isdir(src):
        print("ERROR: %s is not a folder" % src)
        return 1
    if os.path.exists(dst):
        print("ERROR: %s already exists: choose a new folder" % dst)
        return 1
    if os.path.abspath(src) == os.path.abspath(dst):
        print("ERROR: input and output are the same folder")
        return 1
    os.makedirs(dst)
    changed = same = 0
    used = []
    for name in sorted(os.listdir(src)):
        p = os.path.join(src, name)
        if not os.path.isfile(p):
            continue
        q = os.path.join(dst, name)
        if not name.lower().endswith(".spv"):
            shutil.copy2(p, q)
            continue
        with open(p, "rb") as f:
            raw = f.read()
        r = strip(raw)
        if r is None:
            shutil.copy2(p, q)
            print("not SPIR-V (copied as is): " + name)
            continue
        data, removed, kept = r
        with open(q, "wb") as f:
            f.write(data)
        if data != raw:
            changed += 1
        else:
            same += 1
        if kept:
            used.append("%s (kept because used: %s)" % (name, ", ".join(kept)))
    print("SUMMARY changed=%d unchanged=%d used=%d -> %s" % (changed, same, len(used), dst))
    for u in used:
        print("  " + u)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))