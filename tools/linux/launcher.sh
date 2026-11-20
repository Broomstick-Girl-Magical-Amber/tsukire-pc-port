#!/usr/bin/env bash
# launcher.sh - Command-line launcher of the native Linux build: the same things as the Windows launcher
# (play, saves, install/recompile, status), as one script. No GUI, no dependencies beyond bash and coreutils.
#
#   bash tools/linux/launcher.sh status   [-e japan|global]     what is ready (build, data, dump, keys, Vulkan)
#   bash tools/linux/launcher.sh play     [-e ...] [--gpu-hang MS] [-- game args]
#   bash tools/linux/launcher.sh log                            last lines of data/nx.log (or --prev for the old one)
#   bash tools/linux/launcher.sh saves list|backup|restore [N|name]|import <file>   [-e ...]
#   bash tools/linux/launcher.sh open data|saves|backups        open a folder with xdg-open
#   bash tools/linux/launcher.sh install  [-e ...|both] [options of install_requirements.ps1]
#   bash tools/linux/launcher.sh recomp   [-e ...]              recompile one edition (the tools must be installed)
#   bash tools/linux/launcher.sh edition  [japan|global]        show / set the default edition (launcher.ini)
#
# The default edition is the one of launcher.ini (key "edition", the file the Windows launcher uses), else the first
# one that is built, else japan. Everything is relative to the repository: the root is two folders above this script.
# Saves: data/<edition>/user/save; copies in save_backups/<edition>/<yyyy-mm-dd_hhmmss>[_before_restore|_before_import].
# Nothing here reads prod.keys or the content of a dump (only the container header, as the installer does).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
INI="$ROOT/launcher.ini"
. "$HERE/vulkan_check.sh" 2>/dev/null || true   # command to install Vulkan (exit code 6)

die() { echo "ERROR: $*" >&2; exit 1; }
say() { echo "$*"; }

# ---- editions ------------------------------------------------------------------------------------------------
ed_game() { case "$1" in japan) echo tsukihime ;; global) echo tsukihime_global ;; *) return 1 ;; esac; }
ed_data() { case "$1" in japan) echo tsuki ;; global) echo tsuki_global ;; *) return 1 ;; esac; }
ed_name() { case "$1" in japan) echo "Japanese (cartridge)" ;; global) echo "Global (eShop)" ;; esac; }
ed_valid() { case "$1" in japan|global) return 0 ;; *) return 1 ;; esac; }
bin_of()   { local g; g="$(ed_game "$1")"; echo "$ROOT/framework/build/$g/$g"; }
data_of()  { echo "$ROOT/data/$(ed_data "$1")"; }
save_of()  { echo "$(data_of "$1")/user/save"; }
bkp_of()   { echo "$ROOT/save_backups/$1"; }
built()    { [ -x "$(bin_of "$1")" ] && [ "$(head -c4 "$(bin_of "$1")" 2>/dev/null | od -An -c | tr -d ' ')" = '177ELF' ]; }
data_ready() { [ -f "$(data_of "$1")/romfs_source.bin" ] || [ -d "$(data_of "$1")/extract/romfs/shader" ]; }
stamp()    { date +%Y-%m-%d_%H%M%S; }

ini_get() { [ -f "$INI" ] || return 1; sed -n "s/^$1=//p" "$INI" | tr -d '\r' | head -1; }
ini_set() {   # ini_set key value: keeps the other lines (the Windows launcher shares the file)
    local k="$1" v="$2" tmp
    if [ -f "$INI" ] && grep -q "^$k=" "$INI"; then
        tmp="$(mktemp)"; sed "s|^$k=.*|$k=$v|" "$INI" > "$tmp" && cat "$tmp" > "$INI"; rm -f "$tmp"
    else
        printf '%s=%s\n' "$k" "$v" >> "$INI"
    fi
}

default_edition() {
    local e; e="$(ini_get edition || true)"
    if ed_valid "${e:-}"; then echo "$e"; return; fi
    for e in japan global; do built "$e" && { echo "$e"; return; }; done
    echo japan
}

