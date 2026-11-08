"""apply_recomp_hooks.py - recomp + runtime: native replacements (hooks) for recompiled functions reached by bl.

Why: NX_HLE only replaces functions reached through relocations (imports, GOT); a function called with a direct bl
inside the same module is a plain C++ call in the generated code (main_XXXXXXXX(c)) that nothing can intercept. The
title screen stall of Tsukihime (~1.6 s, profile of 2026-11-05) was mostly two such functions: main+0xa90c0 (a linear
file name lookup, ~52 %) and the sdk memcmp (~16 %).

What it does:
- recomp/__main__.py: NX_RECOMP_HOOKS (environment, during the recompile) lists functions to hook, separated by ';':
      <module>:<hook name>:<symbol>                     e.g. sdk:memcmp:memcmp
      <module>:<hook name>:0x<address>,0x<size>,0x<first instruction word>
  The second form is for stripped modules (main): the function must start at that address, have that size and
  that first word, or it is skipped with a message (so the same list cannot hook the wrong function in the other
  edition, whose regen script is a copy of this one). A hooked function is emitted as <name>_orig, plus a stub
  <name> that asks nx::hook_lookup(<hook name>) once and then calls fn(c, <name>_orig, guest address) or, if no
  hook is registered, <name>_orig. Without NX_RECOMP_HOOKS the output is the same as before.
- runtime/include/nx/runtime.h and runtime/src/loader.cpp: NX_HOOK(name, fn) registry and nx::hook_lookup (it logs
  "hook <name>: registered" the first time each stub runs).
- tools/internal/regen_tsuki.ps1: NX_RECOMP_HOOKS for Tsukihime (japan edition): main:tsuki_lookup at main+0xa90c0
  and sdk:memcmp. The hooks themselves are games/tsukihime_common/hle_hooks_tsuki.cpp (new file of the repo;
  NX_TSUKI_LOOKUP / NX_TSUKI_MEMCMP = 0 or check at run time).
- tools/recomp_tsukihime.ps1: rows in $Markers.

Idempotent (marker AP_RECOMP_HOOKS), all or nothing, keeps CRLF and BOM, --remove leaves the files byte for byte.
After: tools\\internal\\regen_tsuki.ps1 main, sdk   and   cmake --build build\\tsukihime
(the first build after the regen compiles all the generated code again: funcs.h changes).

Usage (in <root>\\framework):
  private\\venv\\Scripts\\python ..\\tools\\patches\\apply_recomp_hooks.py [--remove]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_RECOMP_HOOKS"
MAIN = "recomp/__main__.py"
RT_H = "runtime/include/nx/runtime.h"
LOADER = "runtime/src/loader.cpp"
REGEN = "../tools/internal/regen_tsuki.ps1"
RECOMP_PS = "../tools/recomp_tsukihime.ps1"

# Tsukihime (japan): main+0xa90c0 is 0x1278 bytes and starts with "sub sp, sp, #0xa0" (0xd10283ff).
HOOKS = "main:tsuki_lookup:0xa90c0,0x1278,0xd10283ff;sdk:memcmp:memcmp"

MAIN_IMPORT_OLD = "from . import analysis, emit, nso\n"
MAIN_IMPORT_NEW = MAIN_IMPORT_OLD + r'''

# AP_RECOMP_HOOKS (apply_recomp_hooks.py): NX_RECOMP_HOOKS="<module>:<hook>:<symbol>;<module>:<hook>:0xADDR,0xSIZE,0xWORD"
# emits each listed function as <name>_orig plus a stub <name> that calls the runtime hook (NX_HOOK) if there is one.
def resolve_hooks(m, an, module):
    """{function start: hook name} for this module. Entries whose function does not match are skipped (printed)."""
    hooks = {}
    starts = {f.start: f for f in an.functions}
    for item in os.environ.get("NX_RECOMP_HOOKS", "").split(";"):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) != 3 or not all(parts):
            raise SystemExit(f"NX_RECOMP_HOOKS: bad entry {item!r} (<module>:<hook>:<symbol or 0xADDR,0xSIZE,0xWORD>)")
        mod, hook, where = parts
        if mod != module:
            continue
        if where.startswith("0x"):
            vals = where.split(",")
            if len(vals) != 3:
                raise SystemExit(f"NX_RECOMP_HOOKS: bad entry {item!r} (0xADDR,0xSIZE,0xWORD)")
            addr, size, word = (int(v, 16) for v in vals)
            f = starts.get(addr)
            if f is None:
                print(f"hook {hook} skipped: no function starts at {module}+{addr:#x}")
                continue
            if f.end - f.start != size or m.u32(addr) != word:
                print(f"hook {hook} skipped: {module}+{addr:#x} is {f.end - f.start:#x} bytes, first word "
                      f"{m.u32(addr):#010x} (expected {size:#x}, {word:#010x}): another binary")
                continue
        else:
            found = {s.value for s in m.symbols if s.name == where and not s.imported and s.type == 2}
            if len(found) != 1 or next(iter(found)) not in starts:
                print(f"hook {hook} skipped: symbol {where} not found as one function of {module}")
                continue
            addr = next(iter(found))
        hooks[addr] = hook
        print(f"hook {hook}: {module}+{addr:#x}")
    return hooks


def hooked_function(e, f, hook):
    """The recompiled body renamed to <name>_orig, plus the stub <name>."""
    name = e.fn(f.start)
    text = e.function(f)
    head = f"void {name}(nx::CPU& NX_RESTRICT c) {{"
    if not text.startswith(head):
        raise SystemExit(f"hook {hook}: unexpected start of {name}")
    text = f"void {name}_orig(nx::CPU& NX_RESTRICT c) {{" + text[len(head):]
    return (text + "\n\n"
            f"// AP_RECOMP_HOOKS: hook \"{hook}\" (NX_HOOK in the runtime); {name}_orig is the recompiled body.\n"
            f"void {name}(nx::CPU& NX_RESTRICT c) {{\n"
            f"    static const nx::HookFn hook_ = nx::hook_lookup(\"{hook}\");\n"
            f"    if (hook_) hook_(c, {name}_orig, NXB + {f.start:#x}ull); else {name}_orig(c);\n"
            "}")
'''

MAIN_EMITTER_OLD = "    e = emit.Emitter(an, prefix=name)\n"
MAIN_EMITTER_NEW = MAIN_EMITTER_OLD + "    hooks = resolve_hooks(m, an, name)  # AP_RECOMP_HOOKS\n"

MAIN_DECL_OLD = (
    "        for f in an.functions:\n"
    "            h.write(f\"void {e.fn(f.start)}(nx::CPU& c);\\n\")\n")
MAIN_DECL_NEW = MAIN_DECL_OLD + (
    "        if hooks:  # AP_RECOMP_HOOKS: what the stubs use (the same declarations as nx/runtime.h)\n"
    "            h.write(\"\\nnamespace nx {\\nusing HookFn = void (*)(CPU& c, void (*orig)(CPU&), uint64_t entry);\\n\"\n"
    "                    \"HookFn hook_lookup(const char* name);\\n}  // namespace nx\\n\")\n"
    "            for a in sorted(hooks):\n"
    "                h.write(f\"void {e.fn(a)}_orig(nx::CPU& c);\\n\")\n")

MAIN_EMIT_OLD = "        cur.append(e.function(f))\n"
MAIN_EMIT_NEW = ("        cur.append(hooked_function(e, f, hooks[f.start]) if f.start in hooks else e.function(f))"
                 "  # AP_RECOMP_HOOKS\n")

RT_H_OLD = "HleFn hle_lookup(const char* name);\n"
RT_H_NEW = RT_H_OLD + r'''
// AP_RECOMP_HOOKS (apply_recomp_hooks.py): replacements for recompiled functions reached by direct calls (bl), which
// NX_HLE cannot intercept. The recompiler emits a function listed in NX_RECOMP_HOOKS as <name>_orig plus a stub that
// asks hook_lookup(hook) on its first call: then every call goes to fn(c, orig, entry) (orig = the recompiled body,
// entry = guest address of the function) or, with nothing registered, to orig.
using HookFn = void (*)(CPU& c, void (*orig)(CPU&), uint64_t entry);
struct HookRegistrar {
    HookRegistrar(const char* name, HookFn fn);
};
#define NX_HOOK(name, fn) static ::nx::HookRegistrar NX_HLE_CAT(hook_reg_, __LINE__)(name, fn)
HookFn hook_lookup(const char* name);  // nullptr if nothing registered that name (logged)
'''

LOADER_OLD = "const char* hle_current_name() { return t_missing_name; }\n"
LOADER_NEW = LOADER_OLD + r'''
// AP_RECOMP_HOOKS (apply_recomp_hooks.py): NX_HOOK registry, read once by each stub of the generated code.
static std::unordered_map<std::string, HookFn>& hook_registry() {
    static std::unordered_map<std::string, HookFn> r;
    return r;
}

HookRegistrar::HookRegistrar(const char* name, HookFn fn) { hook_registry()[name] = fn; }

HookFn hook_lookup(const char* name) {
    auto it = hook_registry().find(name);
    HookFn fn = it == hook_registry().end() ? nullptr : it->second;
    log("hook %s: %s", name, fn ? "registered" : "not registered (recompiled code)");
    return fn;
}
'''

REGEN_DOC_OLD = ("#                       none; the main of the updates of updates\\ does (main+999e0, crash \"call into the "
                 "middle\" main+99a2c)\n")
REGEN_DOC_NEW = REGEN_DOC_OLD + (
    "#   NX_RECOMP_HOOKS     functions emitted as <name>_orig + a stub to a native hook (apply_recomp_hooks.py, AP_RECOMP_HOOKS;\n"
    "#                       hooks in games\\tsukihime_common\\hle_hooks_tsuki.cpp): main+0xa90c0 (file name lookup, checked\n"
    "#                       by size and first word) and the sdk memcmp\n")

REGEN_CHECK_OLD = ("    throw 'recomp\\analysis.py without apply_jt_mov.py: apply it first'\n"
                   "}\n")
REGEN_CHECK_NEW = REGEN_CHECK_OLD + (
    "# AP_RECOMP_HOOKS: native hooks for hot functions\n"
    "if (-not (Select-String -Path (Join-Path $Framework 'recomp\\__main__.py') -Pattern 'AP_RECOMP_HOOKS' -Quiet)) {\n"
    "    throw 'recomp\\__main__.py without apply_recomp_hooks.py: apply it first'\n"
    "}\n")

REGEN_SET_OLD = "$env:NX_RECOMP_JT_MOV = '1'\n"
REGEN_SET_NEW = REGEN_SET_OLD + (
    "$prevHooks = $env:NX_RECOMP_HOOKS  # AP_RECOMP_HOOKS\n"
    "$env:NX_RECOMP_HOOKS = '%s'\n" % HOOKS)

REGEN_RESTORE_OLD = ("    if ($null -eq $prevJtMov) { Remove-Item Env:NX_RECOMP_JT_MOV -ErrorAction SilentlyContinue } "
                     "else { $env:NX_RECOMP_JT_MOV = $prevJtMov }\n")
REGEN_RESTORE_NEW = REGEN_RESTORE_OLD + (
    "    if ($null -eq $prevHooks) { Remove-Item Env:NX_RECOMP_HOOKS -ErrorAction SilentlyContinue } "
    "else { $env:NX_RECOMP_HOOKS = $prevHooks }  # AP_RECOMP_HOOKS\n")

PS_OLD = "    @('both', 'framework\\runtime\\src\\svc_am.cpp', 'AP_LANG_LOG', 'apply_lang_log.py', $false),\n"
PS_NEW = PS_OLD + (
    "    @('both', 'framework\\recomp\\__main__.py', 'AP_RECOMP_HOOKS', 'apply_recomp_hooks.py', $false),\n"
    "    @('both', 'framework\\runtime\\include\\nx\\runtime.h', 'AP_RECOMP_HOOKS', 'apply_recomp_hooks.py', $false),\n"
    "    @('both', 'framework\\runtime\\src\\loader.cpp', 'AP_RECOMP_HOOKS', 'apply_recomp_hooks.py', $false),\n"
    "    @('japan', 'tools\\internal\\regen_tsuki.ps1', 'AP_RECOMP_HOOKS', 'apply_recomp_hooks.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\hle_hooks_tsuki.cpp', 'AP_RECOMP_HOOKS', "
    "'hle_hooks_tsuki.cpp (in the repo)', $false),\n")

EDITS = [
    (MAIN, MAIN_IMPORT_OLD, MAIN_IMPORT_NEW),
    (MAIN, MAIN_EMITTER_OLD, MAIN_EMITTER_NEW),
    (MAIN, MAIN_DECL_OLD, MAIN_DECL_NEW),
    (MAIN, MAIN_EMIT_OLD, MAIN_EMIT_NEW),
    (RT_H, RT_H_OLD, RT_H_NEW),
    (LOADER, LOADER_OLD, LOADER_NEW),
    (REGEN, REGEN_DOC_OLD, REGEN_DOC_NEW),
    (REGEN, REGEN_CHECK_OLD, REGEN_CHECK_NEW),
    (REGEN, REGEN_SET_OLD, REGEN_SET_NEW),
    (REGEN, REGEN_RESTORE_OLD, REGEN_RESTORE_NEW),
    (RECOMP_PS, PS_OLD, PS_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)