#!/usr/bin/env python3
"""spv_inventory.py - what the SPIR-V modules of a folder ask of the Vulkan device (read only).

Usage (in <root>):
  python tools\\dev\\spv_inventory.py data\\tsuki\\spirv [more folders or .spv files]

For every *.spv found (recursively) it reads the header and the leading OpCapability / OpExtension /
OpExtInstImport / OpEntryPoint instructions and prints:
  - how many modules there are of each SPIR-V version (Vulkan 1.1 accepts up to SPIR-V 1.3; 1.4 needs
    VK_KHR_spirv_1_4, 1.5 needs Vulkan 1.2),
  - every capability and extension with the number of modules that declare it, and the Vulkan feature or
    extension a 1.1 device must have for it,
  - the modules above SPIR-V 1.3, by name.
It writes nothing.
"""
import os
import struct
import sys
from collections import Counter, defaultdict

MAGIC = 0x07230203
OP_EXTENSION, OP_EXT_INST_IMPORT, OP_ENTRY_POINT, OP_CAPABILITY = 10, 11, 15, 17
# Instructions that may come before the first type or function (logical layout of a module, sections 1-7)
HEADER_OPS = {OP_CAPABILITY, OP_EXTENSION, OP_EXT_INST_IMPORT, 14, OP_ENTRY_POINT, 16, 3, 4, 5, 6, 7, 8, 330, 331}

MODELS = {0: "vertex", 1: "tess control", 2: "tess evaluation", 3: "geometry", 4: "fragment", 5: "compute"}

# capability -> (name, what a Vulkan 1.1 device needs; "" = always there)
CAPS = {
    0: ("Matrix", ""), 1: ("Shader", ""), 2: ("Geometry", "feature geometryShader"),
    3: ("Tessellation", "feature tessellationShader"), 9: ("Float16", "VK_KHR_shader_float16_int8"),
    10: ("Float64", "feature shaderFloat64"), 11: ("Int64", "feature shaderInt64"),
    22: ("Int16", "feature shaderInt16"), 23: ("TessellationPointSize", "feature shaderTessellationAndGeometryPointSize"),
    24: ("GeometryPointSize", "feature shaderTessellationAndGeometryPointSize"),
    25: ("ImageGatherExtended", "feature shaderImageGatherExtended"),
    27: ("StorageImageMultisample", "feature shaderStorageImageMultisample"),
    28: ("UniformBufferArrayDynamicIndexing", "feature shaderUniformBufferArrayDynamicIndexing"),
    29: ("SampledImageArrayDynamicIndexing", "feature shaderSampledImageArrayDynamicIndexing"),
    30: ("StorageBufferArrayDynamicIndexing", "feature shaderStorageBufferArrayDynamicIndexing"),
    31: ("StorageImageArrayDynamicIndexing", "feature shaderStorageImageArrayDynamicIndexing"),
    32: ("ClipDistance", "feature shaderClipDistance"), 33: ("CullDistance", "feature shaderCullDistance"),
    34: ("ImageCubeArray", "feature imageCubeArray"), 35: ("SampleRateShading", "feature sampleRateShading"),
    36: ("ImageRect", "not in Vulkan"), 37: ("SampledRect", "not in Vulkan"), 39: ("Int8", "VK_KHR_shader_float16_int8"),
    40: ("InputAttachment", ""), 41: ("SparseResidency", "feature shaderResourceResidency"),
    42: ("MinLod", "feature shaderResourceMinLod"), 43: ("Sampled1D", ""), 44: ("Image1D", ""),
    45: ("SampledCubeArray", "feature imageCubeArray"), 46: ("SampledBuffer", ""), 47: ("ImageBuffer", ""),
    48: ("ImageMSArray", "feature shaderStorageImageMultisample"),
    49: ("StorageImageExtendedFormats", "feature shaderStorageImageExtendedFormats"), 50: ("ImageQuery", ""),
    51: ("DerivativeControl", ""), 52: ("InterpolationFunction", "feature sampleRateShading"),
    53: ("TransformFeedback", "VK_EXT_transform_feedback"), 54: ("GeometryStreams", "VK_EXT_transform_feedback"),
    55: ("StorageImageReadWithoutFormat", "feature shaderStorageImageReadWithoutFormat"),
    56: ("StorageImageWriteWithoutFormat", "feature shaderStorageImageWriteWithoutFormat"),
    57: ("MultiViewport", "feature multiViewport"),
    61: ("GroupNonUniform", "subgroup operations (1.1 core, see supportedOperations)"),
    62: ("GroupNonUniformVote", "subgroup VOTE"), 63: ("GroupNonUniformArithmetic", "subgroup ARITHMETIC"),
    64: ("GroupNonUniformBallot", "subgroup BALLOT"), 65: ("GroupNonUniformShuffle", "subgroup SHUFFLE"),
    4423: ("SubgroupBallotKHR", "VK_EXT_shader_subgroup_ballot"), 4427: ("DrawParameters", "1.1 feature shaderDrawParameters"),
    4431: ("SubgroupVoteKHR", "VK_EXT_shader_subgroup_vote"),
    4445: ("StorageInputOutput16", "1.1 feature storageInputOutput16"),
    4447: ("SampleMaskPostDepthCoverage", "VK_EXT_post_depth_coverage"),
    4465: ("DenormPreserve", "VK_KHR_shader_float_controls"), 4466: ("DenormFlushToZero", "VK_KHR_shader_float_controls"),
    4467: ("SignedZeroInfNanPreserve", "VK_KHR_shader_float_controls"),
    4468: ("RoundingModeRTE", "VK_KHR_shader_float_controls"), 4469: ("RoundingModeRTZ", "VK_KHR_shader_float_controls"),
    5013: ("StencilExportEXT", "VK_EXT_shader_stencil_export"),
    5254: ("ShaderViewportIndexLayerEXT", "VK_EXT_shader_viewport_index_layer"),
    5301: ("ShaderNonUniform", "VK_EXT_descriptor_indexing"), 5302: ("RuntimeDescriptorArray", "VK_EXT_descriptor_indexing"),
    5363: ("FragmentShaderSampleInterlockEXT", "VK_EXT_fragment_shader_interlock"),
    5378: ("FragmentShaderPixelInterlockEXT", "VK_EXT_fragment_shader_interlock"),
    5379: ("DemoteToHelperInvocation", "VK_EXT_shader_demote_to_helper_invocation"),
}

