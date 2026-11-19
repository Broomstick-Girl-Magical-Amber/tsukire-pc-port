r"""apply_linux_install.py - native Linux: the application menu entry after the build (AP_LINUX_INSTALL).

With -Target linux, step 9 of recomp_tsukihime.ps1 (and so install_requirements_linux.sh -Recomp) now ends by running
tools/linux/make_desktop_entry.py for the edition it built: ~/.local/share/applications/<game>.desktop and its icon.
That is the way to start the game without a terminal, and where GNOME on Wayland takes the window icon from. Running
it again only rewrites the same two files (and fixes the entry if the port folder moved). NX_DESKTOP_ENTRY=0 skips it;
`python3 tools/linux/make_desktop_entry.py --remove` takes the entries away. Windows and -Target windows do not change.
"""
from _patch import run

MARK = "AP_LINUX_INSTALL"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    (RECO,
     '''    HudAssets
}

function Step10 {
''',
     '''    HudAssets
}

# AP_LINUX_INSTALL (apply_linux_install.py): -Target linux: after step 9, the application menu entry and its icon
# (tools/linux/make_desktop_entry.py, in ~/.local/share): the way to start the game without a terminal, and where GNOME
# on Wayland takes the window icon from. NX_DESKTOP_ENTRY=0 skips it. Step 9 is wrapped, not edited.
if ($NativeLinux -and $env:NX_DESKTOP_ENTRY -ne '0') {
    $Step9Build = ${function:Step9}
    function Step9 {
        & $Step9Build
        $py = Get-Command python3 -ErrorAction SilentlyContinue
        $script = InRoot 'tools\\linux\\make_desktop_entry.py'
        if (-not $py -or -not (Test-Path -LiteralPath $script)) { Warn 'no python3 or make_desktop_entry.py: no application menu entry'; return }
        & $py.Source $script --edition $Edition | Out-Host
        if ($LASTEXITCODE -eq 0) { $Summary['Menu'] = "$($V.Game).desktop (application menu; NX_DESKTOP_ENTRY=0 skips it)" }
        else { Warn 'make_desktop_entry.py failed: the game still starts from framework/build' }
    }
}

function Step10 {
'''),
    (RECO,
     '''    @('global', 'tools\\internal\\regen_tsuki_global.ps1', 'AP_RELR', 'apply_relr.py', $true),
''',
     '''    @('global', 'tools\\internal\\regen_tsuki_global.ps1', 'AP_RELR', 'apply_relr.py', $true),
    @('both', 'tools\\recomp_tsukihime.ps1', 'AP_LINUX_INSTALL', 'apply_linux_install.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
