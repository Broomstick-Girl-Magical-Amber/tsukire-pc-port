# Wonder Android launcher: options

The launcher (`android/wonder/`, package `io.github.belmantegu.wonderrecomp`) is ported from the Castlevania one and
drawn as in `android/launcher-mockup/index.html`. Options are stored in two files, and the runtime applies the **first
line** of a key (`platform_android.cpp` sets environment variables without overriding), so the launcher rewrites that
line and drops later duplicates (`GameConfig`):

- `config.txt` in the data folder (`Android/data/io.github.belmantegu.wonderrecomp/files/`): `NX_*` lines become
  environment variables before the game boots; plain keys (`render_scale`, `trace`) are read with `plat::setting()`.
- `app.txt` (app-internal): language, FPS counter, advanced rows. Only the launcher reads it.

A row left at the runtime's own default has no line, so the runtime's defaults keep applying. Rows marked *explicit* are
always written. A row the runtime does not read is not shown: no dead switches. Every option below that changes the game
restarts it (the game runs in its own process, `:game`, and reads `config.txt` once at start).

## Rows

| Row | Stored as | Reader in the runtime | Default (no line) | Status |
|---|---|---|---|---|
| Console mode (handheld / TV) | `NX_HANDHELD=1` / `0` (always written) | `svc_am.cpp`, `svc_misc.cpp` (tests that the variable exists); `platform_android.cpp` now removes it when it is `0` | handheld 720p | live; the `0` case is a 6-line runtime change |
| Internal resolution | `NX_RENDER_RES=WxH` (960x540 .. 1920x1080, 16:9) | `svc_am.cpp` GetDefaultDisplayResolution | the console mode's own size | live |
| Window scale | `render_scale=0.5..1` | `platform_android.cpp` `setting("render_scale")` | by GPU (1 on Adreno 7xx+) | live |
| FSR 1 / sharpness | `NX_FSR=1`, `NX_FSR_SHARP=0..2` | `nvn_present.cpp` | off / 0.2 | live |
| FPS limit (60 / 30) | `NX_FPS_CAP=30` (no line = the game's own rule) | `nvn_queue.cpp` `frame_time_probe` (forces interval 2 and the game's interval fields, governors off), `perf_hint.cpp` (ADPF target 33.3 ms) | the game's rule: 60, 30 when heavy | **code written, built into the APK, NOT yet run on the phone** (game speed at 30 not verified) |
| GPU submits in flight | `NX_PIPELINED=2..4` | `nvn_frames.cpp` | **synchronous** (see below) | live, tagged experimental |
| GPU turbo | `NX_GPU_TURBO=1` | `perf_hint.cpp` | off | live |
| Memory limit | `NX_MEM_LIMIT_GB` | `memguard_android.cpp` | 60% of RAM | live |
| CPU cores (advanced) | `NX_AFFINITY=1`/`2` | `frame_stats.cpp` (modes 0, 1, 2) | 0, the scheduler decides | live |
| Draws per submitted part (advanced) | `NX_SUBMIT_CHUNK_DRAWS=0`/`400` | `nvn_frames.cpp` | 150 on Android | live |
| Pipeline cache (advanced) | `NX_PIPELINE_CACHE=0` when off | `nvn_pipeline_cache.cpp` | on | live |
| On-screen buttons | `NX_TOUCH_SHOW=0` when off | `touch_layout.cpp` `touch_enabled()`, `platform_android_input.cpp` | on | **ported** (new, small) |
| Button opacity / size | `NX_TOUCH_OPACITY` 10..100 (85 = the old look), `NX_TOUCH_SIZE` 50..160 | `platform_android_input.cpp` overlay alpha, `touch_layout.cpp` radius | 85 / 100 | **ported** |
| Edit buttons, mirror, reset | `NX_TOUCH_LAYOUT=A:x,y,r;B:...`, `NX_TOUCH_MIRROR=1` | `touch_layout.cpp` `wonder_buttons()`, stick side in `platform_android_input.cpp` | the original layout | **ported**; the editor is `LayoutEditorActivity` |
| Mute | `NX_MUTE=1` | `platform_android_audio.cpp` `audio_open` (same rule as Windows/macOS) | off | **ported** (2 lines) |
| Trace system calls (advanced) | `trace=1` | `main.cpp` `setting("trace")` | off | live |
| FPS counter, position | `app.txt` `fps_mode`, `fps_pos` | `FpsOverlay` over `GameActivity.nativeFrames()` (frames presented, `nx::g_frame`) | off | live (one JNI function in `platform_android.cpp`) |
| Language, advanced rows | `app.txt` | the launcher | auto / off | live |
| Data: state, space, import, wipe, clear cache | none | files under the data folder (`pipelines_*.bin`, `analysis`, `extract`, `spirv`) | | live |
| Saves export / import | none | zip of `user/save/` (`hle_fs.cpp` mount) | | live |
| Logs, save diagnostics, This device | none | `nx.log`, `nx.log.prev` (`loader.cpp`), `Diagnostics` | | live |

### Hidden rows of the mockup (marked PORTAR there)

