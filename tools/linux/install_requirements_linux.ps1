# install_requirements_linux.ps1 - Linux part of install_requirements.ps1 (dot-sourced by it when running on Linux; it
# does not run on its own). Launch it with tools/linux/install_requirements_linux.sh, which brings PowerShell 7 and
# calls install_requirements.ps1 with your options.
#
# Redefines what is different on Linux: tools (official tarballs with a fixed SHA-256 in NX_TOOLS_DIR, by default
# ~/.local/tsuki-tools, no sudo), system packages (a single sudo, asking first), Windows CRT/SDK with xwin (you accept
# the Microsoft license when xwin asks), vcpkg triplet to build the x64-windows libraries cross and the summary.
# Steps 3 to 10 are those of install_requirements.ps1 (AP_DROP_UNUSED: step 7 builds tools/nsextract, no nstool)
# (with their $OnLinux branches).
#
# Steps on Linux:
#   0 precheck       distro, architecture, PowerShell, system programs, network, space; status of each tool
#   1 system         missing system programs (git, curl, make, gcc, nasm, ...): one sudo, asking first
#   2 user           LLVM, CMake, Ninja, xwin, Python 3.12, .NET SDK 10, Vulkan SDK (spirv-val) in NX_TOOLS_DIR;
#                    Windows CRT/SDK with xwin (-XwinCrt/-XwinSdk) and its case overlay (xwin_vfs.py)
#                    AP_LINUX_PARALLEL: 3 processes at once (llvm | xwin + CRT/SDK | the rest), each tarball unpacked while
#                    it downloads (xz -T0), LLVM without the tools this port does not use (1.9 GB instead of 11.8 GB)
#   3..6, 8..10      as on Windows (vcpkg in ~/dev/vcpkg or -Vcpkg; cross triplet in _prepare/triplets)
#   7 nsextract      the one of install_requirements.ps1 (dotnet build tools/nsextract)
#  11 summary

if ($Vcpkg -eq 'C:\dev\vcpkg') { $Vcpkg = Join-Path $HOME 'dev/vcpkg' }   # the Windows default value: on Linux ~/dev/vcpkg
$script:T = if ($env:NX_TOOLS_DIR) { $env:NX_TOOLS_DIR } else { Join-Path $HOME '.local/tsuki-tools' }
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'; $env:DOTNET_NOLOGO = '1'
$env:DOTNET_SYSTEM_GLOBALIZATION_INVARIANT = '1'   # the harness does not need ICU: one system dependency less