# Is the game of this edition running? (any process whose executable is that binary)
running() {
    local b p; b="$(readlink -f "$(bin_of "$1")" 2>/dev/null)" || return 1
    [ -n "$b" ] || return 1
    for p in /proc/[0-9]*/exe; do [ "$(readlink "$p" 2>/dev/null)" = "$b" ] && return 0; done
    return 1
}

# ---- argument parsing shared by the commands ---------------------------------------------------------------------
EDITION=""; REST=()
parse_ed() {   # parse_ed args...: sets EDITION (-e/--edition) and REST (the other arguments)
    EDITION=""; REST=()
    while [ $# -gt 0 ]; do
        case "$1" in
            -e|--edition) [ $# -ge 2 ] || die "$1 needs japan or global"; EDITION="$2"; shift 2 ;;
            *) REST+=("$1"); shift ;;
        esac
    done
    [ -n "$EDITION" ] || EDITION="$(default_edition)"
    [ "$EDITION" = both ] || ed_valid "$EDITION" || die "unknown edition '$EDITION' (japan, global)"
}

# ---- status --------------------------------------------------------------------------------------------------------
dump_kind() {   # container header of the dump: .nsp = PFS0 at 0, .xci = HEAD at 0x100
    local f="$1"
    case "${f,,}" in
        *.nsp) [ "$(head -c4 "$f" 2>/dev/null)" = PFS0 ] ;;
        *.xci) [ "$(dd if="$f" bs=1 skip=256 count=4 2>/dev/null)" = HEAD ] ;;
        *) return 1 ;;
    esac
}

cmd_status() {
    parse_ed "$@"
    local e f n=0 list=("$EDITION"); [ "$EDITION" = both ] && list=(japan global)
    say "Folder: $ROOT"
    if [ -f "$ROOT/data/prod.keys" ]; then say "prod.keys: data/prod.keys"
    elif [ -f "$ROOT/prod.keys" ]; then say "prod.keys: in the root (the recompile moves it to data/)"
    else say "prod.keys: MISSING (put it in data/ or in the root)"; fi
    shopt -s nullglob nocaseglob
    for f in "$ROOT"/*.nsp "$ROOT"/*.xci; do dump_kind "$f" && { say "dump: ${f##*/}"; n=$((n+1)); }; done
    shopt -u nullglob nocaseglob
    [ "$n" -gt 0 ] || say "dump: none (.xci for the Japanese edition, .nsp for the Global one, in the root)"
    if [ -x "$ROOT/framework/private/venv/bin/python" ] || [ -x "$ROOT/framework/private/venv/bin/python3" ]; then
        say "tools: installed (venv)"; else say "tools: not installed (launcher.sh install)"; fi
    if declare -F vulkan_present >/dev/null; then
        if vulkan_present; then say "Vulkan: loader and driver found"; else say "Vulkan: no loader or no driver (launcher.sh install asks to install it)"; fi
    fi
    for e in "${list[@]}"; do
        local st="not built (launcher.sh install -e $e)"
        if running "$e"; then st="running"
        elif built "$e" && data_ready "$e"; then st="ready"
        elif built "$e"; then st="built, but data/$(ed_data "$e") has no romfs_source.bin: recompile it"
        elif [ -f "$ROOT/framework/build/$(ed_game "$e")/$(ed_game "$e").exe" ]; then st="only the Windows .exe is built (recompile with -Target linux)"; fi
        say "$(ed_name "$e"): $st"
        if [ -d "$(save_of "$e")" ]; then
            local c; c="$(find "$(save_of "$e")" -type f 2>/dev/null | wc -l)"
            say "   saves: $c file(s)"
        fi
    done
}