| Row | Why it is not offered |
|---|---|
| FXAA | Wonder has no FXAA pass (`nvn_present.cpp` only has FSR). Porting Castlevania's pass is a new shader and a new pipeline in the present path: not small, and not testable without a device. |
| Frame limit as a plain sleep | A sleep-only cap would slow the game's logic; the shipped `FPS limit` row forces the game's own interval 2 instead. |
| Full screen Hor+ (`widescreen`), display size (`display_res`) | Wonder renders the game's own 16:9 (the present pass adds bars). There is no Hor+ in the runtime. |
| Vibration, floating / fixed stick | The native stick already appears where the thumb lands. Haptics would need a JNI vibrator call from the input thread. |
| Game volume, audio latency | `platform_android_audio.cpp` opens AAudio with fixed settings and has no gain. |
| Button art / circles | The runtime always draws plain circles; there is no art to choose. |
| Driver GPU tab (custom Turnip, download, test) | Castlevania ships hook libraries from another APK (`libhook_impl.so` ...) for this. Wonder's runtime loads `libvulkan.so` directly. "This device" (Diagnostics tab) shows the GPU and Vulkan facts instead. |
| Send report | There is no report server for Wonder. "Save diagnostics" writes a zip you can share. |

### Differences from the mockup's defaults (checked against the runtime)

