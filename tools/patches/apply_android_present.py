"""apply_android_present.py - Android presentation of Tsukihime: upright picture, bars, sRGB after resume (AP_ANDROID_PRESENT).

Phase 1 of the Android port. On Android the picture always goes through the present pass
(nvn_present.cpp: it turns it with the screen and fits it with bars), not through the blit of nvn_vk.cpp, and three
things were missing for Tsukihime there:
  - runtime/src/nvn/nvn_present.cpp: the window origin. Tsukihime draws with NVN's lower-left origin
    (NX_DEFAULT_ORIGIN_LOWER_LEFT, apply_origin_default.py): row 0 of the window texture is the bottom. The blit flips
    it (apply_origin_lower_left.py); the present pass did not, so the picture came out upside down. Now the pass samples
    the crop backwards on that axis (NX_FLIP_Y=0|1 and NX_FLIP_X=0|1 in config.txt override each axis by hand). FSR
    (NX_FSR=1) is skipped while flipped (its pass reads the crop forwards).
  - same file: with NX_EXTEND_BARS (tsukihime_common.cmake defines it on Android, apply_android_build.py) the bars
    around the picture repeat its nearest edge pixel instead of black (a second fragment shader, kFragmentExtend).
  - runtime/src/nvn/nvn_vk.cpp: after home / the lock screen Android gives a new window; make_surface goes back to a
    UNORM swapchain format and the sRGB one that pick_swapchain_format chose (once per run, apply_srgb_present.py) was
    lost: the picture came back darker. Now the format of the old surface is kept if the new surface offers it (only on
    Android; the lines of apply_srgb_present.py are not touched).
The log line "NX_ORIGIN_LOWER_LEFT: the present pass does not flip the window texture" (apply_origin_lower_left.py)
is left as is; with this patch the next line says what the pass does ("present pass: flip ...").
Windows and Linux/Proton use the blit unless NX_PRESENT_PASS=1; then the flip applies there too (it was upside down).

Idempotent (marker AP_ANDROID_PRESENT), all or nothing, keeps CRLF and BOM, --remove leaves the files byte for byte as
they were. Independent of the other AP_ANDROID_* patchers. Also edits ../tools/recomp_tsukihime.ps1 ($Markers rows,
anchored after the row of apply_gpu_hang.py for nvn_frames.cpp, which no other patcher uses).
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_android_present.py
"""
from _patch import run

MARK = "AP_ANDROID_PRESENT"
PRESENT = "runtime/src/nvn/nvn_present.cpp"
VK = "runtime/src/nvn/nvn_vk.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

