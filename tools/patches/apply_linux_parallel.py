r"""apply_linux_parallel.py - Linux step 2 in parallel and lighter. Marker AP_LINUX_PARALLEL.

Measured 2026-11-03 (first Linux run with -Recomp, Ubuntu 26.04 VM, 8 cores): step 2 took 7:48, one tool after the other:
download LLVM (2.0 GB .tar.xz), decompress it with one core and write 11.8 GB, then CMake, Ninja, xwin, Python, .NET,
Vulkan SDK, then the xwin splat. This patch (tools/linux/install_requirements_linux.ps1 only):
- 3 processes at once (-Chain llvm | crt | misc, like step 1 on Windows): LLVM; xwin + CRT/SDK splat; Python, CMake,
  Ninja, .NET, Vulkan SDK. The Microsoft license is asked once in the main window before they start (as on Windows),
  then XWIN_ACCEPT_LICENSE=true for that run only. The case overlay (xwin_vfs.py) runs after them, as before.
  NX_SERIAL_INSTALL=1: one after the other, in the same process.
- Each tarball is unpacked while it downloads (curl | tee | decompressor | tar) into <dir>.tmp, which only becomes the
  tool folder after the downloaded file matches its fixed hash (deleted otherwise). If the stream fails it downloads
  first and unpacks afterwards, as before. NX_NO_STREAM=1: always as before.
- xz -T0: the LLVM tarball has 494 xz blocks, so it decompresses with every core (2 cores measured here: download +
  decompression 3:19 -> 1:38).
- LLVM without the tools nobody here uses (flang, MLIR, BOLT, clangd, clang-tidy, lldb, opt/llc, LTO tools, static
  libraries, LLVM C++ headers): 11.8 GB -> 1.9 GB on disk. clang-cl, lld-link, llvm-lib/ar/ranlib, llvm-rc, llvm-mt,
  clang-scan-deps, llvm-objdump/nm/readobj and lib/clang (the builtin headers) stay. A full LLVM installed before stays
  valid (same pin, same .hash).
- The downloaded archives are deleted once installed (LLVM: 2 GB). Space estimate of the tools 25 GB -> 10 GB.
- No progress bars in the logs of the parallel processes (curl -sS; xwin progress lines filtered).
Not tested on Linux by Claude end to end (no 8-core VM here): parser, ASCII, apply/remove; the stream + exclusion
pipeline with the real LLVM 23.1.3 tarball (SHA-256 OK, 1.9 GB kept, clang-cl/lld-link/llvm-*/lib/clang present) and the
chains with stubs.

Run: cd <root>/framework; private/venv/bin/python ../tools/patches/apply_linux_parallel.py [--remove]
Needs AP_UNZIP_DOTNET (apply_unzip_dotnet.py) and AP_DROP_UNUSED.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_LINUX_PARALLEL"
LINUX = "../tools/linux/install_requirements_linux.ps1"

EDITS = [
    (LINUX,
     r"""#   2 user           LLVM, CMake, Ninja, xwin, Python 3.12, .NET SDK 10, Vulkan SDK (spirv-val) in NX_TOOLS_DIR;
#                    Windows CRT/SDK with xwin (-XwinCrt/-XwinSdk) and its case overlay (xwin_vfs.py)
""",
     r"""#   2 user           LLVM, CMake, Ninja, xwin, Python 3.12, .NET SDK 10, Vulkan SDK (spirv-val) in NX_TOOLS_DIR;
#                    Windows CRT/SDK with xwin (-XwinCrt/-XwinSdk) and its case overlay (xwin_vfs.py)
#                    AP_LINUX_PARALLEL: 3 processes at once (llvm | xwin + CRT/SDK | the rest), each tarball unpacked while
#                    it downloads (xz -T0), LLVM without the tools this port does not use (1.9 GB instead of 11.8 GB)
"""),
    (LINUX,
     r"""                Sha = 'CA9FE385A5D9E2D88132DC0E23A00E9F87F38FA75F67207B5E457D208E71D013'; Probe = 'bin/clang-cl' }