# ---------------------------------------------------------------------------------------------- references (Linux)
# Tarballs: Sha = fixed SHA-256 (measured 2026-10-21). dotnet and vulkan: hash published by Microsoft / LunarG (like
# the Vulkan SDK on Windows), read from their metadata at that moment.
$Tarballs = [ordered]@{
    llvm   = @{ Name = 'LLVM (clang-cl, lld-link, llvm-rc)'; Pin = '23.1.3'; Dir = 'llvm-23.1.3'; Strip = 1
                Url = 'https://github.com/llvm/llvm-project/releases/download/llvmorg-23.1.3/LLVM-23.1.3-Linux-X64.tar.xz'
                Sha = 'CA9FE385A5D9E2D88132DC0E23A00E9F87F38FA75F67207B5E457D208E71D013'; Probe = 'bin/clang-cl'
                Top = 'LLVM-23.1.3-Linux-X64'; Exclude = @('bin/flang*', 'bin/mlir*', 'bin/bbc', 'bin/tco', 'bin/fir-*', 'bin/f18-*', 'bin/clang-repl', 'bin/clang-check', 'bin/clang-tidy', 'bin/clangd', 'bin/clang-query', 'bin/clang-refactor', 'bin/clang-change-namespace', 'bin/clang-move', 'bin/clang-include-*', 'bin/c-index-test', 'bin/find-all-symbols', 'bin/clang-reorder-fields', 'bin/clang-doc', 'bin/modularize', 'bin/pp-trace', 'bin/clang-extdef-mapping', 'bin/clang-installapi', 'bin/clang-ssaf-*', 'bin/clang-linker-wrapper', 'bin/clang-nvlink-wrapper', 'bin/clang-sycl-linker', 'bin/opt', 'bin/llc', 'bin/llvm-lto*', 'bin/llvm-reduce', 'bin/llvm-ir2vec', 'bin/llvm-dwarfutil', 'bin/dsymutil', 'bin/llvm-c-test', 'bin/lldb*', 'bin/llvm-split', 'bin/llvm-gsymutil', 'bin/llvm-exegesis', 'bin/llvm-bolt*', 'bin/perf2bolt', 'bin/llvm-jitlink', 'bin/lli', 'bin/llvm-cfi-verify', 'bin/llvm-debuginfo-analyzer', 'bin/llvm-profgen', 'bin/llvm-mca', 'bin/sancov', 'bin/llvm-rtdyld', 'lib/*.a', 'lib/liblldb*', 'include/*') }   # AP_LINUX_PARALLEL
    cmake  = @{ Name = 'CMake'; Pin = '4.4.4'; Dir = 'cmake-4.4.4'; Strip = 1
                Url = 'https://github.com/Kitware/CMake/releases/download/v4.4.4/cmake-4.4.4-linux-x86_64.tar.gz'
                Sha = 'E5BB807F7728CB60CD8B27EBC97A2EDB469B68655F21E844A600C3575B76F5BB'; Probe = 'bin/cmake' }
    ninja  = @{ Name = 'Ninja'; Pin = '1.13.2'; Dir = 'ninja-1.13.2'; Strip = 0; Zip = $true
                Url = 'https://github.com/ninja-build/ninja/releases/download/v1.13.2/ninja-linux.zip'
                Sha = '5749CBC4E668273514150A80E387A957F933C6ED3F5F11E03FB30955E2BBEAD6'; Probe = 'ninja' }
    xwin   = @{ Name = 'xwin (Windows CRT/SDK)'; Pin = '0.10.0'; Dir = 'xwin-0.10.0'; Strip = 1
                Url = 'https://github.com/Jake-Shadle/xwin/releases/download/0.10.0/xwin-0.10.0-x86_64-unknown-linux-musl.tar.gz'
                Sha = 'D870EB4B2F390878AF6DA1CCD3CF321D22FCB72720984853B4BE732AE597FC88'; Probe = 'xwin' }
    python = @{ Name = 'Python'; Pin = '3.12.10'; Dir = 'python-3.12.10'; Strip = 1
                Url = 'https://github.com/astral-sh/python-build-standalone/releases/download/20250409/cpython-3.12.10+20250409-x86_64-unknown-linux-gnu-install_only.tar.gz'
                Sha = 'E9CF6F7DA499A4400BA30AE1DA8F7EF25CE97827BD8C1084717AA05438035186'; Probe = 'bin/python3' }
    dotnet = @{ Name = '.NET SDK'; Pin = '10.0.401'; Dir = 'dotnet-10.0.401'; Strip = 0; Hash = 'dotnet'
                Url = 'https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json'; Probe = 'dotnet' }
    vulkan = @{ Name = 'Vulkan SDK (spirv-val, vulkaninfo)'; Pin = '1.4.363.0'; Dir = 'vulkan-1.4.363.0'; Strip = 1; Hash = 'lunarg'
                Url = 'https://sdk.lunarg.com/sdk/download/1.4.363.0/linux/vulkan_sdk.tar.xz'; Probe = 'x86_64/bin/spirv-val' }
}
# AP_DROP_UNUSED: the nstool pins (commit, tree) removed
$XwinManifest = '18'   # VS 2026 (Build Tools 18.x), as on Windows
# System programs (not installed in NX_TOOLS_DIR): command -> package per manager.
$SystemPkgs = [ordered]@{
    git = @{ apt = 'git'; dnf = 'git'; pacman = 'git' }
    curl = @{ apt = 'curl'; dnf = 'curl'; pacman = 'curl' }
    tar = @{ apt = 'tar'; dnf = 'tar'; pacman = 'tar' }
    xz = @{ apt = 'xz-utils'; dnf = 'xz'; pacman = 'xz' }
    unzip = @{ apt = 'unzip'; dnf = 'unzip'; pacman = 'unzip' }
    zip = @{ apt = 'zip'; dnf = 'zip'; pacman = 'zip' }
    make = @{ apt = 'make'; dnf = 'make'; pacman = 'make' }
    gcc = @{ apt = 'build-essential'; dnf = 'gcc'; pacman = 'base-devel' }
    'g++' = @{ apt = 'build-essential'; dnf = 'gcc-c++'; pacman = 'base-devel' }
    'pkg-config' = @{ apt = 'pkg-config'; dnf = 'pkgconf-pkg-config'; pacman = 'pkgconf' }
    nasm = @{ apt = 'nasm'; dnf = 'nasm'; pacman = 'nasm' }
    python3 = @{ apt = 'python3'; dnf = 'python3'; pacman = 'python' }
}
$SpaceT = 10GB   # AP_LINUX_PARALLEL: LLVM 1.9 GB (+ its 2 GB archive while installing) + .NET + Vulkan SDK + xwin

