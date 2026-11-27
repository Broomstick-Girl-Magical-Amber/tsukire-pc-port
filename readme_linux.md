# tsukire-pc-port Linux

The native Linux build of the port, built on your PC from your own dump.

> **Use with launcher.** Download `tsukire-pc-port.zip` from [Releases](https://github.com/Broomstick-Girl-Magical-Amber/tsukire-pc-port/releases), extract it in an empty folder and run the graphical launcher. From its **Install** tab you can install everything and build the game.
> ```bash
> bash tools/linux/launcher.sh gui
> ```

## Requirements

- 64-bit **Ubuntu/Debian**, **Fedora** or **Arch**, or one based on them (e.g. **Linux Mint**). Other distributions may work, but the installer cannot install their packages.
- A legal dump of the game and your own `prod.keys`. See [Requirements](readme.md#requirements) and [Dump and keys](readme.md#dump-and-keys).
- A GPU with a **Vulkan 1.1** driver (see [Vulkan driver](#vulkan-driver)).
- Disk space (estimates, the installer warns if there is less). The dump, about 3 GB per edition, about 10 GB of tools in `~/.local/tsuki-tools` and about 15 GB for vcpkg.
- The prebuilt launcher needs glibc 2.35 or newer (Ubuntu 22.04, Debian 12, Fedora 36, Arch). On an older system it is built on your PC.

## Install and build manually

The installer brings its own PowerShell, compiler and libraries. To start it only needs `git`, `curl`, `tar` and `sudo`

```bash
sudo apt update && sudo apt install -y git curl tar coreutils      # Ubuntu, Debian, Mint
sudo dnf install -y git curl tar coreutils                         # Fedora
sudo pacman -S --needed git curl tar coreutils base-devel          # Arch
```

Get the port (the release zip, or `git clone https://github.com/Broomstick-Girl-Magical-Amber/tsukire-pc-port.git`), put your dump in its folder and `prod.keys` in `data/`, then

```bash
bash tools/linux/install_requirements_linux.sh -Target linux -Recomp -Edition japan
```

`-Target linux` builds the native game (without it, the Windows `.exe` for Proton). `-Edition` is `japan`, `global` or `both`. It asks for your password once, for the system packages; the rest needs no root. Long steps can look quiet, do not close it.

The game ends up in `framework/build/tsukihime/tsukihime` (Japanese) or `framework/build/tsukihime_global/tsukihime_global` (global). To recompile later (another edition, a patch)

```bash
bash tools/linux/recomp_linux.sh -Edition japan -Target linux
```

The other options (`-From`, `-To`, `-Check`, `-TrimXci`, `-Extract`...) are the same as on Windows. See [Useful options](readme.md#useful-options).

## Play

```bash
bash tools/linux/launcher.sh gui            # graphical launcher
bash tools/linux/launcher.sh play           # from the terminal; -e global for the global edition
bash tools/linux/launcher.sh desktop add    # game and launcher in the applications menu
bash tools/linux/launcher.sh --help
```

Without the prebuilt launcher, the first `gui` call builds it. You can also run the game binary directly. The data folder and the dump are found relative to it. Controls, saves, mods, updates and environment variables work as in [Playing](readme.md#playing) and [Updates and patches](readme.md#updates-and-patches). The log of the last run is `data/nx.log`.