""",
     r"""                Sha = 'CA9FE385A5D9E2D88132DC0E23A00E9F87F38FA75F67207B5E457D208E71D013'; Probe = 'bin/clang-cl'
                Top = 'LLVM-23.1.3-Linux-X64'; Exclude = @('bin/flang*', 'bin/mlir*', 'bin/bbc', 'bin/tco', 'bin/fir-*', 'bin/f18-*', 'bin/clang-repl', 'bin/clang-check', 'bin/clang-tidy', 'bin/clangd', 'bin/clang-query', 'bin/clang-refactor', 'bin/clang-change-namespace', 'bin/clang-move', 'bin/clang-include-*', 'bin/c-index-test', 'bin/find-all-symbols', 'bin/clang-reorder-fields', 'bin/clang-doc', 'bin/modularize', 'bin/pp-trace', 'bin/clang-extdef-mapping', 'bin/clang-installapi', 'bin/clang-ssaf-*', 'bin/clang-linker-wrapper', 'bin/clang-nvlink-wrapper', 'bin/clang-sycl-linker', 'bin/opt', 'bin/llc', 'bin/llvm-lto*', 'bin/llvm-reduce', 'bin/llvm-ir2vec', 'bin/llvm-dwarfutil', 'bin/dsymutil', 'bin/llvm-c-test', 'bin/lldb*', 'bin/llvm-split', 'bin/llvm-gsymutil', 'bin/llvm-exegesis', 'bin/llvm-bolt*', 'bin/perf2bolt', 'bin/llvm-jitlink', 'bin/lli', 'bin/llvm-cfi-verify', 'bin/llvm-debuginfo-analyzer', 'bin/llvm-profgen', 'bin/llvm-mca', 'bin/sancov', 'bin/llvm-rtdyld', 'lib/*.a', 'lib/liblldb*', 'include/*') }   # AP_LINUX_PARALLEL
"""),
    (LINUX,
     r"""$SpaceT = 25GB   # LLVM extracted 12 GB + .NET + Vulkan SDK + xwin + downloads
""",
     r"""$SpaceT = 10GB   # AP_LINUX_PARALLEL: LLVM 1.9 GB (+ its 2 GB archive while installing) + .NET + Vulkan SDK + xwin
"""),
    (LINUX,
     r"""    Run 'curl' @('-fL', '--retry', '3', '--progress-bar', '-o', $tmp, $url) "download of $url"
""",
     r"""    Run 'curl' @('-fL', '--retry', '3', $(if ($Chain) { '-sS' } else { '--progress-bar' }), '-o', $tmp, $url) "download of $url"   # AP_LINUX_PARALLEL: no bars in the logs of the parallel processes
