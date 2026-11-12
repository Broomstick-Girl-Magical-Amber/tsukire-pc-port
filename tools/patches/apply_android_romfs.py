"""apply_android_romfs.py - reading the dump on Android with the ARMv8 AES instructions (AP_ANDROID_ROMFS).

Phase 1 of the Android port. romfs_direct.cpp (AP_ROMFS_DIRECT) reads every file of the
game straight from the .xci/.nsp and decrypts it with AES-128-CTR: AES-NI on x86-64, plain C elsewhere. On a phone the
plain C path is several times slower (videos of 300 MB, the music, every script read). The Android build already
compiles with -march=armv8.2-a+crypto (CMakeLists.txt: the recompiled code needs it), so the AES instructions
(AESE/AESMC) are always there: this patch adds the same 8-blocks-at-a-time CTR with them (ctr_blocks_ce), used when
__ARM_FEATURE_AES (or __ARM_FEATURE_CRYPTO) is defined. NX_ROMFS_NO_AESNI=1 still forces the plain C path (tests).
The log line says which one is used: "AES-NI", "AES-ARMv8" or "AES-C".

The data and dump paths need no change: on the phone the chosen folder has the same layout as the port on the PC
(<folder>/data/<edition> is the data root, the dump in <folder>, mods in <folder>/mods/<edition>), and a dump elsewhere
is given with NX_ROMFS_DUMP in config.txt (the Android launcher writes it).

Idempotent (marker AP_ANDROID_ROMFS), all or nothing, keeps CRLF and BOM, --remove leaves the files byte for byte as
they were. Independent of the other AP_ANDROID_* patchers and of apply_mod_recipes.py. Also edits
../tools/recomp_tsukihime.ps1 (a $Markers row, anchored after the row of apply_mod_recipes.py for romfs_direct.cpp).
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_android_romfs.py
"""
from _patch import run

MARK = "AP_ANDROID_ROMFS"
RD = "games/tsukihime_common/romfs_direct.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (RD,
     '''namespace fs = std::filesystem;
''',
     '''// AP_ANDROID_ROMFS (apply_android_romfs.py): ARMv8 AES instructions (the Android build has -march=armv8.2-a+crypto)
#if defined(__aarch64__) && (defined(__ARM_FEATURE_AES) || defined(__ARM_FEATURE_CRYPTO))
#define TSUKI_ROMFS_ARM 1
#include <arm_neon.h>
#endif

namespace fs = std::filesystem;
'''),
    (RD,
     '''void ctr_blocks_c(const uint8_t rk[176], const uint8_t upper[8], uint64_t first_block, uint8_t* data, size_t blocks) {
''',
     '''#ifdef TSUKI_ROMFS_ARM
// AP_ANDROID_ROMFS: as ctr_blocks_ni with AESE (AddRoundKey + SubBytes + ShiftRows) and AESMC (MixColumns): rounds 1-9
// are AESE(k[r-1]) + AESMC, round 10 is AESE(k[9]) and the last key is XORed.
void ctr_blocks_ce(const uint8_t rk[176], const uint8_t upper[8], uint64_t first_block, uint8_t* data, size_t blocks) {
    uint8x16_t k[11];
    for (int i = 0; i < 11; i++) k[i] = vld1q_u8(rk + 16 * i);
    uint8_t ctr[8 * 16];
    size_t b = 0;
    while (b < blocks) {
        const size_t n = std::min<size_t>(8, blocks - b);
        uint8x16_t x[8];
        for (size_t j = 0; j < n; j++) {
            std::memcpy(ctr + 16 * j, upper, 8);
            put_be64(ctr + 16 * j + 8, first_block + b + j);
            x[j] = vld1q_u8(ctr + 16 * j);
        }
        for (int r = 0; r < 9; r++)
            for (size_t j = 0; j < n; j++) x[j] = vaesmcq_u8(vaeseq_u8(x[j], k[r]));
        for (size_t j = 0; j < n; j++) {
            x[j] = veorq_u8(vaeseq_u8(x[j], k[9]), k[10]);
            uint8_t* p = data + 16 * (b + j);
            vst1q_u8(p, veorq_u8(vld1q_u8(p), x[j]));
        }
        b += n;
    }
}

bool use_arm_aes() {
    static const bool on = [] {
        const char* e = std::getenv("NX_ROMFS_NO_AESNI");  // tests: force the plain C path
        return !(e && *e && *e != '0');
    }();
    return on;
}
#endif

void ctr_blocks_c(const uint8_t rk[176], const uint8_t upper[8], uint64_t first_block, uint8_t* data, size_t blocks) {
'''),
    (RD,
     '''#endif
    ctr_blocks_c(rk, upper, first_block, data, blocks);
}
''',
     '''#endif
#ifdef TSUKI_ROMFS_ARM  // AP_ANDROID_ROMFS
    if (use_arm_aes()) {
        ctr_blocks_ce(rk, upper, first_block, data, blocks);
        return;
    }
#endif
    ctr_blocks_c(rk, upper, first_block, data, blocks);
}

// AP_ANDROID_ROMFS: which AES the reads use, for the log
const char* aes_kind() {
#ifdef TSUKI_ROMFS_ARM
    if (use_arm_aes()) return "ARMv8";
#endif
    return use_aesni() ? "NI" : "C";
}
'''),
    (RD,
     '''                (unsigned long long)s.program_id, s.files.size(), use_aesni() ? "NI" : "C");
''',
     '''                (unsigned long long)s.program_id, s.files.size(), aes_kind());  // AP_ANDROID_ROMFS
'''),

    # ------------------------------------------------------------------------------------------------ recomp $Markers
    (RECO,
     '''    @('both', 'framework\\games\\tsukihime_common\\romfs_direct.cpp', 'AP_MOD_RECIPES', 'apply_mod_recipes.py', $false),
''',
     '''    @('both', 'framework\\games\\tsukihime_common\\romfs_direct.cpp', 'AP_MOD_RECIPES', 'apply_mod_recipes.py', $false),
    @('both', 'framework\\games\\tsukihime_common\\romfs_direct.cpp', 'AP_ANDROID_ROMFS', 'apply_android_romfs.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
