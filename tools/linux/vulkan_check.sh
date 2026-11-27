# vulkan_check.sh - Asks to install the Vulkan driver when the system has none (AP_LINUX_VULKAN). Loaded with
# ". vulkan_check.sh" after common.sh; it does not run on its own.
#
#   vulkan_check <install_requirements args...>
#       Looks for the Vulkan loader (libvulkan.so.1) and at least one driver (an ICD file). If something is missing it
#       shows the exact command to install it for this distribution (pacman / apt / dnf) and asks first. Without a
#       terminal, or when answering no, it only shows the command and goes on: the build does not need a GPU (the
#       install ends with the warning "no GPU is usable to play"). The command runs with sudo and nothing else does.
#       Skipped with -Check, when step 9 (gpu) is outside -From/-To, with VK_ICD_FILENAMES / VK_DRIVER_FILES set, or
#       with NX_SKIP_VULKAN_CHECK=1.

# 0 when the Vulkan loader and at least one driver (ICD) are installed.
vulkan_present() {
    local d n=0
    command -v ldconfig >/dev/null 2>&1 || [ -x /sbin/ldconfig ] || return 0   # cannot see: do not block
    { ldconfig -p 2>/dev/null || /sbin/ldconfig -p 2>/dev/null || true; } | grep -q 'libvulkan\.so\.1' || return 1
    for d in /usr/share/vulkan/icd.d /etc/vulkan/icd.d /usr/local/share/vulkan/icd.d "$HOME/.local/share/vulkan/icd.d"; do
        [ -d "$d" ] && n=$((n + $(find "$d" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l)))
    done
    [ "$n" -gt 0 ]
}

# Vendors of the video cards, from sysfs (no lspci needed): "nvidia amd intel" in the ones found, in that order.
vulkan_vendors() {
    local f v out=""
    for f in /sys/class/drm/card[0-9]*/device/vendor; do
        [ -r "$f" ] || continue
        v="$(cat "$f" 2>/dev/null)"
        case "$v" in
            0x10de) case " $out " in *" nvidia "*) ;; *) out="$out nvidia" ;; esac ;;
            0x1002) case " $out " in *" amd "*) ;; *) out="$out amd" ;; esac ;;
            0x8086) case " $out " in *" intel "*) ;; *) out="$out intel" ;; esac ;;
        esac
    done
    echo $out
}

# Prints "<command>|<note>" for this distribution, or nothing when it is not one we know.
vulkan_install_cmd() {
    local id="" like="" vend v pk=""
    if [ -r /etc/os-release ]; then
        id="$(. /etc/os-release; echo "${ID:-}")"; like="$(. /etc/os-release; echo "${ID_LIKE:-}")"
    fi
    vend="$(vulkan_vendors)"
    local note
    if [ -n "$vend" ]; then note="graphics card: $vend"; else note="no GPU found, virtual machine?: the CPU driver llvmpipe is installed"; fi
    case " $id $like " in
        *" arch "*)
            pk="vulkan-icd-loader"
            for v in $vend; do
                case "$v" in nvidia) pk="$pk nvidia-utils" ;; amd) pk="$pk vulkan-radeon" ;; intel) pk="$pk vulkan-intel" ;; esac
            done
            [ -n "$vend" ] || pk="$pk vulkan-swrast"
            echo "sudo pacman -S --needed $pk|$note" ;;
        *" fedora "*|*" rhel "*)
            echo "sudo dnf install -y vulkan-loader mesa-vulkan-drivers|$note; NVIDIA proprietary: its own driver" ;;
        *" debian "*|*" ubuntu "*)
            echo "sudo apt install -y libvulkan1 mesa-vulkan-drivers|$note; NVIDIA proprietary: its own driver" ;;
    esac
}

vulkan_check() {
    local a prev="" from=0 to=11 cmd note ans
    [ "${NX_SKIP_VULKAN_CHECK:-0}" = 1 ] && return 0
    [ -n "${VK_ICD_FILENAMES:-}${VK_DRIVER_FILES:-}" ] && return 0
    for a in "$@"; do
        case "${a,,}" in -check) return 0 ;; esac
        case "${prev,,}" in -from) from="$a" ;; -to) to="$a" ;; esac
        prev="$a"
    done
    case "$from$to" in *[!0-9]*) return 0 ;; esac
    { [ "$from" -gt 9 ] || [ "$to" -lt 9 ]; } && return 0
    vulkan_present && return 0
    echo "   Vulkan: no loader or no driver found (the game needs one to run; building does not)."
    local r; r="$(vulkan_install_cmd)"
    if [ -z "$r" ]; then
        echo "   Install the Vulkan loader and the driver of your graphics card with your package manager."
        return 0
    fi
    cmd="${r%%|*}"; note="${r#*|}"
    echo "   $note"
    echo "   command: $cmd"
    if [ ! -t 0 ]; then
        echo "   (no terminal: not run. Run it yourself when you want to play.)"
        return 0
    fi
    read -r -p "   Run it now? [y/N]: " ans || ans=""
    case "${ans,,}" in
        y|yes) if sh -c "$cmd"; then echo "   Vulkan installed."; else echo "   WARNING: the command failed; the install goes on without it."; fi ;;
        *) echo "   Not run: the install goes on (it ends with the warning about the GPU)." ;;
    esac
    return 0
}