# Tool table used by Status, Step0 and Step11 (on Linux: those of the tarballs + git from the system).
function TarDir([string]$k) { Join-Path $script:T $Tarballs[$k].Dir }
function DetectTar([string]$k) {
    $d = TarDir $k
    if (-not (Test-Path -LiteralPath (Join-Path $d $Tarballs[$k].Probe))) { return $null }
    $ok = Test-Path -LiteralPath (Join-Path $d '.hash')
    # .sha256: left by tools/tests/test_vcpkg_cross.sh (same tarball, same SHA): accepted as is
    $m = Join-Path $d '.sha256'
    if (-not $ok -and $Tarballs[$k].ContainsKey('Sha') -and (Test-Path -LiteralPath $m)) { $ok = ([IO.File]::ReadAllText($m).Trim() -eq $Tarballs[$k].Sha) }
    if (-not $ok) { return $null }
    return @{ Version = (ParseVersion $Tarballs[$k].Pin); Path = $d; Text = $Tarballs[$k].Pin }
}
$ToolSpecs = [ordered]@{}
foreach ($k in $Tarballs.Keys) {
    $ToolSpecs[$k] = @{ Name = $Tarballs[$k].Name; Pin = $Tarballs[$k].Pin; Rule = "$($Tarballs[$k].Pin) (verified tarball)"
                        Detect = [scriptblock]::Create("DetectTar '$k'"); Meets = { param($v) $true } }
}
$ToolSpecs['python'].Detect = 'DetectPython'
$ToolSpecs['python'].Meets = { param($v) $v.Major -eq 3 -and $v.Minor -eq 12 }
$ToolSpecs['dotnet'].Meets = { param($v) $v.Major -eq 10 -and $v.Minor -eq 0 }
$ToolSpecs['git'] = @{ Name = 'Git (from the system)'; Pin = ''; Rule = '>= 2.40'; Detect = 'DetectGit'; Meets = { param($v) $v -ge [version]'2.40' } }

# ---------------------------------------------------------------------------------------------- redefinitions
function RefreshEnv {
    $paths = @((Join-Path (TarDir 'llvm') 'bin'), (Join-Path (TarDir 'cmake') 'bin'), (TarDir 'ninja'), (TarDir 'xwin'),
               (Join-Path (TarDir 'python') 'bin'), (TarDir 'dotnet'))
    foreach ($r in $paths) { if ((Test-Path -LiteralPath $r) -and (($env:PATH -split ':') -notcontains $r)) { $env:PATH = "${r}:$env:PATH" } }
    if (Test-Path -LiteralPath (TarDir 'dotnet')) { $env:DOTNET_ROOT = TarDir 'dotnet' }
    $vk = Join-Path (TarDir 'vulkan') 'x86_64'
    if (Test-Path -LiteralPath $vk) { $env:VULKAN_SDK = $vk }
}
function DetectPython {
    $p = Join-Path (TarDir 'python') 'bin/python3'
    if (-not (DetectTar 'python')) { return $null }
    return (FirstThatRuns @($p) @('-c', 'import sys;print(sys.version.split()[0])') '^(\d+\.\d+\.\d+)')
}
function DetectVulkan {
    $d = DetectTar 'vulkan'
    if (-not $d) { return $null }
    return @{ Version = $d.Version; Path = (Join-Path $d.Path 'x86_64'); Text = $d.Text }
}
function XwinDir { Join-Path $script:T 'xwin' }
function XwinRequest { "manifest=$XwinManifest crt=$XwinCrt sdk=$XwinSdk" }
function XwinOk {
    $m = Join-Path (XwinDir) 'VERSIONS.txt'
    if (-not (Test-Path -LiteralPath $m)) { return $false }
    return (@(Get-Content -LiteralPath $m) -contains "request: $(XwinRequest)")
}

