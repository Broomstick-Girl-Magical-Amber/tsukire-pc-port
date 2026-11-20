# preflight.sh - Checks of install_requirements_linux.sh and recomp_linux.sh that run BEFORE anything is installed
# (AP_LINUX_PREFLIGHT). Loaded with ". preflight.sh" after common.sh; it does not run on its own.
#
#   preflight_openssl   .NET (PowerShell 7) loads libssl at run time: it needs OpenSSL 3 (or 1.1). A system with only
#                       OpenSSL 4 (Fedora 45) makes it abort with "No usable version of libssl was found". Stops with
#                       what to install; with both 3 and 4 present, it asks .NET for 3 (CLR_OPENSSL_VERSION_OVERRIDE).
#   preflight_tmp       Removes what an interrupted run left in the tools folder ("<tool>.tmp"): the installer refuses
#                       to continue on them. Never while another install/recomp is running.

# Versions of libssl.so.<N> the dynamic loader knows (one per line, e.g. "3", "4", "1.1").
libssl_versions() {
    { ldconfig -p 2>/dev/null || /sbin/ldconfig -p 2>/dev/null || true; } \
        | sed -n 's/^[[:space:]]*libssl\.so\.\([0-9][0-9.]*\)[[:space:]].*/\1/p' | sort -u
}

preflight_openssl() {
    local v have3=0 have11=0 other=0
    while read -r v; do
        case "$v" in
            3|3.*) have3=1 ;;
            1.1|1.1.*|1.0*) have11=1 ;;
            "") ;;
            *) other=1 ;;
        esac
    done < <(libssl_versions)
    if [ "$have3" = 1 ] || [ "$have11" = 1 ]; then
        # OpenSSL 3 next to a newer one: ask .NET for 3 so it does not look for what it does not know
        if [ "$have3" = 1 ] && [ "$other" = 1 ] && [ -z "${CLR_OPENSSL_VERSION_OVERRIDE:-}" ]; then
            export CLR_OPENSSL_VERSION_OVERRIDE=3
            echo "   OpenSSL: libssl.so.3 and a newer libssl found: CLR_OPENSSL_VERSION_OVERRIDE=3 for this run"
        fi
        return 0
    fi
    if [ "$other" = 0 ] && [ -z "$(libssl_versions)" ]; then
        # the loader cache could not be read (no ldconfig): do not block what we cannot see
        return 0
    fi
    local id="" like="" hint="the OpenSSL 3 runtime library (libssl.so.3)"
    if [ -r /etc/os-release ]; then
        id="$(. /etc/os-release; echo "${ID:-}")"; like="$(. /etc/os-release; echo "${ID_LIKE:-}")"
    fi
    case " $id $like " in
        *" fedora "*|*" rhel "*) hint="sudo dnf install openssl3-libs   (provides /usr/lib64/libssl.so.3)" ;;
        *" debian "*|*" ubuntu "*) hint="sudo apt install libssl3" ;;
    esac
    echo "ERROR: PowerShell 7 (.NET) needs the OpenSSL 3 library (libssl.so.3) and this system has only a newer one."
    echo "       Install it: $hint"
    echo "       Then run this again (it sets CLR_OPENSSL_VERSION_OVERRIDE=3 by itself when both are present)."
    exit 1
}

preflight_tmp() {
    [ -n "${T:-}" ] && [ "$T" != / ] && [ -d "$T" ] || return 0
    local f d busy=0 left=()
    shopt -s nullglob
    for d in "$T"/*.tmp; do left+=("$d"); done
    shopt -u nullglob
    [ "${#left[@]}" -gt 0 ] || return 0
    for f in /proc/[0-9]*/cmdline; do
        if tr '\0' ' ' < "$f" 2>/dev/null | grep -qE 'install_requirements\.ps1|recomp_tsukihime\.ps1'; then busy=1; break; fi
    done
    if [ "$busy" = 1 ]; then
        echo "   leftovers of an interrupted run in $T were NOT removed: another install or recomp is running"
        return 0
    fi
    for d in "${left[@]}"; do rm -rf -- "$d"; done
    echo "   removed what an interrupted run left in $T: $(printf '%s ' "${left[@]##*/}")"
}
