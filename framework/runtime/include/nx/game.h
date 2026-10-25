// What the shared runtime asks of the game it is built for. Each game implements this in games/<game>/game.cpp
// (CMake: -DNX_GAME=<game>); everything that only makes sense for one game lives there.
#pragma once

#include <cstdint>

namespace nx::game {

struct Info {
    const char* id;               // lowercase: CMake target, library, executable, log tag ("hades")
    const char* title;            // for the player: window title, Android data folder ("Hades")
    uint64_t program_id;          // svcGetInfo(ProgramId)
    const char* android_package;  // the APK's package (games/<game>/AndroidManifest.xml)
    const char* pc_data_root;     // PC: where analysis/ and extract/ are ("private/<game>")
    // main.npdm META +0x14. Non-zero enables nn::os virtual address memory (VammManager, SDK 16) over the alias
    // region; svcGetInfo(SystemResourceSizeTotal) reports it.
    uint64_t system_resource_size = 0;
    // NVN driver's format table in sdk (nvn_texture_layout.cpp): its offset and entry count depend on the SDK
    // version. 0 = the Super Mario Bros. Wonder values (SDK 16.2: 0xd34198, 0xd0 entries).
    uint64_t nvn_format_table = 0;
    uint32_t nvn_format_count = 0;
    // AudioRenderSystem's execution mode field (hle_audio.cpp); the rendering device is the next word.
    // 0 = the Super Mario Bros. Wonder value (SDK 16.2: 0x2d0). SDK 11.4: 0x2b8.
    uint32_t audio_execution_mode_offset = 0;
};
extern const Info kInfo;

// After the modules' static constructors, right before nnMain: patch globals, add virtual files.
void after_init();
// The game starts its renderer (vkCreateInstance): settings the engine may have reset while loading its config.
void renderer_start();
// The first frame was presented.
void first_frame();

}  // namespace nx::game
