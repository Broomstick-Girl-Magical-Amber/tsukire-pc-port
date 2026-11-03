r"""apply_unzip_dotnet.py - zips without the Microsoft.PowerShell.Archive module. Marker AP_UNZIP_DOTNET.

Measured 2026-11-03 (first Linux run of install_requirements_linux.sh -Recomp -Edition global, Ubuntu 26.04 VM,
PowerShell 7.6.7): step 5 in the background (the vcpkg worker, a second pwsh process) failed while unpacking the prebuilt
FFmpeg: "The 'Expand-Archive' command was found in the module 'Microsoft.PowerShell.Archive', but the module could not be
loaded". In the main process Expand-Archive had worked minutes before (Ninja zip, step 2). Not reproduced here (pwsh 7.5.4
and 7.6.7, with and without the environment of the script, also as a child process), so the cause is not confirmed. The
fix does not depend on it: Unzip (AP_INSTALL_IO) and the Linux tool installer unpack with .NET
[System.IO.Compression.ZipFile] directly (in PowerShell 7 and in Windows PowerShell 5.1, which loads the assembly with
Add-Type), so no module is imported. On Windows tar.exe still goes first, as before.

Run: cd <root>/framework; private/venv/bin/python ../tools/patches/apply_unzip_dotnet.py [--remove]
(Windows: private\venv\Scripts\python). Needs AP_INSTALL_IO (apply_install_io.py).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_UNZIP_DOTNET"
INSTALL = "../tools/install_requirements.ps1"
LINUX = "../tools/linux/install_requirements_linux.ps1"

EDITS = [
    (INSTALL,
     r"""        Warn "tar.exe could not unpack $(Split-Path -Leaf $zip) ($($r.Output | Select-Object -Last 1)): Expand-Archive"
        Remove-Item -LiteralPath $dst -Recurse -Force -ErrorAction SilentlyContinue
    }
    Expand-Archive -LiteralPath $zip -DestinationPath $dst -Force
}
""",
     r"""        Warn "tar.exe could not unpack $(Split-Path -Leaf $zip) ($($r.Output | Select-Object -Last 1)): .NET ZipFile"
        Remove-Item -LiteralPath $dst -Recurse -Force -ErrorAction SilentlyContinue
    }
    # AP_UNZIP_DOTNET: .NET directly instead of Expand-Archive (a background pwsh on Linux could not load the
    # Microsoft.PowerShell.Archive module, 2026-11-03). Overwrites like Expand-Archive -Force.
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
    $full = [IO.Path]::GetFullPath($dst)
    if ($PSVersionTable.PSVersion.Major -ge 6) { [IO.Compression.ZipFile]::ExtractToDirectory([IO.Path]::GetFullPath($zip), $full, $true); return }
    $za = [IO.Compression.ZipFile]::OpenRead([IO.Path]::GetFullPath($zip))   # Windows PowerShell 5.1: no overwrite overload
    try {
        foreach ($e in $za.Entries) {
            $out = [IO.Path]::GetFullPath((Join-Path $full $e.FullName))
            if (-not $out.StartsWith($full, [StringComparison]::OrdinalIgnoreCase)) { Fail "$(Split-Path -Leaf $zip): entry outside the folder ($($e.FullName))" }
            if ($e.FullName.EndsWith('/') -or $e.FullName.EndsWith('\')) { New-Item -ItemType Directory -Path $out -Force | Out-Null; continue }
            New-Item -ItemType Directory -Path (Split-Path -Parent $out) -Force | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $out, $true)
        }
    } finally { $za.Dispose() }
}
"""),
    (LINUX,
     r"""    if ($h.ContainsKey('Zip')) { Expand-Archive -LiteralPath $arch -DestinationPath $tmp }
""",
     r"""    if ($h.ContainsKey('Zip')) { Unzip $arch $tmp }   # AP_UNZIP_DOTNET: .NET ZipFile, no Archive module
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