# ---------------------------------------------------------------------------------------------- utilities (Linux)
function FreeBytes([string]$p) {
    while ($p -and -not (Test-Path -LiteralPath $p)) { $p = Split-Path -Parent $p }
    if (-not $p) { return $null }
    $r = RunCapture 'df' @('-Pk', $p)
    if ($r.Code -ne 0 -or $r.Output.Count -lt 2) { return $null }
    return [int64](($r.Output[1] -split '\s+')[3]) * 1024
}
function DownloadCurl([string]$url, [string]$dst) {
    Info "downloading $url"
    $tmp = "$dst.partial"
    Run 'curl' @('-fL', '--retry', '3', $(if ($Chain) { '-sS' } else { '--progress-bar' }), '-o', $tmp, $url) "download of $url"   # AP_LINUX_PARALLEL: no bars in the logs of the parallel processes
    Move-Item -LiteralPath $tmp -Destination $dst -Force
}
# URL and official hash of those without a fixed SHA (like the Vulkan SDK on Windows): .NET (SHA-512 from Microsoft's
# releases.json) and Vulkan SDK (SHA-256 from the LunarG API).
function OfficialHash([string]$k) {
    $h = $Tarballs[$k]
    if ($h.Hash -eq 'lunarg') {
        try { $sha = (Invoke-RestMethod -UseBasicParsing -Uri "https://sdk.lunarg.com/sdk/sha/$($h.Pin)/linux/vulkan_sdk.tar.xz.json").sha }
        catch { Fail "could not read the SHA-256 of Vulkan SDK $($h.Pin) at sdk.lunarg.com ($($_.Exception.Message))" }
        if ("$sha" -notmatch '^[0-9a-fA-F]{64}$') { Fail "LunarG did not return a SHA-256 for Vulkan SDK $($h.Pin)" }
        return @{ Url = $h.Url; Algorithm = 'SHA256'; Hash = "$sha".ToUpper(); File = "vulkan_sdk-$($h.Pin).tar.xz" }
    }
    try { $rel = Invoke-RestMethod -UseBasicParsing -Uri $h.Url }
    catch { Fail "could not read the .NET SDK metadata ($($h.Url)): $($_.Exception.Message)" }
    foreach ($r in @($rel.releases)) {
        $sdks = @()
        if ($r.PSObject.Properties['sdks']) { $sdks += @($r.sdks) }
        if ($r.PSObject.Properties['sdk']) { $sdks += @($r.sdk) }
        foreach ($s in $sdks) {
            if ("$($s.version)" -ne $h.Pin) { continue }
            $f = @($s.files | Where-Object { $_.rid -eq 'linux-x64' -and $_.name -like '*.tar.gz' }) | Select-Object -First 1
            if ($f -and "$($f.hash)" -match '^[0-9a-fA-F]{128}$') { return @{ Url = $f.url; Algorithm = 'SHA512'; Hash = "$($f.hash)".ToUpper(); File = "dotnet-sdk-$($h.Pin)-linux-x64.tar.gz" } }
        }
    }
    Fail ".NET releases.json does not have SDK $($h.Pin) for linux-x64 with its SHA-512"
}
# AP_LINUX_PARALLEL: tarballs are decompressed with xz -T0 (the LLVM one has 494 blocks: all the cores; 2 cores measured
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
function InstallTar([string]$k) {
    $h = $Tarballs[$k]
    $dst = TarDir $k
    if (Test-Path -LiteralPath $dst) { Fail "$dst exists and is not the verified $($h.Name) $($h.Pin): set it aside by hand" }
    $dl = Join-Path $script:T 'downloads'
    New-Item -ItemType Directory -Path $dl -Force | Out-Null
    if ($h.ContainsKey('Hash')) { $o = OfficialHash $k } else { $o = @{ Url = $h.Url; Algorithm = 'SHA256'; Hash = $h.Sha; File = (Split-Path -Leaf $h.Url) } }
    $arch = Join-Path $dl $o.File
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
    $script:Done.Add("$($h.Name) $($h.Pin) (installed now)")
}
function PackageManager {
    foreach ($g in 'apt-get', 'dnf', 'pacman') { if (InPath $g) { return ($g -replace '-get$', '') } }
    return $null
}
function SystemCommand([string]$g, [string[]]$packages) {
    switch ($g) {
        'apt' { return @('apt-get', 'install', '-y') + $packages }
        'dnf' { return @('dnf', 'install', '-y') + $packages }
        'pacman' { return @('pacman', '-S', '--needed', '--noconfirm') + $packages }
    }
}
function MissingSystem { @($SystemPkgs.Keys | Where-Object { -not (InPath $_) }) }
# Inventory of a clone (without .git or bin/obj), like TreeInventory.
function CloneInventory([string]$dir) {
    $base = [IO.Path]::GetFullPath($dir).TrimEnd('/')
    $l = New-Object System.Collections.Generic.List[string]
    foreach ($f in Get-ChildItem -LiteralPath $base -Recurse -File -Force) {
        $p = $f.FullName.Substring($base.Length + 1)
        if ($p -match '(^|/)\.git(/|$)' -or $p -match '(^|/)(bin|obj)/') { continue }
        $l.Add("$p  " + (Sha256 $f.FullName))
    }
    $a = $l.ToArray(); [Array]::Sort($a, [StringComparer]::Ordinal)
    $txt = ($a -join "`n") + "`n"
    $sha = [Security.Cryptography.SHA256]::Create()
    return @{ Files = $a.Count; Hash = (($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($txt)) | ForEach-Object { $_.ToString('X2') }) -join '') }
}
# x64-windows overlay triplet to build cross (used by step 5 of install_requirements.ps1). Same name as on
# Windows: installed/x64-windows stays where the build looks for it.
function TripletLinux([string]$ov) {
    $xw = XwinDir
    if (-not (Test-Path -LiteralPath (Join-Path $xw 'VC/Tools/MSVC'))) { Fail "the xwin CRT/SDK is missing in $xw (step 2)" }
    if ($xw -match '\s') { Fail "the xwin path ($xw) has spaces: the vcpkg flags do not tolerate them (NX_TOOLS_DIR without spaces)" }
    $crt = @(Get-ChildItem -LiteralPath (Join-Path $xw 'VC/Tools/MSVC') -Directory)[0].Name
    $compat = '19.' + (($crt -split '\.')[1])
    $vfs = "$xw-vfs.yaml"
    if (-not (Test-Path -LiteralPath $vfs)) { Fail "$vfs is missing (step 2: xwin_vfs.py)" }
    $tc = Join-Path $Root 'tools/linux/clangcl-xwin.cmake'
    if (-not (Test-Path -LiteralPath $tc)) { Fail "$tc is missing" }
    $base = Join-Path $Vcpkg 'triplets/x64-windows.cmake'
    if (-not (Test-Path -LiteralPath $base)) { Fail "$base does not exist" }
    New-Item -ItemType Directory -Path $ov -Force | Out-Null
    $llvm = Join-Path (TarDir 'llvm') 'bin'
    $env_ = @("# Generated by install_requirements.ps1 (Linux): paths of this PC, does not go to the repo.", "set(NX_XWIN `"$xw`")", "set(NX_LLVM `"$llvm`")",
              "include(`"$(Join-Path $Vcpkg 'scripts/toolchains/windows.cmake')`")", "include(`"$tc`")") -join "`n"
    # AP_DROP_UNUSED: no vcpkg-xwin-ffmpeg.cmake toolchain (FFmpeg is prebuilt)
    $flags = "/winsysroot $xw -fms-compatibility-version=$compat -vfsoverlay $vfs"
    $tri = [IO.File]::ReadAllText($base).TrimEnd() + "`n`n" + (@(
        '# --- tsukihime (Linux -> Windows): Release only, clang-cl + xwin toolchain (install_requirements.ps1)',
        'set(VCPKG_BUILD_TYPE release)',
        "set(VCPKG_CHAINLOAD_TOOLCHAIN_FILE `"$(Join-Path $ov 'vcpkg-xwin.cmake')`")",
        "set(VCPKG_C_FLAGS `"$flags`")",
        "set(VCPKG_CXX_FLAGS `"$flags`")",
        "set(VCPKG_LINKER_FLAGS `"/winsysroot:$xw`")") -join "`n") + "`n"   # AP_DROP_UNUSED: no ffmpeg triplet (FFmpeg is prebuilt)
    $utf8 = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $ov 'vcpkg-xwin.cmake'), $env_ + "`n", $utf8)
    # AP_DROP_UNUSED: (and vcpkg-xwin-ffmpeg.cmake is no longer written)
    [IO.File]::WriteAllText((Join-Path $ov 'x64-windows.cmake'), $tri, $utf8)
    Info "x64-windows triplet (cross, clang-cl emulates MSVC $compat, CRT $crt) in $ov"
}