# ---- play ------------------------------------------------------------------------------------------------------------
explain_exit() {
    case "$1" in
        4) say "The game stopped because the GPU stopped responding (code 4). In a virtual machine (llvmpipe) try: launcher.sh play --gpu-hang 120000" ;;
        5) say "Your graphics driver does not support Vulkan 1.1 (code 5). Update the graphics driver and try again." ;;
        6) say "Vulkan is not available on this PC (code 6). The game needs a GPU with a Vulkan driver."
           if declare -F vulkan_install_cmd >/dev/null; then
               local r; r="$(vulkan_install_cmd)"; [ -n "$r" ] && say "Install it with: ${r%%|*}"
           fi ;;
    esac
}

cmd_play() {
    local args=() gh=""
    parse_ed "$@"; set -- "${REST[@]+"${REST[@]}"}"
    while [ $# -gt 0 ]; do
        case "$1" in
            --gpu-hang) [ $# -ge 2 ] || die "--gpu-hang needs milliseconds"; gh="$2"; shift 2 ;;
            --) shift; args=("$@"); break ;;
            *) die "unknown option '$1' (play [-e ed] [--gpu-hang MS] [-- game args])" ;;
        esac
    done
    [ "$EDITION" != both ] || die "play needs one edition (-e japan or -e global)"
    local e="$EDITION" b; b="$(bin_of "$e")"
    [ -f "$ROOT/framework/build/$(ed_game "$e")/$(ed_game "$e").exe" ] && ! built "$e" \
        && die "only the Windows .exe is built for $(ed_name "$e"): recompile it with -Target linux (launcher.sh install -e $e)"
    built "$e" || die "$(ed_name "$e") is not built: bash tools/linux/launcher.sh install -e $e"
    data_ready "$e" || die "data/$(ed_data "$e") has no romfs_source.bin: recompile $(ed_name "$e") (launcher.sh recomp -e $e)"
    running "$e" && die "$(ed_name "$e") is already running"
    case "$gh" in ""|*[!0-9]*) [ -z "$gh" ] || die "--gpu-hang needs a number of milliseconds" ;; esac
    [ -n "$gh" ] && export NX_GPU_HANG_MS="$gh"
    say "Starting $(ed_name "$e")  (log: data/nx.log)"
    "$b" "${args[@]+"${args[@]}"}"
    local rc=$?
    [ "$rc" -eq 0 ] || { say "The game ended with code $rc."; explain_exit "$rc"; say "Log: bash tools/linux/launcher.sh log"; }
    return "$rc"
}

cmd_log() {
    local f="$ROOT/data/nx.log" n=60
    for a in "$@"; do case "$a" in --prev) f="$ROOT/data/nx.log.prev" ;; esac; done
    [ -f "$f" ] || die "no $f yet (the game writes it when it starts)"
    say "== $f (last $n lines)"; tail -n "$n" "$f"
}

# ---- saves ----------------------------------------------------------------------------------------------------------
backup_saves() {   # backup_saves <edition> <suffix>: copies save/ to save_backups/<ed>/<stamp><suffix>; prints the folder
    local e="$1" suffix="${2:-}" src dst; src="$(save_of "$e")"
    [ -d "$src" ] || { echo ""; return 0; }
    mkdir -p "$(bkp_of "$e")" || return 1
    dst="$(bkp_of "$e")/$(stamp)$suffix"
    local k=1; while [ -e "$dst" ]; do k=$((k+1)); dst="$(bkp_of "$e")/$(stamp)${suffix}_$k"; done   # same second: cp -a would nest
    if cp -a -- "$src" "$dst"; then echo "$dst"; else rm -rf -- "$dst"; return 1; fi
}

list_backups() { [ -d "$(bkp_of "$1")" ] && ls -1 "$(bkp_of "$1")" 2>/dev/null | sort -r; }

