#!/usr/bin/env bash
# test_linux_vulkan.sh - Tests of tools/linux/vulkan_check.sh (AP_LINUX_VULKAN) with fake ldconfig / sysfs-free paths.
#   bash tools/tests/test_linux_vulkan.sh
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VC="$HERE/../linux/vulkan_check.sh"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
mkdir -p "$W/bin"
fail=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fail=1; }
fake_ldconfig() { printf '#!/bin/sh\ncat <<X\n%s\nX\n' "$1" > "$W/bin/ldconfig"; chmod +x "$W/bin/ldconfig"; }
fake_osrel() { printf 'ID=%s\nID_LIKE="%s"\n' "$1" "${2:-}" > "$W/os-release"; }
# the function reads /etc/os-release: run it through a copy that points at the fake one
sed "s#/etc/os-release#$W/os-release#g" "$VC" > "$W/vc.sh"
run() { ( PATH="$W/bin:$PATH"; HOME="$W/home"; . "$W/vc.sh"; "$@" ) 2>&1 < /dev/null; }

fake_osrel arch
out="$(run vulkan_install_cmd)"; echo "$out" | grep -q '^sudo pacman -S --needed vulkan-icd-loader vulkan-swrast|' && ok "arch, no GPU: swrast" || bad "arch: $out"
fake_osrel fedora
out="$(run vulkan_install_cmd)"; echo "$out" | grep -q '^sudo dnf install -y vulkan-loader mesa-vulkan-drivers|' && ok "fedora" || bad "fedora: $out"
fake_osrel ubuntu debian
out="$(run vulkan_install_cmd)"; echo "$out" | grep -q '^sudo apt install -y libvulkan1 mesa-vulkan-drivers|' && ok "ubuntu" || bad "ubuntu: $out"
fake_osrel nixos
out="$(run vulkan_install_cmd)"; [ -z "$out" ] && ok "unknown distro: no command" || bad "nixos: $out"

# present / absent
fake_ldconfig '	libvulkan.so.1 (libc6,x86-64) => /usr/lib/libvulkan.so.1'
mkdir -p "$W/home/.local/share/vulkan/icd.d"
run vulkan_present && bad "loader without ICD must be absent" || ok "loader but no driver: absent"
echo '{}' > "$W/home/.local/share/vulkan/icd.d/x.json"
run vulkan_present && ok "loader and driver: present" || bad "present"
fake_ldconfig '	libfoo.so.1 (libc6,x86-64) => /a'
run vulkan_present && bad "no loader" || ok "no loader: absent"
rm "$W/home/.local/share/vulkan/icd.d/x.json"

# vulkan_check: no terminal -> shows the command, does not run it, returns 0
fake_osrel arch
out="$(run vulkan_check)"; rc=$?
echo "$out" | grep -q 'command: sudo pacman' && echo "$out" | grep -q 'no terminal' && ok "no terminal: shows, does not run" || bad "no tty: $out"
out="$(run vulkan_check -Check)"; [ -z "$out" ] && ok "-Check: silent" || bad "-Check"
out="$(run vulkan_check -From 10)"; [ -z "$out" ] && ok "-From 10: silent" || bad "-From 10"
out="$(run vulkan_check -To 5)"; [ -z "$out" ] && ok "-To 5: silent" || bad "-To 5"
out="$(run vulkan_check -From 8)"; echo "$out" | grep -q 'command:' && ok "-From 8: checks" || bad "-From 8"
out="$(NX_SKIP_VULKAN_CHECK=1 run vulkan_check)"; [ -z "$out" ] && ok "NX_SKIP_VULKAN_CHECK" || bad "skip"
out="$(VK_ICD_FILENAMES=/x run vulkan_check)"; [ -z "$out" ] && ok "VK_ICD_FILENAMES set: silent" || bad "icd env"

# with a terminal (script(1)): answer y runs the command (fake sudo via sh -c replaced by a stub), n does not
if command -v script >/dev/null 2>&1; then
    printf '#!/bin/sh\necho RAN "$@" > "%s/ran"\n' "$W" > "$W/bin/sudo"; chmod +x "$W/bin/sudo"
    for ans in y n; do
        rm -f "$W/ran"
        printf '%s\n' "$ans" | script -qec "env PATH=$W/bin:$PATH HOME=$W/home bash -c '. $W/vc.sh; vulkan_check'" /dev/null >/dev/null 2>&1
        if [ "$ans" = y ]; then [ -f "$W/ran" ] && grep -q 'pacman -S --needed' "$W/ran" && ok "answer y: runs the command" || bad "y"; else [ ! -f "$W/ran" ] && ok "answer n: does not run" || bad "n"; fi
    done
fi
[ "$fail" = 0 ] && echo "all OK" || { echo "FAILED"; exit 1; }
