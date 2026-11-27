#!/usr/bin/env bash
# launcher.sh - Command-line launcher of the native Linux build: the same things as the Windows launcher
# (play, saves, install/recompile, status), as one script. No GUI, no dependencies beyond bash and coreutils.
#
# In this help, [ ] means optional and | means "one of": do not type them. Example: launcher.sh play -e global --gpu-hang 120000
#
#   bash tools/linux/launcher.sh status   [-e japan|global]     what is ready (build, data, dump, keys, Vulkan)
#   bash tools/linux/launcher.sh play     [-e ...] [--gpu-hang MS] [-- game args]
#   bash tools/linux/launcher.sh log                            last lines of data/nx.log (or --prev for the old one)
#   bash tools/linux/launcher.sh saves list|backup|restore [N|name]|import <file>   [-e ...]
#   bash tools/linux/launcher.sh mods     list|enable <name>|disable <name>|add <folder>|remove <name>   [-e ...]
#   bash tools/linux/launcher.sh updates  list|add <file.nsp>|remove <file.nsp>
#   bash tools/linux/launcher.sh open data|saves|backups        open a folder with xdg-open
#   bash tools/linux/launcher.sh install  [-e ...|both] [options of install_requirements.ps1]
#   bash tools/linux/launcher.sh recomp   [-e ...]              recompile one edition (the tools must be installed)
#   bash tools/linux/launcher.sh edition  [japan|global]        show / set the default edition (launcher.ini)
#   bash tools/linux/launcher.sh controls [show]                keyboard and mouse of the game (input.ini, both editions)
#   bash tools/linux/launcher.sh controls bind keyboard|mouse ACTION key,key,... [--add] [--move|--share]
#   bash tools/linux/launcher.sh controls unbind|clear|reset ...   (reset keyboard|mouse|all), controls keys = valid names
#   bash tools/linux/launcher.sh title [show]                   title screen video and music (title.ini, both editions)
#   bash tools/linux/launcher.sh title set video|pool|music|hotkeys|click|unlock|fade|swap_delay VALUE | title reset
#   bash tools/linux/launcher.sh options [show] [-e ed|both]    options of the game (config.txt of that edition)
#   bash tools/linux/launcher.sh options set NAME VALUE | options reset [NAME] | options gpus
#   bash tools/linux/launcher.sh vulkan [show|install]          is Vulkan installed? the exact command to install it (install asks first)
#   bash tools/linux/launcher.sh desktop [add|remove]           applications menu entries (the game and this launcher, with the port's icon)
#   bash tools/linux/launcher.sh language [auto|en|es]          language of the graphical launcher (launcher.ini, key "language")
#   bash tools/linux/launcher.sh gui [--build]                  the graphical launcher (Dear ImGui + SDL3; the prebuilt one of the
#                                                               release zip, else built the first time; --build forces a build)
#
# The default edition is the one of launcher.ini (key "edition", the file the Windows launcher uses), else the first
# one that is built, else japan. Everything is relative to the repository: the root is two folders above this script.
# Saves: data/<edition>/user/save; copies in save_backups/<edition>/<yyyy-mm-dd_hhmmss>[_before_restore|_before_import].
# status, saves list and mods list also take --tsv (tab-separated lines for the GUI: tools/linux/gui).
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
ed_name() { case "$1" in japan) echo "Japanese" ;; global) echo "Global" ;; esac; }
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
EDITION=""; REST=(); TSV=0
parse_ed() {   # parse_ed args...: sets EDITION (-e/--edition), TSV (--tsv: tab-separated output for the GUI) and REST
    EDITION=""; REST=(); TSV=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --tsv) TSV=1; shift ;;
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

kv() { if [ "$TSV" = 1 ]; then printf '%s\t%s\n' "$1" "$2"; else say "$3"; fi; }

# "<state>|<text>" of an edition: running, ready, nodata, notbuilt, winonly
edition_state() {
    local e="$1"
    if running "$e"; then echo "running|running"
    elif built "$e" && data_ready "$e"; then echo "ready|ready"
    elif built "$e"; then echo "nodata|built, but data/$(ed_data "$e") has no romfs_source.bin: recompile it"
    elif [ -f "$ROOT/framework/build/$(ed_game "$e")/$(ed_game "$e").exe" ]; then echo "winonly|only the Windows .exe is built (recompile with -Target linux)"
    else echo "notbuilt|not built (launcher.sh install -e $e)"; fi
}

