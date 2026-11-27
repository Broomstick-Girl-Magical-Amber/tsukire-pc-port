#!/usr/bin/env bash
# test_linux_preflight.sh - Tests of tools/linux/preflight.sh (AP_LINUX_PREFLIGHT) with a fake ldconfig and a temporary
# tools folder. Nothing outside a temporary folder is touched.
#   bash tools/tests/test_linux_preflight.sh
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PF="$HERE/../linux/preflight.sh"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
mkdir -p "$W/bin"
fail=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fail=1; }

fake_ldconfig() { printf '#!/bin/sh\ncat <<X\n%s\nX\n' "$1" > "$W/bin/ldconfig"; chmod +x "$W/bin/ldconfig"; }
run_ssl() { ( PATH="$W/bin:$PATH"; . "$PF"; preflight_openssl; echo "override=${CLR_OPENSSL_VERSION_OVERRIDE:-}" ) 2>&1; }

fake_ldconfig '	libssl.so.4 (libc6,x86-64) => /usr/lib64/libssl.so.4'
out="$(run_ssl)"; echo "$out" | grep -q 'ERROR: PowerShell 7' && ok "only OpenSSL 4: stops with what to install" || bad "only OpenSSL 4"

fake_ldconfig '	libssl.so.4 (libc6,x86-64) => /a
	libssl.so.3 (libc6,x86-64) => /b'
out="$(run_ssl)"; echo "$out" | grep -q 'override=3' && ok "OpenSSL 3 and 4: override=3" || bad "OpenSSL 3 and 4"

fake_ldconfig '	libssl.so.3 (libc6,x86-64) => /b'
out="$(run_ssl)"; echo "$out" | grep -q 'override=$' && ! echo "$out" | grep -q ERROR && ok "only OpenSSL 3: nothing to do" || bad "only OpenSSL 3"

fake_ldconfig '	libssl.so.1.1 (libc6,x86-64) => /b'
out="$(run_ssl)"; ! echo "$out" | grep -q ERROR && ok "OpenSSL 1.1: accepted" || bad "OpenSSL 1.1"

fake_ldconfig ''
out="$(run_ssl)"; ! echo "$out" | grep -q ERROR && ok "loader cache unreadable: does not block" || bad "empty cache"

# leftovers
T="$W/tools"; mkdir -p "$T/llvm-1.tmp" "$T/xwin.tmp" "$T/keep"; touch "$T/a.tmp"
out="$( ( . "$PF"; preflight_tmp ) 2>&1 )"
[ ! -e "$T/llvm-1.tmp" ] && [ ! -e "$T/xwin.tmp" ] && [ ! -e "$T/a.tmp" ] && [ -d "$T/keep" ] && ok "leftovers removed, the rest kept" || bad "leftovers"

mkdir -p "$T/llvm-2.tmp"; mkdir -p "$W/run"; cp "$(command -v sleep)" "$W/run/install_requirements.ps1"
"$W/run/install_requirements.ps1" 3 & sp=$!; sleep 0.3
out="$( ( . "$PF"; preflight_tmp ) 2>&1 )"
[ -d "$T/llvm-2.tmp" ] && echo "$out" | grep -q 'NOT removed' && ok "another install running: nothing removed" || bad "busy"
kill "$sp" 2>/dev/null; wait "$sp" 2>/dev/null

[ "$fail" = 0 ] && echo "all OK" || { echo "FAILED"; exit 1; }