EXTEND = """#ifdef NX_EXTEND_BARS  // AP_ANDROID_PRESENT (apply_android_present.py; tsukihime_common.cmake sets it on Android)
// The bars around the picture repeat its nearest edge pixel instead of black: present.frag above with
// `c = texture(src, p.crop.xy + clamp(b, 0.0, 1.0) * p.crop.zw).rgb;` in place of the test and sample inside the box
// (glslang -V --target-env vulkan1.0); same bindings, push constants and outputs.
const uint32_t kFragmentExtend[] = {
    0x07230203, 0x00010000, 0x0008000b, 0x000000a3, 0x00000000, 0x00020011, 0x00000001, 0x0006000b, 0x00000001,
    0x4c534c47, 0x6474732e, 0x3035342e, 0x00000000, 0x0003000e, 0x00000000, 0x00000001, 0x0007000f, 0x00000004,
    0x00000004, 0x6e69616d, 0x00000000, 0x00000022, 0x0000009d, 0x00030010, 0x00000004, 0x00000007, 0x00030003,
    0x00000002, 0x000001c2, 0x00040005, 0x00000004, 0x6e69616d, 0x00000000, 0x00030005, 0x00000009, 0x00007075,
    0x00030005, 0x0000000b, 0x00000050, 0x00040006, 0x0000000b, 0x00000000, 0x00746f72, 0x00040006, 0x0000000b,
    0x00000001, 0x0066666f, 0x00040006, 0x0000000b, 0x00000002, 0x00786f62, 0x00050006, 0x0000000b, 0x00000003,
    0x706f7263, 0x00000000, 0x00030005, 0x0000000d, 0x00000070, 0x00030005, 0x00000022, 0x00007675, 0x00030005,
    0x0000002a, 0x00000062, 0x00030005, 0x00000037, 0x00000063, 0x00030005, 0x0000003b, 0x00637273, 0x00030005,
    0x0000004d, 0x0000006e, 0x00030005, 0x00000054, 0x00000069, 0x00030005, 0x0000005f, 0x00000073, 0x00040005,
    0x00000062, 0x7265764f, 0x0079616c, 0x00050006, 0x00000062, 0x00000000, 0x70616873, 0x00000065, 0x00030005,
    0x00000064, 0x0000766f, 0x00030005, 0x0000006a, 0x00000064, 0x00040005, 0x0000009d, 0x6f6c6f63, 0x00000072,
    0x00030047, 0x0000000b, 0x00000002, 0x00050048, 0x0000000b, 0x00000000, 0x00000023, 0x00000000, 0x00050048,
    0x0000000b, 0x00000001, 0x00000023, 0x00000010, 0x00050048, 0x0000000b, 0x00000002, 0x00000023, 0x00000020,
    0x00050048, 0x0000000b, 0x00000003, 0x00000023, 0x00000030, 0x00040047, 0x00000022, 0x0000001e, 0x00000000,
    0x00040047, 0x0000003b, 0x00000021, 0x00000000, 0x00040047, 0x0000003b, 0x00000022, 0x00000000, 0x00040047,
    0x00000061, 0x00000006, 0x00000010, 0x00030047, 0x00000062, 0x00000002, 0x00050048, 0x00000062, 0x00000000,
    0x00000023, 0x00000000, 0x00040047, 0x00000064, 0x00000021, 0x00000001, 0x00040047, 0x00000064, 0x00000022,
    0x00000000, 0x00040047, 0x0000009d, 0x0000001e, 0x00000000, 0x00020013, 0x00000002, 0x00030021, 0x00000003,
    0x00000002, 0x00030016, 0x00000006, 0x00000020, 0x00040017, 0x00000007, 0x00000006, 0x00000002, 0x00040020,
    0x00000008, 0x00000007, 0x00000007, 0x00040017, 0x0000000a, 0x00000006, 0x00000004, 0x0006001e, 0x0000000b,
    0x0000000a, 0x0000000a, 0x0000000a, 0x0000000a, 0x00040020, 0x0000000c, 0x00000009, 0x0000000b, 0x0004003b,
    0x0000000c, 0x0000000d, 0x00000009, 0x00040015, 0x0000000e, 0x00000020, 0x00000001, 0x0004002b, 0x0000000e,
    0x0000000f, 0x00000000, 0x00040020, 0x00000010, 0x00000009, 0x0000000a, 0x00040018, 0x00000017, 0x00000007,
    0x00000002, 0x0004002b, 0x00000006, 0x00000018, 0x3f800000, 0x0004002b, 0x00000006, 0x00000019, 0x00000000,
    0x00040020, 0x00000021, 0x00000001, 0x00000007, 0x0004003b, 0x00000021, 0x00000022, 0x00000001, 0x0004002b,
    0x0000000e, 0x00000025, 0x00000001, 0x0004002b, 0x0000000e, 0x0000002c, 0x00000002, 0x00040017, 0x00000035,
    0x00000006, 0x00000003, 0x00040020, 0x00000036, 0x00000007, 0x00000035, 0x00090019, 0x00000038, 0x00000006,
    0x00000001, 0x00000000, 0x00000000, 0x00000000, 0x00000001, 0x00000000, 0x0003001b, 0x00000039, 0x00000038,
    0x00040020, 0x0000003a, 0x00000000, 0x00000039, 0x0004003b, 0x0000003a, 0x0000003b, 0x00000000, 0x0004002b,
    0x0000000e, 0x0000003d, 0x00000003, 0x0005002c, 0x00000007, 0x00000042, 0x00000019, 0x00000019, 0x0005002c,
    0x00000007, 0x00000043, 0x00000018, 0x00000018, 0x00040020, 0x0000004c, 0x00000007, 0x0000000e, 0x00040015,
    0x0000004e, 0x00000020, 0x00000000, 0x0004002b, 0x0000004e, 0x0000004f, 0x00000002, 0x00040020, 0x00000050,
    0x00000009, 0x00000006, 0x00020014, 0x0000005c, 0x00040020, 0x0000005e, 0x00000007, 0x0000000a, 0x0004002b,
    0x0000004e, 0x00000060, 0x00000020, 0x0004001c, 0x00000061, 0x0000000a, 0x00000060, 0x0003001e, 0x00000062,
    0x00000061, 0x00040020, 0x00000063, 0x00000002, 0x00000062, 0x0004003b, 0x00000063, 0x00000064, 0x00000002,
    0x00040020, 0x00000066, 0x00000002, 0x0000000a, 0x00040020, 0x00000069, 0x00000007, 0x00000006, 0x0004002b,
    0x0000004e, 0x0000006b, 0x00000000, 0x0004002b, 0x0000004e, 0x00000071, 0x00000003, 0x0004002b, 0x0000004e,
    0x00000075, 0x00000001, 0x0006002c, 0x00000035, 0x00000084, 0x00000018, 0x00000018, 0x00000018, 0x0004002b,
    0x00000006, 0x0000008d, 0x3ba3d70a, 0x0004002b, 0x00000006, 0x00000095, 0x40400000, 0x00040020, 0x0000009c,
    0x00000003, 0x0000000a, 0x0004003b, 0x0000009c, 0x0000009d, 0x00000003, 0x00050036, 0x00000002, 0x00000004,
    0x00000000, 0x00000003, 0x000200f8, 0x00000005, 0x0004003b, 0x00000008, 0x00000009, 0x00000007, 0x0004003b,
    0x00000008, 0x0000002a, 0x00000007, 0x0004003b, 0x00000036, 0x00000037, 0x00000007, 0x0004003b, 0x0000004c,
    0x0000004d, 0x00000007, 0x0004003b, 0x0000004c, 0x00000054, 0x00000007, 0x0004003b, 0x0000005e, 0x0000005f,
    0x00000007, 0x0004003b, 0x00000069, 0x0000006a, 0x00000007, 0x00050041, 0x00000010, 0x00000011, 0x0000000d,
    0x0000000f, 0x0004003d, 0x0000000a, 0x00000012, 0x00000011, 0x0007004f, 0x00000007, 0x00000013, 0x00000012,
    0x00000012, 0x00000000, 0x00000001, 0x00050041, 0x00000010, 0x00000014, 0x0000000d, 0x0000000f, 0x0004003d,
    0x0000000a, 0x00000015, 0x00000014, 0x0007004f, 0x00000007, 0x00000016, 0x00000015, 0x00000015, 0x00000002,
    0x00000003, 0x00050051, 0x00000006, 0x0000001a, 0x00000013, 0x00000000, 0x00050051, 0x00000006, 0x0000001b,
    0x00000013, 0x00000001, 0x00050051, 0x00000006, 0x0000001c, 0x00000016, 0x00000000, 0x00050051, 0x00000006,
    0x0000001d, 0x00000016, 0x00000001, 0x00050050, 0x00000007, 0x0000001e, 0x0000001a, 0x0000001b, 0x00050050,
    0x00000007, 0x0000001f, 0x0000001c, 0x0000001d, 0x00050050, 0x00000017, 0x00000020, 0x0000001e, 0x0000001f,
    0x0004003d, 0x00000007, 0x00000023, 0x00000022, 0x00050091, 0x00000007, 0x00000024, 0x00000020, 0x00000023,
    0x00050041, 0x00000010, 0x00000026, 0x0000000d, 0x00000025, 0x0004003d, 0x0000000a, 0x00000027, 0x00000026,
    0x0007004f, 0x00000007, 0x00000028, 0x00000027, 0x00000027, 0x00000000, 0x00000001, 0x00050081, 0x00000007,
    0x00000029, 0x00000024, 0x00000028, 0x0003003e, 0x00000009, 0x00000029, 0x0004003d, 0x00000007, 0x0000002b,
    0x00000009, 0x00050041, 0x00000010, 0x0000002d, 0x0000000d, 0x0000002c, 0x0004003d, 0x0000000a, 0x0000002e,
    0x0000002d, 0x0007004f, 0x00000007, 0x0000002f, 0x0000002e, 0x0000002e, 0x00000000, 0x00000001, 0x00050083,
    0x00000007, 0x00000030, 0x0000002b, 0x0000002f, 0x00050041, 0x00000010, 0x00000031, 0x0000000d, 0x0000002c,
    0x0004003d, 0x0000000a, 0x00000032, 0x00000031, 0x0007004f, 0x00000007, 0x00000033, 0x00000032, 0x00000032,
    0x00000002, 0x00000003, 0x00050088, 0x00000007, 0x00000034, 0x00000030, 0x00000033, 0x0003003e, 0x0000002a,
    0x00000034, 0x0004003d, 0x00000039, 0x0000003c, 0x0000003b, 0x00050041, 0x00000010, 0x0000003e, 0x0000000d,
    0x0000003d, 0x0004003d, 0x0000000a, 0x0000003f, 0x0000003e, 0x0007004f, 0x00000007, 0x00000040, 0x0000003f,
    0x0000003f, 0x00000000, 0x00000001, 0x0004003d, 0x00000007, 0x00000041, 0x0000002a, 0x0008000c, 0x00000007,
    0x00000044, 0x00000001, 0x0000002b, 0x00000041, 0x00000042, 0x00000043, 0x00050041, 0x00000010, 0x00000045,
    0x0000000d, 0x0000003d, 0x0004003d, 0x0000000a, 0x00000046, 0x00000045, 0x0007004f, 0x00000007, 0x00000047,
    0x00000046, 0x00000046, 0x00000002, 0x00000003, 0x00050085, 0x00000007, 0x00000048, 0x00000044, 0x00000047,
    0x00050081, 0x00000007, 0x00000049, 0x00000040, 0x00000048, 0x00050057, 0x0000000a, 0x0000004a, 0x0000003c,
    0x00000049, 0x0008004f, 0x00000035, 0x0000004b, 0x0000004a, 0x0000004a, 0x00000000, 0x00000001, 0x00000002,
    0x0003003e, 0x00000037, 0x0000004b, 0x00060041, 0x00000050, 0x00000051, 0x0000000d, 0x00000025, 0x0000004f,
    0x0004003d, 0x00000006, 0x00000052, 0x00000051, 0x0004006e, 0x0000000e, 0x00000053, 0x00000052, 0x0003003e,
    0x0000004d, 0x00000053, 0x0003003e, 0x00000054, 0x0000000f, 0x000200f9, 0x00000055, 0x000200f8, 0x00000055,
    0x000400f6, 0x00000057, 0x00000058, 0x00000000, 0x000200f9, 0x00000059, 0x000200f8, 0x00000059, 0x0004003d,
    0x0000000e, 0x0000005a, 0x00000054, 0x0004003d, 0x0000000e, 0x0000005b, 0x0000004d, 0x000500b1, 0x0000005c,
    0x0000005d, 0x0000005a, 0x0000005b, 0x000400fa, 0x0000005d, 0x00000056, 0x00000057, 0x000200f8, 0x00000056,
    0x0004003d, 0x0000000e, 0x00000065, 0x00000054, 0x00060041, 0x00000066, 0x00000067, 0x00000064, 0x0000000f,
    0x00000065, 0x0004003d, 0x0000000a, 0x00000068, 0x00000067, 0x0003003e, 0x0000005f, 0x00000068, 0x00050041,
    0x00000069, 0x0000006c, 0x00000009, 0x0000006b, 0x0004003d, 0x00000006, 0x0000006d, 0x0000006c, 0x00050041,
    0x00000069, 0x0000006e, 0x0000005f, 0x0000006b, 0x0004003d, 0x00000006, 0x0000006f, 0x0000006e, 0x00050083,
    0x00000006, 0x00000070, 0x0000006d, 0x0000006f, 0x00060041, 0x00000050, 0x00000072, 0x0000000d, 0x00000025,
    0x00000071, 0x0004003d, 0x00000006, 0x00000073, 0x00000072, 0x00050085, 0x00000006, 0x00000074, 0x00000070,
    0x00000073, 0x00050041, 0x00000069, 0x00000076, 0x00000009, 0x00000075, 0x0004003d, 0x00000006, 0x00000077,
    0x00000076, 0x00050041, 0x00000069, 0x00000078, 0x0000005f, 0x00000075, 0x0004003d, 0x00000006, 0x00000079,
    0x00000078, 0x00050083, 0x00000006, 0x0000007a, 0x00000077, 0x00000079, 0x00050050, 0x00000007, 0x0000007b,
    0x00000074, 0x0000007a, 0x0006000c, 0x00000006, 0x0000007c, 0x00000001, 0x00000042, 0x0000007b, 0x0003003e,
    0x0000006a, 0x0000007c, 0x0004003d, 0x00000006, 0x0000007d, 0x0000006a, 0x00050041, 0x00000069, 0x0000007e,
    0x0000005f, 0x0000004f, 0x0004003d, 0x00000006, 0x0000007f, 0x0000007e, 0x000500b8, 0x0000005c, 0x00000080,
    0x0000007d, 0x0000007f, 0x000300f7, 0x00000082, 0x00000000, 0x000400fa, 0x00000080, 0x00000081, 0x00000089,
    0x000200f8, 0x00000081, 0x0004003d, 0x00000035, 0x00000083, 0x00000037, 0x00050041, 0x00000069, 0x00000085,
    0x0000005f, 0x00000071, 0x0004003d, 0x00000006, 0x00000086, 0x00000085, 0x00060050, 0x00000035, 0x00000087,
    0x00000086, 0x00000086, 0x00000086, 0x0008000c, 0x00000035, 0x00000088, 0x00000001, 0x0000002e, 0x00000083,
    0x00000084, 0x00000087, 0x0003003e, 0x00000037, 0x00000088, 0x000200f9, 0x00000082, 0x000200f8, 0x00000089,
    0x0004003d, 0x00000006, 0x0000008a, 0x0000006a, 0x00050041, 0x00000069, 0x0000008b, 0x0000005f, 0x0000004f,
    0x0004003d, 0x00000006, 0x0000008c, 0x0000008b, 0x00050081, 0x00000006, 0x0000008e, 0x0000008c, 0x0000008d,
    0x000500b8, 0x0000005c, 0x0000008f, 0x0000008a, 0x0000008e, 0x000300f7, 0x00000091, 0x00000000, 0x000400fa,
    0x0000008f, 0x00000090, 0x00000091, 0x000200f8, 0x00000090, 0x0004003d, 0x00000035, 0x00000092, 0x00000037,
    0x00050041, 0x00000069, 0x00000093, 0x0000005f, 0x00000071, 0x0004003d, 0x00000006, 0x00000094, 0x00000093,
    0x00050085, 0x00000006, 0x00000096, 0x00000094, 0x00000095, 0x0007000c, 0x00000006, 0x00000097, 0x00000001,
    0x00000025, 0x00000018, 0x00000096, 0x00060050, 0x00000035, 0x00000098, 0x00000097, 0x00000097, 0x00000097,
    0x0008000c, 0x00000035, 0x00000099, 0x00000001, 0x0000002e, 0x00000092, 0x00000084, 0x00000098, 0x0003003e,
    0x00000037, 0x00000099, 0x000200f9, 0x00000091, 0x000200f8, 0x00000091, 0x000200f9, 0x00000082, 0x000200f8,
    0x00000082, 0x000200f9, 0x00000058, 0x000200f8, 0x00000058, 0x0004003d, 0x0000000e, 0x0000009a, 0x00000054,
    0x00050080, 0x0000000e, 0x0000009b, 0x0000009a, 0x00000025, 0x0003003e, 0x00000054, 0x0000009b, 0x000200f9,
    0x00000055, 0x000200f8, 0x00000057, 0x0004003d, 0x00000035, 0x0000009e, 0x00000037, 0x00050051, 0x00000006,
    0x0000009f, 0x0000009e, 0x00000000, 0x00050051, 0x00000006, 0x000000a0, 0x0000009e, 0x00000001, 0x00050051,
    0x00000006, 0x000000a1, 0x0000009e, 0x00000002, 0x00070050, 0x0000000a, 0x000000a2, 0x0000009f, 0x000000a0,
    0x000000a1, 0x00000018, 0x0003003e, 0x0000009d, 0x000000a2, 0x000100fd, 0x00010038,
};
#endif
"""

