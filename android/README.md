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

For players (Windows; on Linux the `.sh` next to each script takes the same options), no Android Studio, no NDK:

1. Recompile the edition as usual (`tools\recomp_tsukihime.ps1 -Edition global`, or the launcher).
2. `tools\android\android_port.ps1 -Edition global [-Mods] [-InternalStorage | -ExternalStorage] [-InstallApp] [-GetAdb]`:
   builds the game's library (`build_game.ps1`: the recompiled code compiled with the LLVM clang of the PC install and
   linked with the prebuilt runtime of the "Android pack", `_prepare\android-pack` or downloaded as pinned in
   `tools\internal\android_pack.json`), puts the folder for the phone together in `_android\global\phone`
   (`phone_data.ps1`: the game's library, analysis, romfs_source.bin, spirv, fonts, hud...; no dump) and, with
   `-InternalStorage`/`-ExternalStorage`, sends it to the phone by USB with adb (only what changed, with its progress;
   `-GetAdb` downloads adb if the PC has none) and with `-InstallApp` installs or updates the app.
   The launcher does the same: "Install and recompile" tab, "Android" sub-tab (after recompiling in "Windows").
3. Without sending: copy the contents of `_android\global\phone` to a folder of the phone and put the `.xci`/`.nsp` in it
   (or choose the dump in the app).
4. In the app: grant the access, choose the folder, Play. The game needs a GPU with Vulkan 1.3: on a phone that declares
   less, the app warns before Play, and after a stop at Vulkan it says why.

For the maintainer (the APK and the Android pack): Android Studio with an NDK (r27 or newer), Build-Tools, Platform-Tools
and an SDK Platform of API 30 or newer; `tools\android\android_deps.ps1` once (FFmpeg, opus, libjpeg-turbo, libpng and
zlib for arm64-android, vcpkg); `tools\android\build_apk.ps1 -Edition global` (`_android\tsukihime.apk` and the NDK
build of the game's library); then `tools\dev\build_android_pack.ps1 -Version N` makes the pack. Both go to the release,
with their URLs and SHA-256 in `tools\internal\android_pack.json`.

On the phone the folder has the same layout as the port on the PC: `data/<tsuki|tsuki_global>` is the data root (saves in
`user/`, `config.txt`, `language.txt`, `title.ini`, `nx.log`), the dump sits in the folder itself and mods in
`mods/<edition>`. A dump elsewhere is written by the app to `config.txt` as `NX_ROMFS_DUMP`.