- **GPU submits in flight**: the mockup said `2 (default)`. The runtime default on Android is synchronous (`pipelined()` is
  only on by default on macOS; commit dc3d5fc found exploded meshes with pipelining, DEBUG-2 #76). The row keeps
  synchronous as the default and tags 2/3/4 as experimental.
- **CPU cores**: the mockup said `3 (default)`. `frame_stats.cpp` knows modes 0, 1 and 2 (3 behaves as 1). Default is 0.
- **Internal resolution** is 16:9 (the game's own aspect), not "the phone's aspect".

## Import

`LauncherActivity` asks for a folder (SAF, no storage permission) holding `prod.keys` (+ `title.keys`) and the dump. It passes
`.xci` (and split parts `.xc0`, `.xci.00` ...), `.nsp`, `prod.keys`, `title.keys`, `keys.txt` to `NativeImporter.run` with
title `010015100B514000` (Super Mario Bros. Wonder 1.0.0, `games/wonder/game.cpp`). The recompiled code is for **1.0.0**: the
launcher tells the player to put only the 1.0.0 dump there (the importer applies an update it finds in the folder, which would
change `main` and break the recompiled code).

The importer writes `analysis/*.bin`, `analysis/main.npdm` and `extract/romfs/`. It does not write `spirv/`: the translated
shaders come from the APK (`assets/spirv.zip`, made by `android/build_apk.sh` from `private/wonder/spirv`, which needs the
player's own dump and `tools/maxwell/spirv_harness`), and `config.txt` is created on the first import or Play. The button
font (`fonts/shared_5.ttf`) is not produced by the importer: the launcher installs it (see Fixes below).
A "data .zip" made by `tools/pack_phone_wonder.py` is also accepted.

## Vulkan 1.3 requirement

`android/wonder/AndroidManifest.xml` requires Vulkan 1.3 (`0x403000`). Reading `runtime/src/nvn/`: the layer asks for API 1.3 and
chains `VkPhysicalDeviceVulkan13Features`, uses dynamic rendering (`vkCmdBeginRendering`, `nvn_draw.cpp`), **legacy**
`vkCmdPipelineBarrier` (no synchronization2) and **binary** semaphores plus fences (no timeline semaphores). Dynamic rendering is
the only 1.3 feature actually needed. A device on Vulkan 1.1/1.2 would need: `VK_KHR_dynamic_rendering` enabled and the KHR
entry points used (or `NX_RENDER_PASS_OBJECTS=1` made the default: that path exists in `nvn_renderpass.cpp`, not measured),
the 1.3 features struct chained only on 1.3, and a check of the translated shaders' SPIR-V version and capabilities. Effort:
about a day of code plus testing on a real 1.1/1.2 phone, which nobody here has run. The target (Adreno 740) is 1.3, the gain is
only devices that are unlikely to run the game fast enough, so the manifest and runtime are left at 1.3.

## Fixes

- **Controller-button icons shown as "??".** The game writes the buttons in its text ("Press [L] + [R] to Start") as codepoints of the
  Switch's extension font, which lives in the firmware and is not ours to ship. Without `fonts/shared_5.ttf` the runtime fell back to
  Roboto (`hle_pl.cpp`), which has no such glyphs. The launcher now installs a stand-in: `tools/fonts/make_button_font.py` draws 39
  button icons (A B X Y L R ZL ZR SL SR + - D-pad, stick, arrows) on top of Fredoka (SIL OFL, so it can be bundled; 56 KB), and
  `AppFiles.installSharedFont` copies it from `assets/fonts/shared_5.ttf` to `<data root>/fonts/shared_5.ttf` on every launcher
  start when the file is missing. It never replaces a font that is already there, so a font put in by hand wins. Checked on the
  S23: the title shows "Press (L) + (R) to Start" with the icons. The earlier macOS-Arial based font is only for local use and is not
  in the APK.
- **Closing at once on a POCO F6** (Android 15, Adreno 735): the HLE thunk range at `0x6F00000000` was already mapped and its
  reservation was fatal (`FATAL: cannot reserve thunks ...`). It only names HLE functions, so `memory.cpp` now logs a warning and goes on.
- **Console mode.** `NX_HANDHELD=0` now really gives docked 1080p (before, the variable existing at all meant handheld).
- **Mute** (`NX_MUTE`) and the **touch layout** keys (`NX_TOUCH_*`) now have readers in the runtime; rows without a reader are hidden.
- **Launcher layout:** the 7 tabs fit the screen and the rows list fades at its edge so a row cut by the panel reads as "scroll".
- **FPS limit** (`NX_FPS_CAP`): see the rows table; 60 keeps the game's own rule, 30 holds the 30 fps cadence.

## Measured on a Galaxy S23 (SM-S911B, Adreno 740, Android 14)

Handheld mode (the game renders 1280x720, the present pass draws it into the 2340x1080 swapchain), 60 Hz panel, phone plugged in.
Numbers are the runtime's own "per frame over 60" lines (`nx.log`), steady state of each run, **one run per row**.

| Scene | Setting | fps | replay (CPU) | fence wait (GPU) | present | upload / frame |
|---|---|---|---|---|---|---|
| Title, user select, menus | default | 60 | ~6 ms | ~4 ms | ~4.4 ms | ~3.5 MB |
| "Welcome to the Flower Kingdom" (fountain) | default (synchronous) | 24 | 20.6 ms | 12.7 ms | 5.5 ms | 10.4 MB |
| same | `NX_PIPELINED=2` | **60** | 9.7 ms | 1.3 ms | 0.3 ms | 10.1 MB |
| same | `NX_PIPELINED=3` | 60 | 10.2 ms | 0.9 ms | 0.4 ms | 10.0 MB |

What the numbers say: in synchronous mode the CPU replay and the GPU wait are serialized (about 21 + 13 + 5 ms per frame), so the
game drops to its own 30 fps mode (the log shows `window present interval 1 -> 2`). Pipelining overlaps them and the GPU is not
the limit (the fence wait falls to about 1 ms): the replay thread is the CPU-side limit and pipelining hides it. Memory stays
around 1.1 GB resident (peak 1.17 GB, limit 4.1 GB). Entering the fountain scene creates about 130 pipelines in one second
(~41 ms hitch; the on-disk pipeline cache makes the next visit cheaper). Upload per frame goes from ~3.5 MB to ~10 MB in that scene;
what is uploaded was not investigated.

### Recommended settings for this phone

| Option | Value | Why |
|---|---|---|
| GPU submits in flight (Performance) | **2** (restart) | the only lever measured: 24 to 60 fps on the fountain scene |
| FPS limit (Performance) | 60 (the game's own rule) | 60 when the phone keeps up, its own 30 when heavy; 30 locks the 30 fps cadence (not measured on a phone) |
| Console mode (Image) | Handheld (720p) | lighter than Docked (1080p), which was not measured |
| FSR, GPU turbo | off | no gain measured; turbo only runs hotter |
| Memory limit | automatic | the game uses ~1.1 GB |

Caveats: a single scene, a single run per row. Pipelined mode is still off by default because it once drew exploded meshes in
some courses (DEBUG-2 #76; the docs list the cause, #90, as fixed): watch other courses after turning it on, and go back to
synchronous if the geometry breaks. Back-to-back runs without a cooldown took the AP sensor from 39.7 to 52 C with thermal status 0.

## Other phones

A POCO F6 (Xiaomi 24069PC21G, Android 15, Adreno 735, 11 GB) imported the game fine and then closed as soon as it started:
`FATAL: cannot reserve thunks at 6f00000000 (+100000): the range is in use` (`wonder-diagnostics.zip`, Settings > Diagnostics). The
HLE thunk range only names HLE functions (`loader.cpp` dispatches by address and nothing is ever mapped there), so a host mapping
already in it is harmless: `memory.cpp` now logs a warning instead of stopping. The fix is the one the Castlevania runtime already
had for the same phone; it has not been run on that phone yet.

## Not verified

Run on the S23: the launcher UI, the XCI import (19,012 RomFS files, 3.73 GB), the game starting, the title, the menus, the intro
and the fountain scene (the controller-button glyphs show with the stand-in font), and the benchmark above.

Not run on any device: the 30 fps limit (the code forces the present interval to 2 and the game's own interval fields; game speed
at 30 was not timed against real time), Console mode `NX_HANDHELD=0` (docked), FSR, the window scale and resolution rows, chunk
draws, the pipeline cache cold against warm, mute, the FPS counter overlay, the layout editor, the saves import/export, the zip
import, and course 1-1 (a script could not get past the fountain dialog). The POCO F6 fix has not been run on that phone.
The launcher compiles (javac/d8), the APK passes `apksigner verify` and `aapt2 dump badging`, `OptionsModel`/`GameConfig`/
`ControlLayout` have a plain-JVM test of the file rules, and the runtime edits pass `clang -fsyntax-only` with the NDK.