EDITS = [
    # ------------------------------------------------------------------------------------------------ nvn_present.cpp
    (PRESENT,
     """    0x000000ac, 0x000000ad, 0x00000018, 0x0003003e, 0x000000a9, 0x000000ae, 0x000100fd, 0x00010038,
};
""",
     """    0x000000ac, 0x000000ad, 0x00000018, 0x0003003e, 0x000000a9, 0x000000ae, 0x000100fd, 0x00010038,
};
""" + EXTEND),
    (PRESENT,
     """    VkResult ok = build_pipeline(g.render_pass, kFragment, sizeof kFragment, &g.pipeline) ? VK_SUCCESS : VK_ERROR_UNKNOWN;
""",
     """#ifdef NX_EXTEND_BARS  // AP_ANDROID_PRESENT: bars that repeat the edge of the picture
    VkResult ok = build_pipeline(g.render_pass, kFragmentExtend, sizeof kFragmentExtend, &g.pipeline) ? VK_SUCCESS : VK_ERROR_UNKNOWN;
#else
    VkResult ok = build_pipeline(g.render_pass, kFragment, sizeof kFragment, &g.pipeline) ? VK_SUCCESS : VK_ERROR_UNKNOWN;
#endif
"""),
    (PRESENT,
     """bool present_pass_record(VkCommandBuffer cb, GpuImage& src, const int32_t* crop, uint32_t index) {
""",
     """// AP_ANDROID_PRESENT (apply_android_present.py): NX_ORIGIN_LOWER_LEFT (the default of a game with
// NX_DEFAULT_ORIGIN_LOWER_LEFT, its game.cmake): row 0 of the window texture is the bottom. The blit of nvn_vk.cpp swaps
// its source rows; the present pass samples the crop upside down instead. NX_FLIP_X=0|1 / NX_FLIP_Y=0|1 (config.txt)
// override each axis by hand.
static bool present_flip(const char* name, bool fallback) {
    const char* e = std::getenv(name);
    return e && *e ? e[0] == '1' : fallback;
}
static bool present_origin_lower_left() {
    static const bool on = [] {
        const char* e = std::getenv("NX_ORIGIN_LOWER_LEFT");
        bool d = false;
#ifdef NX_DEFAULT_ORIGIN_LOWER_LEFT
        d = true;
#endif
        return e && *e ? e[0] == '1' : d;
    }();
    return on;
}

bool present_pass_record(VkCommandBuffer cb, GpuImage& src, const int32_t* crop, uint32_t index) {
"""),
    (PRESENT,
     """    p.crop[2] = cw / float(src.width), p.crop[3] = ch / float(src.height);
""",
     """    p.crop[2] = cw / float(src.width), p.crop[3] = ch / float(src.height);
    // AP_ANDROID_PRESENT: the shader reads texture(src, crop.xy + b * crop.zw): a mirrored axis starts at the far edge
    // and runs backwards.
    static const bool flip_y = present_flip("NX_FLIP_Y", present_origin_lower_left());
    static const bool flip_x = present_flip("NX_FLIP_X", false);
    static bool flip_logged = false;
    if (!flip_logged)
        flip_logged = true, log("nvn/vk: present pass: flip x %d, y %d (NX_FLIP_X / NX_FLIP_Y)", int(flip_x), int(flip_y));
    const bool flipped = flip_x || flip_y;
    if (flip_y) p.crop[1] += p.crop[3], p.crop[3] = -p.crop[3];
    if (flip_x) p.crop[0] += p.crop[2], p.crop[2] = -p.crop[2];
"""),
    (PRESENT,
     """    if (g.easu && g.rcas && r == 0 && out_w > cw * 1.01f && out_h > ch * 1.01f && fsr_inter(out_w, out_h)) {
""",
     """    if (g.easu && g.rcas && r == 0 && !flipped /* AP_ANDROID_PRESENT */ && out_w > cw * 1.01f && out_h > ch * 1.01f &&
        fsr_inter(out_w, out_h)) {
"""),

    # ------------------------------------------------------------------------------------------------ nvn_vk.cpp
    (VK,
     """        log("nvn/vk: new window: surface and swapchain re-made");
        if (!make_surface()) return;
""",
     """        log("nvn/vk: new window: surface and swapchain re-made");
#ifdef __ANDROID__  // AP_ANDROID_PRESENT (apply_android_present.py): keep the sRGB format of the old surface
        const VkFormat kept_format = g_sc_format;
#endif
        if (!make_surface()) return;
#ifdef __ANDROID__
        // make_surface went back to a UNORM format; the sRGB choice of apply_srgb_present.py runs once per run, so
        // the sRGB format it chose for the first window is put back if the new surface offers it.
        if (kept_format != g_sc_format) {
            uint32_t nf = 0;
            p_vkGetPhysicalDeviceSurfaceFormatsKHR(g_physical, g_surface, &nf, nullptr);
            std::vector<VkSurfaceFormatKHR> fs(nf);
            p_vkGetPhysicalDeviceSurfaceFormatsKHR(g_physical, g_surface, &nf, fs.data());
            for (const VkSurfaceFormatKHR& f : fs)
                if (f.format == kept_format) {
                    g_sc_format = kept_format;
                    log("nvn/vk: new window: swapchain format %d kept (sRGB)", int(kept_format));
                    break;
                }
        }
#endif
"""),

    # ------------------------------------------------------------------------------------------------ recomp $Markers
    (RECO,
     """    @('both', 'framework\\runtime\\src\\nvn\\nvn_frames.cpp', 'AP_GPU_HANG', 'apply_gpu_hang.py', $false),
""",
     """    @('both', 'framework\\runtime\\src\\nvn\\nvn_frames.cpp', 'AP_GPU_HANG', 'apply_gpu_hang.py', $false),
    @('both', 'framework\\runtime\\src\\nvn\\nvn_present.cpp', 'AP_ANDROID_PRESENT', 'apply_android_present.py', $false),
    @('both', 'framework\\runtime\\src\\nvn\\nvn_vk.cpp', 'AP_ANDROID_PRESENT', 'apply_android_present.py', $false),
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