# ---------------------------------------------------------------------------------------------- steps (Linux)
function Step0 {
    $os = ''; if (Test-Path -LiteralPath '/etc/os-release') { foreach ($l in Get-Content -LiteralPath '/etc/os-release') { if ($l -match '^PRETTY_NAME="?([^"]*)') { $os = $Matches[1] } } }
    $machine = ((RunCapture 'uname' @('-m')).Output | Select-Object -First 1)
    Info ("PowerShell {0}; {1}; {2}; tools in {3}" -f $PSVersionTable.PSVersion, $os, $machine, $script:T)
    if ($machine -ne 'x86_64') { Fail "the port is x64; this machine is $machine" }
    if ($script:T -match '\s') { Fail "NX_TOOLS_DIR ($script:T) has spaces: choose a folder without spaces" }
    RefreshEnv
    $f = @(MissingSystem)
    if ($f.Count) { Warn "system programs missing: $($f -join ', ') (step 1 installs them with sudo, asking first)" } else { Info 'system programs: OK' }
    if ($To -ge 2 -and -not $Check -and (InPath 'curl')) {
        $r = RunCapture 'curl' @('-sSfI', '--max-time', '15', 'https://github.com')
        if ($r.Code -eq 0) { Info 'network: github.com responds' } else { Warn 'github.com does not respond: the steps that download will fail' }
    }
    $eds = Editions; $nRoot = $SpaceExtract; foreach ($e in $eds) { $nRoot += $SpacePerEdition[$e] }
    foreach ($u in @(@($Root, $nRoot, "data and build of $($eds -join ' + ')"), @($script:T, $SpaceT, 'tools (LLVM, .NET, Vulkan SDK, xwin)'), @($Vcpkg, 15GB, 'vcpkg with ffmpeg'))) {
        $l = FreeBytes $u[0]
        if ($null -eq $l) { continue }
        if ($l -lt $u[1]) { Warn ('{0}: {1:N0} GB free; ~{2:N0} GB recommended ({3}; estimate, does not block)' -f $u[0], ($l / 1GB), ($u[1] / 1GB), $u[2]) }
        else { Info ('{0}: {1:N0} GB free' -f $u[0], ($l / 1GB)) }
    }
    Info 'status of the tools:'
    foreach ($c in $ToolSpecs.Keys) {
        $e = Status $c
        $t = switch ($e.State) { 'ok' { "OK $($e.Det.Text)" } 'missing' { 'not present' } default { "$($e.Det.Text): does not meet the rule" } }
        Info ('  {0,-36} {1,-20} rule: {2}' -f $ToolSpecs[$c].Name, $t, $ToolSpecs[$c].Rule)
    }
    Info ('  {0,-36} {1}' -f 'Windows CRT/SDK (xwin)', $(if (XwinOk) { "OK ($(XwinRequest))" } else { "not present ($(XwinRequest))" }))
}