# The venv counts only with its packages: python alone says nothing (the venv can exist without lz4, which nso2bin.py
# needs, and the recompile stops at step 0). Same two packages as step 6 of install_requirements.ps1 imports.
venv_has_packages() {
    local d ok=1
    for d in "$ROOT"/framework/private/venv/lib/python3*/site-packages; do
        [ -d "$d/lz4" ] && [ -d "$d/capstone" ] && ok=0
    done
    return $ok
}

cmd_status() {
    parse_ed "$@"
    local e f n=0 list=("$EDITION"); [ "$EDITION" = both ] && list=(japan global)
    kv folder "$ROOT" "Folder: $ROOT"
    if [ -f "$ROOT/data/prod.keys" ]; then kv keys data "prod.keys: data/prod.keys"
    elif [ -f "$ROOT/prod.keys" ]; then kv keys root "prod.keys: in the root (the recompile moves it to data/)"
    else kv keys none "prod.keys: MISSING (put it in data/ or in the root)"; fi
    shopt -s nullglob nocaseglob
    for f in "$ROOT"/*.nsp "$ROOT"/*.xci; do dump_kind "$f" && { kv dump "${f##*/}" "dump: ${f##*/}"; n=$((n+1)); }; done
    shopt -u nullglob nocaseglob
    [ "$n" -gt 0 ] || kv dump "" "dump: none (put your .xci or .nsp in the root)"
    if [ ! -x "$ROOT/framework/private/venv/bin/python" ] && [ ! -x "$ROOT/framework/private/venv/bin/python3" ]; then
        kv tools no "tools: not installed (launcher.sh install)"
    elif ! venv_has_packages; then
        kv tools no "tools: incomplete, the venv has no lz4/capstone (launcher.sh install fixes it)"
    else kv tools yes "tools: installed (venv)"; fi
    if declare -F vulkan_present >/dev/null; then
        if vulkan_present; then kv vulkan yes "Vulkan: loader and driver found"; else kv vulkan no "Vulkan: no loader or no driver (launcher.sh install asks to install it)"; fi
    fi
    for e in "${list[@]}"; do
        local r st txt; r="$(edition_state "$e")"; st="${r%%|*}"; txt="${r#*|}"
        if [ "$TSV" = 1 ]; then printf 'edition\t%s\t%s\t%s\n' "$e" "$st" "$txt"; else say "$(ed_name "$e"): $txt"; fi
        if [ -d "$(save_of "$e")" ]; then
            local c; c="$(find "$(save_of "$e")" -type f 2>/dev/null | wc -l)"
            if [ "$TSV" = 1 ]; then printf 'saves\t%s\t%s\n' "$e" "$c"; else say "   saves: $c file(s)"; fi
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
            --gpu-hang) [ $# -ge 2 ] || die "--gpu-hang needs milliseconds"; gh="${2%[mM][sS]}"; shift 2 ;;   # 120000 or 120000ms
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
    case "$gh" in ""|*[!0-9]*) [ -z "$gh" ] || die "--gpu-hang needs a number of milliseconds, like 120000 (not '$gh')" ;; esac
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
            local cnt=0 last=""
            if [ -d "$save" ]; then
                cnt="$(find "$save" -type f | wc -l)"
                last="$(find "$save" -type f -printf '%TY-%Tm-%Td %TH:%TM\n' 2>/dev/null | sort | tail -1)"
            fi
            local i=0 b
            if [ "$TSV" = 1 ]; then
                printf 'save\t%s\t%s\n' "$cnt" "$last"
                while read -r b; do [ -n "$b" ] || continue; i=$((i+1)); printf 'copy\t%s\t%s\n' "$i" "$b"; done < <(list_backups "$e")
            else
                if [ -d "$save" ]; then say "Saves of $(ed_name "$e"): $cnt file(s), last change $last"
                else say "Saves of $(ed_name "$e"): none yet"; fi
                while read -r b; do [ -n "$b" ] || continue; i=$((i+1)); printf '  %2d) %s\n' "$i" "$b"; done < <(list_backups "$e")
                if [ "$i" -gt 0 ]; then say "Restore: launcher.sh saves restore <number|name> -e $e"; else say "No copies yet (launcher.sh saves backup)"; fi
            fi ;;
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