"""),
    (LINUX,
     r"""function InstallTar([string]$k) {""",
     r"""# AP_LINUX_PARALLEL: tarballs are decompressed with xz -T0 (the LLVM one has 494 blocks: all the cores; 2 cores measured
# 3:19 -> 1:38 for download + decompression) and the LLVM tools nobody here uses are not written (flang, MLIR, BOLT,
# clangd, lldb, static libraries, C++ headers of LLVM: 11.8 GB -> 1.9 GB on disk; clang-cl, lld-link, llvm-lib/ar/rc/mt,
# clang-scan-deps, llvm-objdump and lib/clang stay).
$StreamScript = 'set -o pipefail; url=$1; out=$2; dec=$3; shift 3; curl -fsSL "$url" | tee "$out" | $dec | tar -xf - "$@"'
$UnpackScript = 'set -o pipefail; arch=$1; dec=$2; shift 2; $dec < "$arch" | tar -xf - "$@"'
function Decompressor([string]$file) {
    if ($file -like '*.xz') { return 'xz -dc -T0' }
    if ($file -like '*.gz' -or $file -like '*.tgz') { return 'gzip -dc' }
    return 'cat'
}
function TarArgs($h, [string]$tmp) {
    $a = @('-C', $tmp)
    if ($h.Strip -gt 0) { $a += "--strip-components=$($h.Strip)" }
    if ($h.ContainsKey('Exclude')) { $a += '--anchored'; foreach ($x in $h.Exclude) { $a += "--exclude=$($h.Top)/$x" } }
    return , $a
}
function InstallTar([string]$k) {"""),
    (LINUX,
     r"""    $arch = Join-Path $dl $o.File
    $ok = (Test-Path -LiteralPath $arch) -and ((Get-FileHash -Algorithm $o.Algorithm -LiteralPath $arch).Hash -eq $o.Hash)
    if (-not $ok) {
        if (Test-Path -LiteralPath $arch) { Fail "$arch exists with another $($o.Algorithm): set it aside by hand" }
        DownloadCurl $o.Url $arch
        $got = (Get-FileHash -Algorithm $o.Algorithm -LiteralPath $arch).Hash
        if ($got -ne $o.Hash) {
            Move-Item -LiteralPath $arch -Destination "$arch.hash-mismatch" -Force
            Fail "$($o.File): $($o.Algorithm) $got, expected $($o.Hash) (kept as $arch.hash-mismatch)"
        }
    }
    Info "$($o.File): $($o.Algorithm) OK"
    $tmp = "$dst.tmp"
    if (Test-Path -LiteralPath $tmp) { Fail "$tmp exists (from an interrupted run): set it aside by hand" }
    New-Item -ItemType Directory -Path $tmp | Out-Null
    if ($h.ContainsKey('Zip')) { Unzip $arch $tmp }   # AP_UNZIP_DOTNET: .NET ZipFile, no Archive module
    else {
        $a = @('-xf', $arch, '-C', $tmp)
        if ($h.Strip -gt 0) { $a += "--strip-components=$($h.Strip)" }
        Run 'tar' $a "tar ($($o.File))"
    }
    $p = Join-Path $tmp $h.Probe
    if (-not (Test-Path -LiteralPath $p)) { Fail "$($o.File) does not have $($h.Probe) (left in ${tmp}: check it)" }
    if ($h.ContainsKey('Zip')) { Run 'chmod' @('+x', $p) 'chmod' }
    [IO.File]::WriteAllText((Join-Path $tmp '.hash'), "$($o.Algorithm) $($o.Hash) $($o.File)`n")
    Move-Item -LiteralPath $tmp -Destination $dst
    Info "$($h.Name) $($h.Pin) in $dst"
""",
     r"""    $arch = Join-Path $dl $o.File
    $tmp = "$dst.tmp"
    if (Test-Path -LiteralPath $tmp) { Fail "$tmp exists (from an interrupted run): set it aside by hand" }
    $ok = (Test-Path -LiteralPath $arch) -and ((Get-FileHash -Algorithm $o.Algorithm -LiteralPath $arch).Hash -eq $o.Hash)
    $streamed = $false
    if (-not $ok) {
        if (Test-Path -LiteralPath $arch) { Fail "$arch exists with another $($o.Algorithm): set it aside by hand" }
        # AP_LINUX_PARALLEL: a tarball is unpacked WHILE it downloads (curl | tee | xz -T0 | tar) into $tmp, which only
        # becomes $dst after the downloaded bytes match the hash; on a mismatch $tmp is deleted. If the stream fails
        # (network), it downloads first and unpacks afterwards, as before. NX_NO_STREAM=1: always as before.
        if (-not $h.ContainsKey('Zip') -and -not $env:NX_NO_STREAM) {
            New-Item -ItemType Directory -Path $tmp | Out-Null
            Info "downloading and unpacking at the same time: $($o.Url)"
            $r = RunCapture 'bash' (@('-c', $StreamScript, 'stream', $o.Url, "$arch.partial", (Decompressor $o.File)) + (TarArgs $h $tmp))
            if ($r.Code -eq 0) { Move-Item -LiteralPath "$arch.partial" -Destination $arch -Force; $streamed = $true }
            else {
                Warn "the download + unpack stream failed ($(($r.Output | Select-Object -Last 2) -join ' | ')): downloading first, then unpacking"
                Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
                Remove-Item -LiteralPath "$arch.partial" -Force -ErrorAction SilentlyContinue
            }
        }
        if (-not $streamed) { DownloadCurl $o.Url $arch }
        $got = (Get-FileHash -Algorithm $o.Algorithm -LiteralPath $arch).Hash
        if ($got -ne $o.Hash) {
            if ($streamed) { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
            Move-Item -LiteralPath $arch -Destination "$arch.hash-mismatch" -Force
            Fail "$($o.File): $($o.Algorithm) $got, expected $($o.Hash) (kept as $arch.hash-mismatch; nothing installed)"
        }
    }
    Info "$($o.File): $($o.Algorithm) OK$(if ($streamed) { ' (unpacked while downloading)' })"
    if (-not $streamed) {
        New-Item -ItemType Directory -Path $tmp | Out-Null
        if ($h.ContainsKey('Zip')) { Unzip $arch $tmp }   # AP_UNZIP_DOTNET: .NET ZipFile, no Archive module
        else { Run 'bash' (@('-c', $UnpackScript, 'unpack', $arch, (Decompressor $o.File)) + (TarArgs $h $tmp)) "tar ($($o.File))" }
    }
    $p = Join-Path $tmp $h.Probe
    if (-not (Test-Path -LiteralPath $p)) { Fail "$($o.File) does not have $($h.Probe) (left in ${tmp}: check it)" }
    if ($h.ContainsKey('Zip')) { Run 'chmod' @('+x', $p) 'chmod' }
    [IO.File]::WriteAllText((Join-Path $tmp '.hash'), "$($o.Algorithm) $($o.Hash) $($o.File)`n")
    Move-Item -LiteralPath $tmp -Destination $dst
    Remove-Item -LiteralPath $arch -Force -ErrorAction SilentlyContinue   # AP_LINUX_PARALLEL: installed: the archive is not needed (LLVM: 2 GB)
    Info "$($h.Name) $($h.Pin) in $dst"
"""),
    (LINUX,
     r"""function Step2 {
    New-Item -ItemType Directory -Path $script:T -Force -ErrorAction SilentlyContinue | Out-Null
    foreach ($k in $Tarballs.Keys) {
        $e = Status $k
        if ($e.State -eq 'ok') { Info ('{0,-36} {1} OK ({2})' -f $Tarballs[$k].Name, $e.Det.Text, $e.Det.Path); continue }
        if ($Check) { Problem "$($Tarballs[$k].Name) $($Tarballs[$k].Pin): not present in $(TarDir $k)"; continue }
        InstallTar $k
    }
    RefreshEnv
    # MSVC CRT/STL and Windows SDK (xwin). You accept the Microsoft license: xwin asks (or XWIN_ACCEPT_LICENSE=true
    # that you set in the console); this script never accepts it for you.
    $xw = XwinDir
    if (XwinOk) { Info "Windows CRT/SDK in $xw ($(XwinRequest))" }
    elseif ($Check) { Problem "the xwin Windows CRT/SDK is missing in $xw ($(XwinRequest))" }
    else {
        if (Test-Path -LiteralPath $xw) { Fail "$xw exists and is from another request (now: $(XwinRequest)): set it aside by hand" }
        $xb = Join-Path (TarDir 'xwin') 'xwin'
        if (-not (Test-Path -LiteralPath $xb)) { Fail 'xwin is missing (step 2, tarballs)' }
        if ($env:XWIN_ACCEPT_LICENSE -ne 'true') {
            if ($NonInteractive) { Fail 'xwin asks you to accept the Microsoft license (https://go.microsoft.com/fwlink/?LinkId=2086102): with -NonInteractive set XWIN_ACCEPT_LICENSE=true yourself' }
            Info 'xwin downloads the CRT and the SDK from the Microsoft servers and asks whether you accept their license (answer it yourself: yes / no):'
            Info '  https://go.microsoft.com/fwlink/?LinkId=2086102'
        }
        $tmp = "$xw.tmp"
        if (Test-Path -LiteralPath $tmp) { Fail "$tmp exists (from an interrupted run): set it aside by hand" }
        $xa = @('--manifest-version', $XwinManifest, '--cache-dir', (Join-Path $script:T 'xwin-cache'), '--http-retry', '3', '--crt-version', $XwinCrt, '--sdk-version', $XwinSdk,
                'splat', '--use-winsysroot-style', '--preserve-ms-arch-notation', '--output', $tmp)
        & $xb @xa   # not captured: the license question has to be visible
        if ($LASTEXITCODE -ne 0) { Fail "xwin failed (code $LASTEXITCODE; or you did not accept the license). If $tmp was left, set it aside by hand before retrying" }
        $crt = @(Get-ChildItem -LiteralPath (Join-Path $tmp 'VC/Tools/MSVC') -Directory)[0].Name
        $sdk = @(Get-ChildItem -LiteralPath (Join-Path $tmp 'Windows Kits/10/Include') -Directory)[0].Name
        $stl = (Select-String -LiteralPath (Join-Path $tmp "VC/Tools/MSVC/$crt/include/yvals_core.h") -Pattern '#define _MSVC_STL_UPDATE\s+(\S+)' | Select-Object -First 1)
        $stlTxt = if ($stl) { $stl.Matches[0].Groups[1].Value } else { '?' }
        [IO.File]::WriteAllText((Join-Path $tmp 'VERSIONS.txt'), "request: $(XwinRequest)`ncrt: $crt`nsdk: $sdk`nstl: $stlTxt`nxwin: $($Tarballs.xwin.Pin)`ndate: $(Get-Date -Format s)`n")
        Move-Item -LiteralPath $tmp -Destination $xw
        Info "CRT $crt (_MSVC_STL_UPDATE $stlTxt), SDK $sdk in $xw"
        $script:Done.Add("Windows CRT/SDK (xwin, CRT $crt, SDK $sdk)")
    }
    if (XwinOk) {
        $crt = @(Get-ChildItem -LiteralPath (Join-Path $xw 'VC/Tools/MSVC') -Directory)[0].Name
        if (-not (Test-Path -LiteralPath (Join-Path $xw "VC/Tools/MSVC/$crt/lib/x64/delayimp.lib"))) { Warn 'the xwin CRT does not have delayimp.lib: /DELAYLOAD:SDL3.dll will fail when linking the game' }
        # Case overlay for the #includes and links for the .rc files (derived from xwin; always redone).
        if (-not $Check) {
            $py = Join-Path (TarDir 'python') 'bin/python3'
            if (-not (Test-Path -LiteralPath $py)) { $py = 'python3' }
            Run $py @('-I', (Join-Path $Root 'tools/linux/xwin_vfs.py'), $xw) 'xwin_vfs.py'
        } elseif (-not (Test-Path -LiteralPath "$xw-vfs.yaml")) { Problem "$xw-vfs.yaml is missing (case overlay)" }
    }
}
""",
     r"""# AP_LINUX_PARALLEL: step 2 in parallel, like step 1 on Windows (AP_TOOLCHAIN_XWIN): 3 processes of install_requirements.ps1
# (-Chain llvm | crt | misc). Measured 2026-11-03 (VM, one after the other): 7:48. NX_SERIAL_INSTALL=1: one after the other.
$LinuxChains = [ordered]@{
    llvm = { TarIfMissing 'llvm' }
    crt  = { TarIfMissing 'xwin'; XwinSplatLinux }
    misc = { foreach ($k in 'python', 'cmake', 'ninja', 'dotnet', 'vulkan') { TarIfMissing $k } }
}
function TarIfMissing([string]$k) {
    $e = Status $k
    if ($e.State -eq 'ok') { Info ('{0,-36} {1} OK ({2})' -f $Tarballs[$k].Name, $e.Det.Text, $e.Det.Path); return }
    InstallTar $k
}
function ChainPending([string]$name) {
    switch ($name) {
        'llvm' { return ((Status 'llvm').State -ne 'ok') }
        'crt' { return ((Status 'xwin').State -ne 'ok' -or -not (XwinOk)) }
        'misc' { foreach ($k in 'python', 'cmake', 'ninja', 'dotnet', 'vulkan') { if ((Status $k).State -ne 'ok') { return $true } }; return $false }
    }
    return $false
}
function RunChain([string]$name) {
    if (-not $LinuxChains.Contains($name)) { Write-Host "ERROR: unknown chain $name"; return 1 }
    New-Item -ItemType Directory -Path $script:T -Force | Out-Null
    RefreshEnv
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try { & $LinuxChains[$name] | Out-Host; Write-Host ('   (chain {0}: {1:mm\:ss\.f})' -f $name, $sw.Elapsed); return 0 }   # Out-Host: nothing leaks into the exit code
    catch { Write-Host "ERROR: $($_.Exception.Message)"; return 1 }
}
# MSVC CRT/STL and Windows SDK (xwin). The license was accepted in step 2 (main process): XWIN_ACCEPT_LICENSE=true.
function XwinSplatLinux {
    $xw = XwinDir
    if (XwinOk) { Info "Windows CRT/SDK in $xw ($(XwinRequest))"; return }
    if ($env:XWIN_ACCEPT_LICENSE -ne 'true') { Fail 'the Microsoft license of the CRT/SDK was not accepted (https://go.microsoft.com/fwlink/?LinkId=2086102)' }
    $xb = Join-Path (TarDir 'xwin') 'xwin'
    if (-not (Test-Path -LiteralPath $xb)) { Fail 'xwin is missing (step 2, tarballs)' }
    if (Test-Path -LiteralPath $xw) { Fail "$xw exists and is from another request (now: $(XwinRequest)): set it aside by hand" }
    $tmp = "$xw.tmp"
    if (Test-Path -LiteralPath $tmp) { Fail "$tmp exists (from an interrupted run): set it aside by hand" }
    $xa = @('--manifest-version', $XwinManifest, '--cache-dir', (Join-Path $script:T 'xwin-cache'), '--http-retry', '3', '--crt-version', $XwinCrt, '--sdk-version', $XwinSdk,
            'splat', '--use-winsysroot-style', '--preserve-ms-arch-notation', '--output', $tmp)
    $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'   # AP_LINUX_PARALLEL: license already accepted; no progress bars in the log
    try { & $xb @xa 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ -notmatch '\[[=> -]*\]|^\s*$|^System\.Management\.Automation\.RemoteException$' } | Out-Host } finally { $ErrorActionPreference = $prevEap }
    if ($LASTEXITCODE -ne 0) { Fail "xwin failed (code $LASTEXITCODE; or you did not accept the license). If $tmp was left, set it aside by hand before retrying" }
    $crt = @(Get-ChildItem -LiteralPath (Join-Path $tmp 'VC/Tools/MSVC') -Directory)[0].Name
    $sdk = @(Get-ChildItem -LiteralPath (Join-Path $tmp 'Windows Kits/10/Include') -Directory)[0].Name
    $stl = (Select-String -LiteralPath (Join-Path $tmp "VC/Tools/MSVC/$crt/include/yvals_core.h") -Pattern '#define _MSVC_STL_UPDATE\s+(\S+)' | Select-Object -First 1)
    $stlTxt = if ($stl) { $stl.Matches[0].Groups[1].Value } else { '?' }
    [IO.File]::WriteAllText((Join-Path $tmp 'VERSIONS.txt'), "request: $(XwinRequest)`ncrt: $crt`nsdk: $sdk`nstl: $stlTxt`nxwin: $($Tarballs.xwin.Pin)`ndate: $(Get-Date -Format s)`n")
    Move-Item -LiteralPath $tmp -Destination $xw
    Info "CRT $crt (_MSVC_STL_UPDATE $stlTxt), SDK $sdk in $xw"
    Remove-Item -LiteralPath (Join-Path $script:T 'xwin-cache') -Recurse -Force -ErrorAction SilentlyContinue   # the downloaded packages (~1 GB): not needed after splat
}

function Step2 {
    New-Item -ItemType Directory -Path $script:T -Force -ErrorAction SilentlyContinue | Out-Null
    if ($Check) {
        foreach ($k in $Tarballs.Keys) {
            $e = Status $k
            if ($e.State -eq 'ok') { Info ('{0,-36} {1} OK ({2})' -f $Tarballs[$k].Name, $e.Det.Text, $e.Det.Path) }
            else { Problem "$($Tarballs[$k].Name) $($Tarballs[$k].Pin): not present in $(TarDir $k)" }
        }
        if (-not (XwinOk)) { Problem "the xwin Windows CRT/SDK is missing in $(XwinDir) ($(XwinRequest))" }
    } else {
        $todo = @($LinuxChains.Keys | Where-Object { ChainPending $_ })
        if ($todo -contains 'crt' -and -not (XwinOk) -and $env:XWIN_ACCEPT_LICENSE -ne 'true') {
            Info 'xwin downloads the MSVC CRT/STL and the Windows SDK from the Microsoft servers. Their license:'
            Info '  https://go.microsoft.com/fwlink/?LinkId=2086102'
            if ($NonInteractive) { Fail 'the Microsoft license of the CRT/SDK has to be accepted: with -NonInteractive set XWIN_ACCEPT_LICENSE=true yourself' }
            if (-not (Ask 'Do you accept the Microsoft license of the CRT/SDK (the one xwin shows; the same Visual Studio asks for)?')) {
                Fail 'license not accepted: without the CRT/SDK the game cannot be built'
            }
            $env:XWIN_ACCEPT_LICENSE = 'true'   # only for this run and the processes it starts
        }
        if (-not $todo.Count) { Info 'everything already in place' }
        elseif ($env:NX_SERIAL_INSTALL) { foreach ($c in $todo) { & $LinuxChains[$c] } }
        else {
            $main = Join-Path (Split-Path -Parent $PSScriptRoot) 'install_requirements.ps1'   # $PSCommandPath here would be this file
            $runs = @()
            foreach ($c in $todo) {
                $out = Join-Path $WorkDir "chain_${c}_$(Stamp).log"
                $a = @('-NoProfile', '-File', $main, '-Chain', $c, '-NonInteractive', '-Root', $Root, '-Edition', $Edition,
                       '-From', "$From", '-To', "$To", '-XwinCrt', $XwinCrt, '-XwinSdk', $XwinSdk)
                $p = Start-Process -FilePath (Get-Process -Id $PID).Path -ArgumentList (($a | ForEach-Object { Quote $_ }) -join ' ') -NoNewWindow -PassThru `
                     -RedirectStandardOutput $out -RedirectStandardError "$out.err"
                $null = $p.Handle
                $runs += @{ C = $c; P = $p; Out = $out }
            }
            Info "in parallel: $($todo -join ', ') (the output of each one is shown when it finishes)"
            $failed = @()
            foreach ($r in $runs) {
                $r.P.WaitForExit()
                $code = -1; try { $code = $r.P.ExitCode } catch { }
                if ($null -eq $code) { $code = -1 }
                Write-Host "   -- $($r.C)$(if ($code -ne 0) { ' FAILED' })" -ForegroundColor $(if ($code -ne 0) { 'Red' } else { 'Gray' })
                foreach ($f in @($r.Out, "$($r.Out).err")) {
                    if (Test-Path -LiteralPath $f) { Get-Content -LiteralPath $f | Where-Object { $_ -match '\S' } | ForEach-Object { Write-Host "      $_" }; Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
                }
                if ($code -ne 0) { $failed += $r.C }
            }
            if ($failed.Count) { Fail "step 2: $($failed -join ', ') failed (output above); run again: what is done is kept" }
        }
        foreach ($k in $Tarballs.Keys) {
            $e = Status $k
            if ($e.State -ne 'ok') { Fail "$($Tarballs[$k].Name): not present after step 2" }
            $script:Done.Add("$($Tarballs[$k].Name) $($e.Det.Text)")
        }
        if (-not (XwinOk)) { Fail "the Windows CRT/SDK is not in $(XwinDir) after step 2" }
        $script:Done.Add("Windows CRT/SDK (xwin, $(XwinRequest))")
    }
    RefreshEnv
    $xw = XwinDir
    if (XwinOk) {
        $crt = @(Get-ChildItem -LiteralPath (Join-Path $xw 'VC/Tools/MSVC') -Directory)[0].Name
        if (-not (Test-Path -LiteralPath (Join-Path $xw "VC/Tools/MSVC/$crt/lib/x64/delayimp.lib"))) { Warn 'the xwin CRT does not have delayimp.lib: /DELAYLOAD:SDL3.dll will fail when linking the game' }
        # Case overlay for the #includes and links for the .rc files (derived from xwin; always redone).
        if (-not $Check) {
            $py = Join-Path (TarDir 'python') 'bin/python3'
            if (-not (Test-Path -LiteralPath $py)) { $py = 'python3' }
            Run $py @('-I', (Join-Path $Root 'tools/linux/xwin_vfs.py'), $xw) 'xwin_vfs.py'
        } elseif (-not (Test-Path -LiteralPath "$xw-vfs.yaml")) { Problem "$xw-vfs.yaml is missing (case overlay)" }
    }
}
"""),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