# Replaces save/ by a copy of $2. The current saves are copied first (suffix $3). Any failure leaves everything as it was.
replace_saves() {
    local e="$1" from="$2" suffix="$3" save safety
    save="$(save_of "$e")"
    running "$e" && die "$(ed_name "$e") is running: close it first"
    safety="$(backup_saves "$e" "$suffix")" || die "could not copy the current saves: nothing changed"
    [ -n "$safety" ] && say "Current saves copied to ${safety#$ROOT/}"
    mkdir -p "$(dirname "$save")"
    if [ -d "$save" ]; then mv -- "$save" "$save.restoring" || die "could not move the current saves: nothing changed"; fi
    if cp -a -- "$from" "$save"; then
        rm -rf -- "$save.restoring"
    else
        rm -rf -- "$save"; [ -d "$save.restoring" ] && mv -- "$save.restoring" "$save"
        die "the copy failed: the saves are as they were"
    fi
}

cmd_saves() {
    local sub="${1:-}"; [ $# -gt 0 ] && shift
    parse_ed "$@"; set -- "${REST[@]+"${REST[@]}"}"
    [ "$EDITION" != both ] || die "saves needs one edition (-e japan or -e global)"
    local e="$EDITION" save; save="$(save_of "$e")"
    case "$sub" in
        list|"")
            if [ -d "$save" ]; then
                say "Saves of $(ed_name "$e"): $(find "$save" -type f | wc -l) file(s), last change $(find "$save" -type f -printf '%TY-%Tm-%Td %TH:%TM\n' 2>/dev/null | sort | tail -1)"
            else say "Saves of $(ed_name "$e"): none yet"; fi
            local i=0 b
            while read -r b; do [ -n "$b" ] || continue; i=$((i+1)); printf '  %2d) %s\n' "$i" "$b"; done < <(list_backups "$e")
            [ "$i" -gt 0 ] && say "Restore: launcher.sh saves restore <number|name> -e $e" || say "No copies yet (launcher.sh saves backup)" ;;
        backup)
            [ -d "$save" ] || die "there are no saves yet"
            local d; d="$(backup_saves "$e" "")" || die "the copy failed"
            say "Copy made: ${d#$ROOT/}" ;;
        restore)
            local pick="${1:-}" name
            [ -n "$pick" ] || die "which copy? (launcher.sh saves list)"
            if [[ "$pick" =~ ^[0-9]+$ ]]; then name="$(list_backups "$e" | sed -n "${pick}p")"; else name="$pick"; fi
            [ -n "$name" ] && [ -d "$(bkp_of "$e")/$name" ] || die "no such copy '$pick' (launcher.sh saves list)"
            case "$name" in */*|..) die "invalid name" ;; esac
            replace_saves "$e" "$(bkp_of "$e")/$name" "_before_restore"
            say "Restored: $name" ;;
        import)
            local f="${1:-}"
            [ -f "$f" ] || die "import needs a save FILE (launcher.sh saves import <file>)"
            import_save "$e" "$f" ;;
        *) die "saves: list | backup | restore <number|name> | import <file>" ;;
    esac
}

# One save file replaces the matching current one: 1) same relative path when it comes from ".../user/save/<mount>/..",
# 2) the only file with that name in save/ (case-insensitive), 3) the one in a folder named like the chosen file's.
import_save() {
    local e="$1" f="$2" save rel="" target="" cand=() c n; save="$(save_of "$e")"
    [ -d "$save" ] || die "there are no saves yet: nothing to replace"
    f="$(readlink -f -- "$f")"
    case "$f" in */user/save/*) rel="${f#*/user/save/}" ;; esac
    [ -n "$rel" ] && [ -f "$save/$rel" ] && target="$save/$rel"
    if [ -z "$target" ]; then
        n="$(basename -- "$f")"
        while IFS= read -r c; do cand+=("$c"); done < <(find "$save" -type f -iname "$n" 2>/dev/null)
        if [ "${#cand[@]}" -eq 1 ]; then target="${cand[0]}"
        elif [ "${#cand[@]}" -gt 1 ]; then
            local pd; pd="$(basename -- "$(dirname -- "$f")")"
            local hit=()
            for c in "${cand[@]}"; do [ "$(basename -- "$(dirname -- "$c")")" = "$pd" ] && hit+=("$c"); done
            [ "${#hit[@]}" -eq 1 ] && target="${hit[0]}"
        fi
    fi
    [ -n "$target" ] || die "cannot tell which save file '$(basename -- "$f")' replaces (no match or ambiguous): nothing changed"
    running "$e" && die "$(ed_name "$e") is running: close it first"
    say "It will REPLACE: ${target#$ROOT/}   ($(stat -c %s -- "$f") bytes from $f)"
    say "All the current saves are copied first to save_backups/$e/<date>_before_import."
    if [ -t 0 ]; then
        local ans; read -r -p "Go ahead? [y/N]: " ans || ans=""
        case "${ans,,}" in y|yes) ;; *) say "Cancelled."; return 0 ;; esac
    elif [ "${LAUNCHER_ASSUME_YES:-0}" != 1 ]; then
        die "no terminal to ask: set LAUNCHER_ASSUME_YES=1 to go ahead"
    fi
    local safety; safety="$(backup_saves "$e" "_before_import")" || die "could not copy the current saves: nothing changed"
    say "Copy made: ${safety#$ROOT/}"
    cp -- "$f" "$target.importing" && mv -f -- "$target.importing" "$target" || { rm -f -- "$target.importing"; die "the copy failed: the original is intact"; }
    say "Imported."
}

