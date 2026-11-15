"""apply_bc_staging.py - BC textures decoded on the CPU go through a staging buffer of their own (AP_BC_STAGING).

On a GPU without BC (textureCompressionBC = 0: Mali, some Vulkan 1.1 phones) the runtime decodes BC1-5/BC7 to
uncompressed texels on the CPU (nvn_bc.cpp) and copies them to the image. That branch of upload_texture
(nvn_upload.cpp, `if (decode)`) always took the per-submit upload ring. A scene load decodes hundreds of MB in one
submit (the title: ~350 MB against a 128 MB ring), so the ring wrapped over its own data before the GPU had copied it
("nvn/vk: upload buffer full (... bytes in this submit)", ~1000 lines at the title load): textures uploaded earlier in
that submit (HUD text, the dialog window, effects) kept other textures' bytes for the whole session.

The other big uploads already avoid this (DEBUG-2 #72, PHASE4.md U1): 1 MB and up get a staging buffer of their own
when staging_wanted() says so (past a quarter of the ring in this submit, or one upload over an eighth of it).
This patch gives the decoded BC uploads the same rule, with the same switch (NX_STAGING_RING=1: the ring, as before).
GPUs with BC never enter that branch: NVIDIA, AMD, Intel and Adreno are not affected.
What to see in nx.log: no "upload buffer full" at the title load, and "nvn/vk: staging: N buffers, X MB over ..." lines.

Edits: runtime/src/nvn/nvn_upload.cpp (one line, the allocation of the decode branch; apply_title_click.py inserts
before the `if (decode)` line, a different line, so the two apply and remove in any order) and
../tools/recomp_tsukihime.ps1 (its $Markers row, after the tsukihime_common.cmake row of apply_packaged_layout.py,
which no other patcher uses as an anchor).
Idempotent (marker AP_BC_STAGING), all or nothing, keeps CRLF and BOM, refuses if an anchor is missing;
--remove leaves the files byte for byte as they were.

Usage (in <root>):
  python tools\\patches\\apply_bc_staging.py            then rebuild (PC: recomp_tsukihime.ps1 -Edition <ed> -From 9;
                                                         phone: tools\\android\\build_apk.ps1 -Edition <ed>)
  python tools\\patches\\apply_bc_staging.py --remove
"""
from _patch import run

MARK = "AP_BC_STAGING"

UPLOAD = "runtime/src/nvn/nvn_upload.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (UPLOAD,
     """        Upload u = upload_alloc(total, 16);
""",
     """        // AP_BC_STAGING: big decodes get a staging buffer of their own, as the uncompressed uploads below: a scene load
        // decodes hundreds of MB in one submit and the ring wrapped over textures the GPU had not copied yet.
        Upload u = total >= (1u << 20) && !std::getenv("NX_STAGING_RING") && staging_wanted(total)
                       ? staging_alloc(total)
                       : upload_alloc(total, 16);
"""),

    (RECO,
     """    @('both', 'framework\\games\\tsukihime_common\\tsukihime_common.cmake', 'AP_PACKAGED_LAYOUT', 'apply_packaged_layout.py', $false),
""",
     """    @('both', 'framework\\games\\tsukihime_common\\tsukihime_common.cmake', 'AP_PACKAGED_LAYOUT', 'apply_packaged_layout.py', $false),
    @('both', 'framework\\runtime\\src\\nvn\\nvn_upload.cpp', 'AP_BC_STAGING', 'apply_bc_staging.py', $false),
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)