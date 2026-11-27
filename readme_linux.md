# tsukire-pc-port Linux

The native Linux build of the port. The game's code is built on your PC from your own dump. The Windows `.exe` can still be played with Proton: see [Rebuild on Linux and play with Proton](readme.md#rebuild-on-linux-and-play-with-proton).

## Requirements

- A 64-bit Intel or AMD Linux with **Ubuntu or Debian**, **Fedora** or **Arch**, or one based on them like **Linux Mint**. Other distributions may work, but the installer cannot install their packages for you.
- A legal dump of the game and your own `prod.keys`, as in [Requirements](readme.md#requirements) and [Dump and keys](readme.md#dump-and-keys).
- A GPU with a **Vulkan 1.1** driver, see [Vulkan driver](#vulkan-driver).
- Disk space: the dump, about 3 GB per edition, about 10 GB of tools in `~/.local/tsuki-tools` and about 15 GB for vcpkg. These are estimates, and the installer warns when there is less.
- The prebuilt launcher needs glibc 2.35 or newer (Ubuntu 22.04, Debian 12, Fedora 36 or newer, Arch). On an older system it is built on your PC.

## Install and build

The installer brings its own PowerShell, compiler and libraries. To start it only needs `git`, `curl`, `tar` and `sudo`:

```bash
sudo apt update && sudo apt install -y git curl tar coreutils      # Ubuntu, Debian, Mint
sudo dnf install -y git curl tar coreutils                         # Fedora
sudo pacman -S --needed git curl tar coreutils base-devel          # Arch
```

Use `git clone`. Put your dump in the cloned folder and `prod.keys` in `data/`.

```bash
git clone https://github.com/Broomstick-Girl-Magical-Amber/tsukire-pc-port.git
cd tsukire-pc-port
bash tools/linux/install_requirements_linux.sh -Target linux -Recomp -Edition japan
```

`-Target linux` makes the native build; without it the installer builds the Windows `.exe` for Proton. `-Edition` is `japan`, `global` or `both`. The installer asks for your password once, to install the system packages. Everything else needs no root. It can look quiet on a long step; do not close it.

The game ends up in `framework/build/tsukihime/tsukihime` (Japanese) or `framework/build/tsukihime_global/tsukihime_global` (global). To recompile later, for another edition or a patch:

```bash
bash tools/linux/recomp_linux.sh -Edition japan -Target linux
```

The other options (`-From`, `-To`, `-Check`, `-TrimXci`, `-Extract`...) are the same as on Windows, see [Useful options](readme.md#useful-options).

## Play

The release zip carries a prebuilt launcher, with the same look and layout as the Windows one, in English or Spanish. Without it, the first call builds it.

```bash
bash tools/linux/launcher.sh gui        # graphical launcher
bash tools/linux/launcher.sh play       # from the terminal; -e global for the global edition
bash tools/linux/launcher.sh desktop add    # game and launcher in the applications menu
bash tools/linux/launcher.sh --help     # every command: saves, mods, updates, controls, options, language...
```

You can also run the binary directly. The data folder and the dump are found relative to it. Controls, saves, mods, updates, shortcuts and environment variables work as in [Playing](readme.md#playing) and [Updates and patches](readme.md#updates-and-patches). The log of the last run is `data/nx.log`.

## Vulkan driver

`bash tools/linux/launcher.sh vulkan` says whether you have the Vulkan loader and a driver and, if not, shows the command for your system. To check by hand, run `vulkaninfo --summary` (package `vulkan-tools`).

| GPU | Ubuntu, Debian, Mint | Fedora | Arch |
|---|---|---|---|
| AMD or Intel | `sudo apt install libvulkan1 mesa-vulkan-drivers` | `sudo dnf install vulkan-loader mesa-vulkan-drivers` | `sudo pacman -S --needed vulkan-icd-loader vulkan-radeon` (Intel: `vulkan-intel`) |
| NVIDIA | Proprietary driver: `sudo ubuntu-drivers install`, or the Driver Manager on Mint | Proprietary driver from [RPM Fusion](https://rpmfusion.org/Howto/NVIDIA) | `sudo pacman -S --needed vulkan-icd-loader nvidia-utils` plus the `nvidia` or `nvidia-open` kernel driver |
| No GPU | `mesa-vulkan-drivers` | `mesa-vulkan-drivers` | `sudo pacman -S --needed vulkan-icd-loader vulkan-swrast` |

Without a GPU the game runs on the CPU (`llvmpipe`), slowly. In a virtual machine, raise the GPU watchdog: `bash tools/linux/launcher.sh play --gpu-hang 120000`.

## Notes per distribution

- **Ubuntu and Debian:** nothing special. Tested on Ubuntu 26.04.
- **Fedora:** Fedora 45 only ships OpenSSL 4 and PowerShell needs OpenSSL 3. The installer stops before downloading anything and shows the command: `sudo dnf install openssl3-libs`.
- **Arch:** the packages have other names, and the Vulkan driver does not come with Mesa. If you update first, use `sudo pacman -Syu`, never `pacman -Sy` alone.