# ---- open / install / recomp / edition ----------------------------------------------------------------------------
cmd_open() {
    local what="${1:-}"; [ $# -gt 0 ] && shift
    parse_ed "$@"
    [ "$EDITION" != both ] || die "open needs one edition"
    local d
    case "$what" in
        data) d="$(data_of "$EDITION")" ;; saves) d="$(save_of "$EDITION")" ;; backups) d="$(bkp_of "$EDITION")" ;;
        *) die "open: data | saves | backups" ;;
    esac
    [ -d "$d" ] || die "$d does not exist yet"
    command -v xdg-open >/dev/null 2>&1 || { say "$d"; return 0; }
    xdg-open "$d" >/dev/null 2>&1 &
}

cmd_install() {
    local ed="" args=() a
    while [ $# -gt 0 ]; do
        case "$1" in
            -e|--edition) [ $# -ge 2 ] || die "$1 needs japan, global or both"; ed="$2"; shift 2 ;;
            *) args+=("$1"); shift ;;
        esac
    done
    [ -n "$ed" ] || ed="$(default_edition)"
    case "$ed" in japan|global|both) ;; *) die "unknown edition '$ed' (japan, global, both)" ;; esac
    for a in "${args[@]+"${args[@]}"}"; do case "${a,,}" in -target) die "launcher.sh install already uses -Target linux" ;; esac; done
    exec bash "$HERE/install_requirements_linux.sh" -Target linux -Recomp -Edition "$ed" "${args[@]+"${args[@]}"}"
}

cmd_recomp() {
    parse_ed "$@"
    [ "$EDITION" != both ] || die "recomp needs one edition (run it twice, or use install -e both)"
    running "$EDITION" && die "$(ed_name "$EDITION") is running: close it first"
    exec bash "$HERE/recomp_linux.sh" -Target linux -Edition "$EDITION" "${REST[@]+"${REST[@]}"}"
}

cmd_edition() {
    if [ $# -eq 0 ]; then say "$(default_edition)"; return 0; fi
    ed_valid "$1" || die "unknown edition '$1' (japan, global)"
    ini_set edition "$1"; say "Default edition: $1"
}

usage() { awk 'NR>1 { if ($0 !~ /^#/) exit; sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"; }

cmd="${1:-}"; [ $# -gt 0 ] && shift
case "$cmd" in
    status) cmd_status "$@" ;;
    play) cmd_play "$@" ;;
    log) cmd_log "$@" ;;
    saves) cmd_saves "$@" ;;
    open) cmd_open "$@" ;;
    install) cmd_install "$@" ;;
    recomp) cmd_recomp "$@" ;;
    edition) cmd_edition "$@" ;;
    ""|-h|--help|help) usage ;;
    *) echo "unknown command '$cmd'" >&2; usage >&2; exit 1 ;;
esac
