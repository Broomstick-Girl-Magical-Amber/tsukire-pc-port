# Android

The Android version of the port: the same runtime as on Windows, built for arm64-v8a, plus a small app.

- **The app (APK)** has nothing of the game inside: a home screen (`java/.../Home.java`, portrait) and a tiny loader
  (`boot/boot.c` -> `libtsuki_boot.so`). One app for both editions. This is what can be published.
- **The game's library** (`lib<game>.so`: the runtime + the code recompiled from your dump) is built on your PC and
  travels in the port folder you copy to the phone (`data/<edition>/android/`). The app copies it to its own files
  folder, from where Android allows loading it. It is derived from your dump: never publish it.

What is here:

- `java/io/github/tsukire/launcher/Home.java`: the home screen. It asks for "all files access", lets you choose the port
  folder and the dump with Android's own file picker, checks the data, installs the game's library and starts the game.
- `boot/boot.c`: the library of the game's activity; it loads the game's library and hands over to it.
- `AndroidManifest.xml.in`: the manifest template (`tools/android/build_apk.ps1` fills in package, name and version).
- `res/`: the theme of the game screen. The app icon is the one of the exe and the launcher
  (`framework/games/tsukihime_common/tsukihime.ico`), put in the APK by `build_apk.ps1`.

How to build today (Windows; on Linux the `.sh` next to each script takes the same options):

1. Recompile the edition as usual (`tools\recomp_tsukihime.ps1 -Edition global`, or the launcher).
2. Android Studio with, in the SDK Manager: an NDK (r27 or newer), Build-Tools, Platform-Tools, an SDK Platform of
   API 30 or newer.
3. `tools\android\android_deps.ps1` once: FFmpeg, opus, libjpeg-turbo, libpng and zlib for arm64-android (vcpkg).
4. `tools\android\build_apk.ps1 -Edition global [-Install]`: `_android\tsukihime.apk` and
   `_android\global\libtsukihime_global.so`.
5. `tools\android\phone_data.ps1 -Edition global [-Mods] [-Saves] [-InternalStorage | -ExternalStorage]`: the port folder
   for the phone in `_android\global\phone` (the game's library, analysis, romfs_source.bin, spirv, fonts, hud...; no
   dump). Copy its contents to a folder of the phone and put the `.xci`/`.nsp` in it (or choose the dump in the app).
6. In the app: grant the access, choose the folder, Play.

Without Android Studio or the NDK (end users; being tested): `tools\android\build_game.ps1 -Edition global` replaces
steps 2-4. It compiles the recompiled code with the LLVM clang of the PC install and links it with the prebuilt runtime
of the "Android pack" (`_prepare\android-pack`; from `-Pack <zip>` or downloaded as pinned in
`tools\internal\android_pack.json`). The maintainer makes that pack with `tools\dev\build_android_pack.ps1` after a
normal NDK build (`build_apk.ps1 -Edition`), and publishes it with the APK.

On the phone the folder has the same layout as the port on the PC: `data/<tsuki|tsuki_global>` is the data root (saves in
`user/`, `config.txt`, `language.txt`, `title.ini`, `nx.log`), the dump sits in the folder itself and mods in
`mods/<edition>`. A dump elsewhere is written by the app to `config.txt` as `NX_ROMFS_DUMP`.