# SPIR-V extension -> what a Vulkan 1.1 device needs ("" = part of 1.1)
EXTS = {
    "SPV_KHR_shader_draw_parameters": "1.1 feature shaderDrawParameters", "SPV_KHR_storage_buffer_storage_class": "",
    "SPV_KHR_variable_pointers": "", "SPV_KHR_16bit_storage": "", "SPV_KHR_multiview": "", "SPV_KHR_device_group": "",
    "SPV_KHR_float_controls": "VK_KHR_shader_float_controls", "SPV_KHR_shader_ballot": "VK_EXT_shader_subgroup_ballot",
    "SPV_KHR_subgroup_vote": "VK_EXT_shader_subgroup_vote", "SPV_KHR_post_depth_coverage": "VK_EXT_post_depth_coverage",
    "SPV_EXT_shader_stencil_export": "VK_EXT_shader_stencil_export",
    "SPV_EXT_shader_viewport_index_layer": "VK_EXT_shader_viewport_index_layer",
    "SPV_EXT_fragment_shader_interlock": "VK_EXT_fragment_shader_interlock",
    "SPV_EXT_demote_to_helper_invocation": "VK_EXT_shader_demote_to_helper_invocation",
    "SPV_EXT_descriptor_indexing": "VK_EXT_descriptor_indexing", "SPV_KHR_8bit_storage": "VK_KHR_8bit_storage",
}


def literal_string(words):
    raw = b"".join(struct.pack("<I", w) for w in words)
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def read_module(path):
    """Returns (version, capabilities, extensions, imports, execution models) or None if it is not SPIR-V."""
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) < 20 or len(raw) % 4:
        return None
    endian = "<" if struct.unpack_from("<I", raw)[0] == MAGIC else ">" if struct.unpack_from(">I", raw)[0] == MAGIC else None
    if endian is None:
        return None
    words = struct.unpack(endian + "%dI" % (len(raw) // 4), raw)
    version = words[1]
    caps, exts, imports, models = [], [], [], []
    i = 5
    while i < len(words):
        op, count = words[i] & 0xFFFF, words[i] >> 16
        if count == 0 or i + count > len(words) or op not in HEADER_OPS:
            break
        if op == OP_CAPABILITY and count >= 2:
            caps.append(words[i + 1])
        elif op == OP_EXTENSION:
            exts.append(literal_string(words[i + 1:i + count]))
        elif op == OP_EXT_INST_IMPORT and count >= 3:
            imports.append(literal_string(words[i + 2:i + count]))
        elif op == OP_ENTRY_POINT and count >= 2:
            models.append(words[i + 1])
        i += count
    return version, caps, exts, imports, models


def version_text(v):
    return "%d.%d" % (v >> 16 & 0xFF, v >> 8 & 0xFF)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    files = []
    for arg in argv[1:]:
        if os.path.isdir(arg):
            for dp, _, names in os.walk(arg):
                files += [os.path.join(dp, n) for n in names if n.lower().endswith(".spv")]
        elif os.path.isfile(arg):
            files.append(arg)
        else:
            print("ERROR: %s does not exist" % arg)
            return 1
    files.sort()
    versions, caps, exts, imports, models = Counter(), Counter(), Counter(), Counter(), Counter()
    by_version, bad = defaultdict(list), []
    for path in files:
        m = read_module(path)
        if m is None:
            bad.append(path)
            continue
        versions[m[0]] += 1
        by_version[m[0]].append(path)
        caps.update(set(m[1]))
        exts.update(set(m[2]))
        imports.update(set(m[3]))
        models.update(set(m[4]))
    total = sum(versions.values())
    print("modules: %d (%d files were not SPIR-V)" % (total, len(bad)))
    for v in sorted(versions):
        note = "" if v <= 0x10300 else "  <-- above 1.3: not accepted by plain Vulkan 1.1"
        print("  SPIR-V %s: %d%s" % (version_text(v), versions[v], note))
    print("stages: " + ", ".join("%s %d" % (MODELS.get(k, "model %d" % k), n) for k, n in sorted(models.items())))
    print("capabilities (modules declaring it; what a Vulkan 1.1 device needs):")
    for c, n in sorted(caps.items(), key=lambda kv: (-kv[1], kv[0])):
        name, need = CAPS.get(c, ("capability %d" % c, "?"))
        print("  %-36s %4d  %s" % (name, n, need or "-"))
    print("extensions:")
    for e, n in sorted(exts.items(), key=lambda kv: (-kv[1], kv[0])):
        print("  %-40s %4d  %s" % (e, n, EXTS.get(e, "?") or "-"))
    if not exts:
        print("  (none)")
    print("extended instruction sets: " + (", ".join("%s %d" % kv for kv in sorted(imports.items())) or "(none)"))
    high = [p for v in sorted(by_version) if v > 0x10300 for p in by_version[v]]
    if high:
        print("modules above SPIR-V 1.3:")
        for p in high:
            print("  " + p)
    for p in bad:
        print("not SPIR-V: " + p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))