# ---- mods and updates ---------------------------------------------------------------------------------------------
# Same rules as the Windows launcher (docs of the port: updates/*.nsp are the inbox of patches, mods/<edition>/<name> are
# the layers the game reads, "_" or "." at the start = ignored). Nothing here reads a dump or prod.keys.
mods_of()  { echo "$ROOT/mods/$1"; }
upd_in()   { echo "$ROOT/updates"; }
layer_kind() {   # own main, update, or manual
    if [ -f "$1/requires_main.txt" ]; then echo "update (own main)"
    elif [ -f "$1/from_update.txt" ]; then echo "update"
    else echo "manual"; fi
}
lc() { tr 'A-Z' 'a-z'; }
trim_hex() { tr -d ' \r\n\t' | lc; }

# Layer folder names of an edition, in the order the game reads them (active ones, then the disabled ones).
layers_of() {
    local d; d="$(mods_of "$1")"; [ -d "$d" ] || return 0
    ( cd "$d" && for n in *; do [ -d "$n" ] && echo "$n"; done ) | LC_ALL=C sort -f
}

# Resolves a name given by the user (with or without the leading "_") to the folder name that exists.
resolve_layer() {
    local e="$1" n="$2" d; d="$(mods_of "$e")"
    case "$n" in */*|.|..|"") return 1 ;; esac
    if [ -d "$d/$n" ]; then echo "$n"; elif [ -d "$d/_$n" ]; then echo "_$n"; else return 1; fi
}

# 100-ns ticks of .NET (DateTime UTC) of a file's modification time: what the installer writes to source.txt.
dotnet_ticks() {
    local t sec ns; t="$(stat -c %.9Y -- "$1")" || return 1
    sec="${t%%.*}"; ns="${t##*.}"; ns=$((10#$ns))
    echo $(( sec * 10000000 + ns / 100 + 621355968000000000 ))
}

nsp_state() {   # pending | done | ignored for updates/<file>
    local f="$1" b n san d line
    b="$(basename -- "$f")"
    case "$b" in _*) echo ignored; return ;; esac
    n="${b%.*}"
    san="$(printf '%s' "$n" | LC_ALL=C sed 's/[^A-Za-z0-9._()\[\] +-]/_/g')"
    local want="$b|$(stat -c %s -- "$f")|$(dotnet_ticks "$f")"
    for d in tsuki tsuki_global; do
        line="$(head -1 "$ROOT/data/$d/updates/$san/source.txt" 2>/dev/null | tr -d '\r')"
        [ -n "$line" ] && [ "${line%|*}" = "$want" ] && { echo done; return; }
    done
    echo pending
}

nsp_files() { shopt -s nullglob nocaseglob; local f; for f in "$ROOT"/updates/*.nsp; do echo "$f"; done; shopt -u nullglob nocaseglob; }

# Lines "<level>|<message>" about what the next recompile of an edition will do.
mods_check() {
    local e="$1" d n m ms cur next="" first="" conflict="" shas="" f pending=0 bad=0
    d="$(mods_of "$e")"
    while read -r n; do
        [ -n "$n" ] || continue
        case "$n" in _*|.*) continue ;; esac
        if [ -f "$d/$n/requires_main.txt" ]; then
            m="$(trim_hex < "$d/$n/requires_main.txt")"
            if [ -z "$first" ]; then first="$m"; fi
            case " $shas " in *" $m "*) ;; *) shas="$shas $m" ;; esac
            conflict="$conflict $n"
        fi
    done < <(layers_of "$e")
    if [ "$(echo $shas | wc -w)" -gt 1 ]; then
        echo "error|two active updates need a different main ($(echo $conflict)): the recompile would fail; disable one"; bad=1
    fi
    while read -r f; do [ -n "$f" ] || continue; [ "$(nsp_state "$f")" = pending ] && pending=$((pending+1)); done < <(nsp_files)
    if [ "$pending" -gt 0 ]; then echo "warn|$pending file(s) in updates/ not processed yet (each recompile reads the ones of its own edition): recompile"; bad=1; fi
    ms="$ROOT/data/$(ed_data "$e")/analysis/main_source.txt"
    if [ ! -f "$ms" ]; then echo "info|$(ed_name "$e") has not been recompiled yet"; return 0; fi
    cur="$(trim_hex < "$ms")"
    if [ -n "$first" ]; then next="$first"
    elif [ -f "$ROOT/data/$(ed_data "$e")/exefs/main" ]; then next="$(sha256sum "$ROOT/data/$(ed_data "$e")/exefs/main" | cut -d' ' -f1 | lc)"; fi
    if [ -n "$next" ] && [ "$next" != "$cur" ]; then echo "warn|the main of the active mods/updates is not the one this build was made with: recompile before playing"; bad=1; fi
    [ "$bad" -eq 0 ] && echo "ok|nothing to recompile"
    return 0
}

cmd_mods() {
    local sub="${1:-}"; [ $# -gt 0 ] && shift
    parse_ed "$@"; set -- "${REST[@]+"${REST[@]}"}"
    [ "$EDITION" != both ] || die "mods needs one edition (-e japan or -e global)"
    local e="$EDITION" d; d="$(mods_of "$e")"
    case "$sub" in
        list|"")
            local n state note f any=0
            while read -r n; do
                [ -n "$n" ] || continue; any=1
                case "$n" in _*|.*) state=off ;; *) state=on ;; esac
                note="$(layer_kind "$d/$n")"; [ -d "$d/$n/romfs/shader" ] && note="$note, has shaders"
                if [ "$TSV" = 1 ]; then printf 'layer\t%s\t%s\t%s\n' "$n" "$state" "$note"
                else printf '  [%s] %-40s %s\n' "$([ "$state" = on ] && echo x || echo ' ')" "$n" "$note"; fi
            done < <(layers_of "$e")
            [ "$any" = 1 ] || { [ "$TSV" = 1 ] || say "  (no layers in mods/$e)"; }
            while read -r f; do
                [ -n "$f" ] || continue
                if [ "$TSV" = 1 ]; then printf 'nsp\t%s\t%s\t%s\n' "$(basename -- "$f")" "$(nsp_state "$f")" "$(stat -c %s -- "$f")"
                else printf '  update: %-40s %s\n' "$(basename -- "$f")" "$(nsp_state "$f")"; fi
            done < <(nsp_files)
            local lv msg
            while IFS='|' read -r lv msg; do
                [ -n "$lv" ] || continue
                if [ "$TSV" = 1 ]; then printf 'check\t%s\t%s\n' "$lv" "$msg"; else say "$lv: $msg"; fi
            done < <(mods_check "$e") ;;
        enable|disable)
            local n r new; n="${1:-}"; [ -n "$n" ] || die "which one? (launcher.sh mods list)"
            r="$(resolve_layer "$e" "$n")" || die "no such layer '$n' in mods/$e"
            running "$e" && die "$(ed_name "$e") is running: close it first"
            if [ "$sub" = enable ]; then
                case "$r" in _*) new="${r#_}" ;; *) say "Already active: $r"; return 0 ;; esac
            else
                case "$r" in _*) say "Already disabled: $r"; return 0 ;; *) new="_$r" ;; esac
            fi
            [ -e "$d/$new" ] && die "mods/$e/$new already exists"
            mv -- "$d/$r" "$d/$new" || die "could not rename it"
            say "$sub: $r -> $new   (recompile if it is an update or has shaders)" ;;
        add)
            local src="${1:-}"; [ -d "$src" ] || die "add needs the mod's FOLDER (launcher.sh mods add <folder>)"
            running "$e" && die "$(ed_name "$e") is running: close it first"
            src="$(readlink -f -- "$src")"
            local name; name="$(basename -- "$src")"
            case "$name" in .*|_*) name="${name#[._]}" ;; esac
            [ -n "$name" ] || die "cannot name the layer"
            [ -e "$d/$name" ] || [ -e "$d/_$name" ] && die "mods/$e/$name already exists"
            mkdir -p "$d" || die "cannot create mods/$e"
            local tmp="$d/.adding_$name"; [ -e "$tmp" ] && die "$tmp exists (an interrupted add): remove it by hand"
            if [ -d "$src/romfs" ]; then
                mv -- "$src" "$tmp" || die "could not move the folder"
                say "Moved the folder as a layer."
            else
                local deep; deep="$(find "$src" -maxdepth 5 -type d -name romfs 2>/dev/null | head -1)"
                [ -n "$deep" ] || die "no 'romfs' folder inside $src: nothing to add"
                mkdir -p "$tmp" && mv -- "$deep" "$tmp/romfs" || { rm -rf -- "$tmp"; die "could not move the romfs folder"; }
                say "Moved only its romfs folder (what else came with it stays in $src)."
            fi
            mv -- "$tmp" "$d/$name" || die "could not finish: the files are in $tmp"
            say "Added mods/$e/$name (it is read the next time the game starts; recompile only if it has shaders or an update)" ;;
        remove)
            local n r; n="${1:-}"; [ -n "$n" ] || die "which one? (launcher.sh mods list)"
            r="$(resolve_layer "$e" "$n")" || die "no such layer '$n' in mods/$e"
            running "$e" && die "$(ed_name "$e") is running: close it first"
            [ -f "$d/$r/from_update.txt" ] && say "It is the layer of an update: the next recompile deletes that update's data too."
            to_trash "$d/$r" || die "no trash tool (gio, trash-put): remove mods/$e/$r by hand"
            say "Moved to the trash: mods/$e/$r" ;;
        *) die "mods: list | enable <name> | disable <name> | add <folder> | remove <name>" ;;
    esac
}

to_trash() {
    if command -v gio >/dev/null 2>&1 && gio trash -- "$1" >/dev/null 2>&1; then return 0; fi
    if command -v trash-put >/dev/null 2>&1 && trash-put -- "$1" >/dev/null 2>&1; then return 0; fi
    return 1
}

cmd_updates() {
    local sub="${1:-}"; [ $# -gt 0 ] && shift
    parse_ed "$@"; set -- "${REST[@]+"${REST[@]}"}"
    local d; d="$ROOT/updates"
    case "$sub" in
        list|"")
            local f any=0
            while read -r f; do
                [ -n "$f" ] || continue; any=1
                if [ "$TSV" = 1 ]; then printf 'nsp\t%s\t%s\t%s\n' "$(basename -- "$f")" "$(nsp_state "$f")" "$(stat -c %s -- "$f")"
                else printf '  %-44s %s\n' "$(basename -- "$f")" "$(nsp_state "$f")"; fi
            done < <(nsp_files)
            [ "$any" = 1 ] || { [ "$TSV" = 1 ] || say "  (no .nsp in updates/)"; } ;;
        add)
            local f="${1:-}"; [ -f "$f" ] || die "add needs a .nsp FILE (launcher.sh updates add <file.nsp>)"
            case "${f,,}" in *.nsp) ;; *) die "it is not a .nsp" ;; esac
            [ "$(head -c4 -- "$f")" = PFS0 ] || die "it is not a valid .nsp (no PFS0 header)"
            local b; b="$(basename -- "$f")"
            [ -e "$d/$b" ] && die "updates/$b already exists"
            mkdir -p "$d" || die "cannot create updates/"
            mv -- "$f" "$d/$b.partial" && mv -- "$d/$b.partial" "$d/$b" || die "could not move it (a copy may be left as $d/$b.partial)"
            say "Moved to updates/$b: the next recompile of its edition processes it (launcher.sh recomp -e <edition>)" ;;
        remove)
            local n="${1:-}"; [ -n "$n" ] || die "which one? (launcher.sh updates list)"
            case "$n" in */*|.|..) die "invalid name" ;; esac
            [ -f "$d/$n" ] || die "no such file updates/$n"
            to_trash "$d/$n" || die "no trash tool (gio, trash-put): remove updates/$n by hand"
            say "Moved to the trash: updates/$n" ;;
        *) die "updates: list | add <file.nsp> | remove <file.nsp>" ;;
    esac
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