function Step1 {
    $f = @(MissingSystem)
    if (-not $f.Count) { Info "system programs OK: $($SystemPkgs.Keys -join ', ')"; return }
    $g = PackageManager
    if (-not $g) { Problem "$($f -join ', ') are missing and the package manager is not recognized (apt, dnf, pacman): install them by hand"; return }
    $pkgs = @($f | ForEach-Object { $SystemPkgs[$_][$g] } | Sort-Object -Unique)
    $cmd = SystemCommand $g $pkgs
    $txt = "sudo $($cmd -join ' ')"
    if ($Check) { Problem "$($f -join ', ') are missing: $txt"; return }
    if ($g -eq 'apt' -and -not (Ask "Update the package list and install: $($pkgs -join ' ') (sudo)?")) { Fail "$($f -join ', ') are missing: install them with: sudo apt-get update; $txt" }
    elseif ($g -ne 'apt' -and -not (Ask "Install: $($pkgs -join ' ') (sudo)?")) { Fail "$($f -join ', ') are missing: install them with: $txt" }
    if ($g -eq 'apt') { & sudo apt-get update; if ($LASTEXITCODE -ne 0) { Fail 'sudo apt-get update failed' } }
    & sudo @cmd
    if ($LASTEXITCODE -ne 0) { Fail "$txt failed (code $LASTEXITCODE)" }
    $f = @(MissingSystem)
    if ($f.Count) { Fail "after installing these are still missing: $($f -join ', ')" }
    $script:Done.Add("system programs ($($pkgs -join ' ')) installed now")
}

