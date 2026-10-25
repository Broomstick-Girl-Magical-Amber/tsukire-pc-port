# install_requirements_linux.ps1 - Linux part of install_requirements.ps1 (dot-sourced by it when running on Linux; it
# does not run on its own). Launch it with tools/linux/install_requirements_linux.sh, which brings PowerShell 7 and
# calls install_requirements.ps1 with your options.
#
# Redefines what is different on Linux: tools (official tarballs with a fixed SHA-256 in NX_TOOLS_DIR, by default
# ~/.local/tsuki-tools, no sudo), system packages (a single sudo, asking first), Windows CRT/SDK with xwin (you accept
# the Microsoft license when xwin asks), nstool built from source (commit and tree verified), vcpkg triplet to build the
# x64-windows libraries cross and the summary. Steps 3, 4, 5, 6, 8, 9 and 10 are those of install_requirements.ps1
# (with their $OnLinux branches).
#
# Steps on Linux:
#   0 precheck       distro, architecture, PowerShell, system programs, network, space; status of each tool
#   1 system         missing system programs (git, curl, make, gcc, nasm, ...): one sudo, asking first
#   2 user           LLVM, CMake, Ninja, xwin, Python 3.12, .NET SDK 10, Vulkan SDK (spirv-val) in NX_TOOLS_DIR;
#                    Windows CRT/SDK with xwin (-XwinCrt/-XwinSdk) and its case overlay (xwin_vfs.py)
#   3..6, 8..10      as on Windows (vcpkg in ~/dev/vcpkg or -Vcpkg; cross triplet in _prepare/triplets)
#   7 nstool         tools/nstool built from jakcron/nstool v1.9.2 (commit + tree verified)
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
                Sha = 'CA9FE385A5D9E2D88132DC0E23A00E9F87F38FA75F67207B5E457D208E71D013'; Probe = 'bin/clang-cl' }
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
$NstoolCommit = 'e89ea84bd70c11b67ebec9153fb4a446f18a9d3d'
$NstoolRepo = 'https://github.com/jakcron/nstool.git'
# Tree of nstool v1.9.2 with submodules (clone --depth 1 --recurse-submodules --shallow-submodules): "path  SHA256" per
# file without .git or bin/obj, ordinal order, LF; SHA-256 of that text (measured 2026-10-21).
$NstoolFiles = 2000
$NstoolTree = '2C97AE6672D5D1814C5DBCDAA8C844C790AC8CFFB13BBEA322FC281A0AA80717'
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
$SpaceT = 25GB   # LLVM extracted 12 GB + .NET + Vulkan SDK + xwin + downloads

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
    Run 'curl' @('-fL', '--retry', '3', '--progress-bar', '-o', $tmp, $url) "download of $url"
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
function InstallTar([string]$k) {
    $h = $Tarballs[$k]
    $dst = TarDir $k
    if (Test-Path -LiteralPath $dst) { Fail "$dst exists and is not the verified $($h.Name) $($h.Pin): set it aside by hand" }
    $dl = Join-Path $script:T 'downloads'
    New-Item -ItemType Directory -Path $dl -Force | Out-Null
    if ($h.ContainsKey('Hash')) { $o = OfficialHash $k } else { $o = @{ Url = $h.Url; Algorithm = 'SHA256'; Hash = $h.Sha; File = (Split-Path -Leaf $h.Url) } }
    $arch = Join-Path $dl $o.File
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
    if ($h.ContainsKey('Zip')) { Expand-Archive -LiteralPath $arch -DestinationPath $tmp }
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
    $ff = @("# Generated by install_requirements.ps1 (Linux): like vcpkg-xwin.cmake but with llvm-ar (ffmpeg only; see clangcl-xwin.cmake).",
            'set(NX_AR_GNU ON)', "include(`"$(Join-Path $ov 'vcpkg-xwin.cmake')`")") -join "`n"
    $flags = "/winsysroot $xw -fms-compatibility-version=$compat -vfsoverlay $vfs"
    $tri = [IO.File]::ReadAllText($base).TrimEnd() + "`n`n" + (@(
        '# --- tsukihime (Linux -> Windows): Release only, clang-cl + xwin toolchain (install_requirements.ps1)',
        'set(VCPKG_BUILD_TYPE release)',
        "set(VCPKG_CHAINLOAD_TOOLCHAIN_FILE `"$(Join-Path $ov 'vcpkg-xwin.cmake')`")",
        "set(VCPKG_C_FLAGS `"$flags`")",
        "set(VCPKG_CXX_FLAGS `"$flags`")",
        "set(VCPKG_LINKER_FLAGS `"/winsysroot:$xw`")",
        'if(PORT STREQUAL "ffmpeg")',
        "    set(VCPKG_CHAINLOAD_TOOLCHAIN_FILE `"$(Join-Path $ov 'vcpkg-xwin-ffmpeg.cmake')`")",
        'endif()') -join "`n") + "`n"
    $utf8 = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $ov 'vcpkg-xwin.cmake'), $env_ + "`n", $utf8)
    [IO.File]::WriteAllText((Join-Path $ov 'vcpkg-xwin-ffmpeg.cmake'), $ff + "`n", $utf8)
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

function Step2 {
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

function Step7 {
    $dst = Join-Path $Root 'tools/nstool'
    $stamp = "$dst.source"
    $expected = "nstool v1.9.2 $NstoolCommit $NstoolTree"
    if ($NstoolFrom) { Warn '-NstoolFrom does not apply on Linux (the binary is not reproducible): it is built from the verified source' }
    if ((Test-Path -LiteralPath $dst) -and (Test-Path -LiteralPath $stamp) -and (([IO.File]::ReadAllText($stamp)).Trim() -eq $expected)) {
        Info "tools/nstool built from v1.9.2 ($($NstoolCommit.Substring(0, 12)), verified tree)"; $script:Done.Add('nstool 1.9.2'); return
    }
    if ($Check) { Problem "tools/nstool built from verified v1.9.2 is missing"; return }
    if (Test-Path -LiteralPath $dst) {
        if (-not (Ask "$dst is not the nstool built by this script. Set it aside as nstool.old and build the reference one?")) { Fail "$dst is not the reference one" }
        if (Test-Path -LiteralPath "$dst.old") { Fail "$dst.old already exists: delete it by hand" }
        Move-Item -LiteralPath $dst -Destination "$dst.old"
    }
    $src = Join-Path $WorkDir "nstool_$(Stamp)"
    try {
        Run 'git' @('clone', '-q', '--depth', '1', '--branch', 'v1.9.2', '--recurse-submodules', '--shallow-submodules', $NstoolRepo, $src) 'git clone (nstool)'
        $head = (RunCapture 'git' @('-C', $src, 'rev-parse', 'HEAD')).Output | Select-Object -First 1
        if ($head -ne $NstoolCommit) { Fail "the v1.9.2 tag of nstool points to $head, not to $NstoolCommit" }
        $inv = CloneInventory $src
        if ($inv.Hash -ne $NstoolTree) { Fail "the nstool v1.9.2 tree is not the reference one ($($inv.Files) files, $NstoolFiles expected; hash $($inv.Hash))" }
        Info "nstool v1.9.2: commit and tree verified ($($inv.Files) files); building"
        $j = "-j$([Environment]::ProcessorCount)"
        Push-Location $src
        try { Run 'make' @($j, 'deps') 'make deps (nstool)'; Run 'make' @($j) 'make (nstool)' } finally { Pop-Location }
        $bin = Join-Path $src 'bin/nstool'
        if (-not (Test-Path -LiteralPath $bin)) { Fail 'make finished but bin/nstool does not appear' }
        Copy-Item -LiteralPath $bin -Destination "$dst.tmp"
        Move-Item -LiteralPath "$dst.tmp" -Destination $dst
        [IO.File]::WriteAllText($stamp, "$expected`n")
    } finally { if (Test-Path -LiteralPath $src) { Remove-Item -LiteralPath $src -Recurse -Force -ErrorAction SilentlyContinue } }
    $v = (RunCapture $dst @()).Output | Where-Object { $_ -match 'NSTool v' } | Select-Object -First 1
    Info "tools/nstool ready ($v)"
    $script:Done.Add('nstool 1.9.2 (built now)')
}

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