# controls / title / options: the logic is in controls.py (the same files as the Windows launcher, same rules)
run_controls() {   # run_controls <input|title|options> args... (the default verb is show)
    local area="$1"; shift
    parse_ed "$@"
    command -v python3 >/dev/null 2>&1 || die "python3 is needed for $area (it comes with install_requirements_linux.sh)"
    [ -f "$HERE/controls.py" ] || die "tools/linux/controls.py is missing"
    local a=(--root "$ROOT" --edition "$EDITION"); [ "$TSV" = 1 ] && a+=(--tsv)
    [ ${#REST[@]} -gt 0 ] || REST=(show)
    python3 -I "$HERE/controls.py" "${a[@]}" "$area" "${REST[@]}"
}
cmd_controls() { run_controls input "$@"; }
cmd_title() { run_controls title "$@"; }
cmd_options() { run_controls options "$@"; }

cmd_vulkan() {
    parse_ed "$@"
    declare -F vulkan_present >/dev/null || die "tools/linux/vulkan_check.sh is missing"
    local sub="${REST[0]:-show}" r cmd note ans
    case "$sub" in show|install) ;; *) die "usage: launcher.sh vulkan [show|install]" ;; esac
    if vulkan_present; then
        if [ "$TSV" = 1 ]; then printf 'vulkan\tyes\n'; else say "Vulkan: loader and driver found"; fi
        return 0
    fi
    r="$(vulkan_install_cmd)"; cmd="${r%%|*}"; note="${r#*|}"
    [ -n "$r" ] || { cmd=""; note="this distribution is not known: install the Vulkan loader and the driver of your graphics card with your package manager"; }
    if [ "$sub" = show ]; then
        if [ "$TSV" = 1 ]; then printf 'vulkan\tno\nvulkan_cmd\t%s\t%s\n' "$cmd" "$note"
        else say "Vulkan: no loader or no driver found (the game needs one to run)"; say "$note"; [ -z "$cmd" ] || say "command: $cmd"; fi
        return 0
    fi
    [ -n "$cmd" ] || die "$note"
    say "$note"; say "command: $cmd"
    if [ "${LAUNCHER_ASSUME_YES:-0}" = 1 ]; then ans=y
    elif [ -t 0 ]; then read -r -p "Run it now? [y/N]: " ans || ans=""
    else die "needs a terminal to ask (or LAUNCHER_ASSUME_YES=1); run the command yourself"; fi
    case "${ans,,}" in y|yes) ;; *) say "Not run."; return 0 ;; esac
    sh -c "$cmd" || die "the command failed"
    if vulkan_present; then say "Vulkan installed."; else say "Done, but no Vulkan driver is seen yet: log out and in, or check the driver of your card."; fi
}