# AP_LINUX_PARALLEL: step 2 in parallel, like step 1 on Windows (AP_TOOLCHAIN_XWIN): 3 processes of install_requirements.ps1
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

# AP_DROP_UNUSED: the Linux step 7 (nstool built from source) removed: step 7 is the one of install_requirements.ps1

function Step11 {
    Write-Host ''
    Write-Host '   Status:' -ForegroundColor Green
    foreach ($c in $ToolSpecs.Keys) {
        $e = Status $c
        $t = switch ($e.State) { 'ok' { "OK $($e.Det.Text)" } 'missing' { 'MISSING' } default { "$($e.Det.Text) (does not meet $($ToolSpecs[$c].Rule))" } }
        Write-Host ('     {0,-36} {1}' -f $ToolSpecs[$c].Name, $t)
    }
    Write-Host ('     {0,-36} {1}' -f 'Windows CRT/SDK (xwin)', $(if (XwinOk) { 'OK' } else { 'MISSING' }))
    foreach ($l in $script:Done) { Write-Host "     $l" }
    if ($null -ne $script:GpuOk) { Write-Host ('     GPU to play (Proton): {0}' -f $(if ($script:GpuOk) { 'OK' } else { 'NO (see the warning of step 9)' })) }
    Write-Host ''
    Write-Host '   By hand (the script does not touch it):' -ForegroundColor Yellow
    $keys = Join-Path $Root 'data/prod.keys'
    Write-Host ("     prod.keys   {0}" -f $(if (Test-Path -LiteralPath $keys) { "present in $keys (not read)" } else { "copy your keys to $keys" }))
    $dumps = @(Get-ChildItem -LiteralPath $Root -File | Where-Object { $_.Extension -in '.xci', '.nsp' } | ForEach-Object { $_.Name })
    Write-Host ("     dumps       {0}" -f $(if ($dumps.Count) { "in ${Root}: " + ($dumps -join ', ') } else { "copy the .xci or .nsp of each edition to $Root (the root, not data/)" }))
    Write-Host ''
    Write-Host '   Next:' -ForegroundColor Cyan
    foreach ($ed in (Editions)) { Write-Host "     bash tools/linux/recomp_linux.sh -Edition $ed" }
    if ($script:Problems.Count) {
        Write-Host ''
        Write-Host '   Problems:' -ForegroundColor Yellow
        foreach ($p in $script:Problems) { Write-Host "     $p" }
    }
}
