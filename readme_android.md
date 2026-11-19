# tsukire-pc-port on Android

The Android version of the port. The game's code is built on your PC from your own dump, and the phone gets a small app plus a folder with the data.

## Requirements

- The edition you want, **already recompiled on your PC** (see [Rebuild on Windows](readme.md#rebuild-on-windows) or [Rebuild on Linux](readme.md#rebuild-on-linux-and-play-with-proton)). Android uses that same recompilation.
- A phone with **Android 11 or newer**, an arm64 CPU and a GPU with **Vulkan 1.1** or newer.
- The space required on the phone is the dump (about 21–23 GB), plus about **85 MB** for the port folder.
- To send by USB (optional): **Enable USB debugging** on the phone (Settings > About phone > Software information > tap *Build number* 7 times; then Developer options > USB debugging). If the PC has no `adb`, Google's Platform-Tools are downloaded to `%LOCALAPPDATA%\tsuki-tools`.

## Install with the launcher

1. **Recompile the edition** in Install and recompile > *Windows*, as usual.

2. **Connect the phone** by USB with USB debugging on, and accept the prompt on the phone. Skip this step if you prefer to copy the files by hand.
3. **Prepare it for Android** in Install and recompile > *Android*:

   - Tick the edition (it must say "ready to prepare" or "library built ...").
   - In *Send*, choose **By USB to the phone's internal storage**, **By USB to the phone's SD card**, or **Do not send (copy the folder by hand)**.
   - Press **Prepare for Android**.

   It downloads the Android pack and `adb` if they are missing, builds the game's library, sends the port folder to `Tsukihime` on the phone (only what changed) and installs or updates the app.

4. **Without sending**, *Phone folder* opens `_android\<edition>\phone`. Copy its **contents** to a folder of the phone (for example `Internal storage\Tsukihime`), put the `.xci` / `.nsp` in that folder and install `tsukihime.apk` from Releases by hand.

5. **On the phone**, open *Tsukihime*:

   - *Grant access* (all files access; the game reads the folder and the dump by path).
   - *Choose port folder* and pick the folder from step 3 or 4. If the dump is somewhere else, *Choose dump (.xci / .nsp)*.
   - Press **Play**.

Later updates only need step 3 again: it sends only what changed and updates the app.

## Install with scripts

The launcher runs `tools\android\android_port.ps1`. The same can be done from a console, from the repository root.

The script `android_port` sends the port folder to the phone with `-InternalStorage` or `-ExternalStorage`. With neither, nothing is sent. 

The folder is left in `_android/<edition>/phone`. Copy its **contents** to a folder of the phone, put the dump in it and install `tsukihime.apk` from Releases.


**Windows**

```powershell
# Recompile first, if you have not yet.
powershell -ExecutionPolicy Bypass -File .\tools\recomp_tsukihime.ps1 -Edition global

# Build the game's library, send the folder by USB and install the app.
powershell -ExecutionPolicy Bypass -File .\tools\android\android_port.ps1 -Edition global -Mods -InternalStorage -InstallApp -GetAdb
```

**Linux**

The `.sh` next to each script takes the same options. Call them with `bash`.

```bash
bash tools/linux/recomp_linux.sh -Edition global
bash tools/android/android_port.sh -Edition global -Mods -InternalStorage -InstallApp -GetAdb
```

After completing these steps, then follow step 5 of [the launcher](#install-with-the-launcher-windows).