cmd_desktop() {
    parse_ed "$@"
    command -v python3 >/dev/null 2>&1 || die "python3 is needed for desktop"
    [ -f "$HERE/make_desktop_entry.py" ] || die "tools/linux/make_desktop_entry.py is missing"
    case "${REST[0]:-add}" in
        add) python3 -I "$HERE/make_desktop_entry.py" ;;
        remove) python3 -I "$HERE/make_desktop_entry.py" --remove ;;
        *) die "usage: launcher.sh desktop [add|remove]" ;;
    esac
}

cmd_language() {
    if [ $# -eq 0 ]; then local v; v="$(ini_get language || true)"; say "${v:-auto}"; return 0; fi
    case "$1" in auto|en|es) ;; *) die "unknown language '$1' (auto, en, es)" ;; esac
    ini_set language "$1"; say "Language of the graphical launcher: $1"
}

cmd_gui() {
    # Order: tools/linux/bin/tsukihime_launcher (prebuilt, from the release zip), then the one built here
    # (framework/build/launcher/), else build it. "gui --build" skips the prebuilt one and compiles.
    local g="$ROOT/tools/linux/bin/tsukihime_launcher" b="$ROOT/framework/build/launcher/tsukihime_launcher"
    if [ "${1:-}" = "--build" ]; then shift; g=""; bash "$HERE/build_gui.sh" || die "could not build the graphical launcher"; fi
    if [ -n "$g" ] && [ -f "$g" ]; then
        [ -x "$g" ] || chmod +x "$g" 2>/dev/null || die "$g is not executable (the zip does not keep the permission): chmod +x it"
    else
        g="$b"; [ -x "$g" ] || bash "$HERE/build_gui.sh" || die "could not build the graphical launcher"
    fi
    exec "$g" --root "$ROOT" "$@"
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
    mods) cmd_mods "$@" ;;
    updates) cmd_updates "$@" ;;
    open) cmd_open "$@" ;;
    install) cmd_install "$@" ;;
    recomp) cmd_recomp "$@" ;;
    edition) cmd_edition "$@" ;;
    controls) cmd_controls "$@" ;;
    title) cmd_title "$@" ;;
    options) cmd_options "$@" ;;
    vulkan) cmd_vulkan "$@" ;;
    desktop) cmd_desktop "$@" ;;
    language) cmd_language "$@" ;;
    gui) cmd_gui "$@" ;;
    ""|-h|--help|help) usage ;;
    *) echo "unknown command '$cmd'" >&2; usage >&2; exit 1 ;;
esac
