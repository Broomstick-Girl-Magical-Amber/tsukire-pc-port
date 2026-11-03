# install_requirements_xwin.ps1 - Windows WITHOUT Visual Studio Build Tools (AP_TOOLCHAIN_XWIN).
# Dot-sourced by tools\install_requirements.ps1 on Windows; it only redefines things when the xwin toolchain is chosen:
#   -Toolchain xwin, or -Toolchain auto (the default) when tools\internal\prebuilt.json has a "deps" asset.
# -Toolchain vs keeps the previous flow (VS Build Tools, .NET, Vulkan SDK, vcpkg) untouched.
#
# The same exe is built as with VS, the way the Linux cross build already does it: clang-cl + lld-link + llvm-rc of the
# official LLVM against the MSVC CRT/STL and the Windows SDK that xwin downloads from Microsoft (you accept their
# license), with tools/linux/clangcl-xwin.cmake. Everything goes to NX_TOOLS_DIR (default %LOCALAPPDATA%\tsuki-tools),
# per user: NO administrator, NO winget, nothing in Program Files.
#   LLVM 23.1.3     the official MSI (SHA-256 of the winget manifest), unpacked with "msiexec /a" (not installed)
#   CMake 4.4.4, Ninja 1.13.2, Python 3.12.10 (python-build-standalone), xwin 0.10.0: official archives, fixed SHA-256
#   prebuilt pack   tools\internal\prebuilt.json (published by the maintainer with tools\dev\build_prebuilt.ps1):
#                   deps = opus, libjpeg-turbo, libpng, zlib, SDL3 (x64-windows, built by vcpkg) + spirv-val + Vulkan
#                   headers -> no vcpkg, no Vulkan SDK; harness / nsextract (optional) -> no .NET SDK. A component that
#                   is not in the pack is built here as before (the .NET SDK then comes from Microsoft's zip, per user).
# Step 1 does all of that in parallel (4 processes of this same script: -Chain llvm | crt | python | misc), the venv
# included, and writes <root>\_prepare\toolchain.json, which tools\dev_env.ps1 and recomp_tsukihime.ps1 read.
# NX_TOOLCHAIN=vs (environment) makes dev_env / recomp ignore toolchain.json.

$XwinPrebuiltFile = Join-Path $PSScriptRoot 'prebuilt.json'
function ReadPrebuilt {
    if (-not (Test-Path -LiteralPath $XwinPrebuiltFile)) { return $null }
    try { return (Get-Content -LiteralPath $XwinPrebuiltFile -Raw | ConvertFrom-Json) } catch { return $null }
}
$script:Prebuilt = ReadPrebuilt
# Fingerprint of the sources a prebuilt tool was built from (the same function is in tools\dev\build_prebuilt.ps1 and
# recomp_tsukihime.ps1): SHA-256 over "path SHA-256(text without CR)" of every source file ($SrcExt), bin\ and obj\ excluded.
$SrcExt = @('.cs', '.csproj', '.props', '.targets', '.patch', '.py')
function SourceStamp([string]$root, [string[]]$rels) {
    $sha = [Security.Cryptography.SHA256]::Create(); $enc = New-Object Text.UTF8Encoding($false); $lines = @()
    foreach ($rel in $rels) {
        $p = Join-Path $root $rel
        if (-not (Test-Path -LiteralPath $p)) { $lines += "missing $rel"; continue }
        $base = (Resolve-Path -LiteralPath $root).Path.TrimEnd('\', '/')
        foreach ($f in @(Get-ChildItem -LiteralPath $p -Recurse -File -Force | Where-Object { $_.Extension -in $SrcExt -and $_.FullName.Substring($base.Length) -notmatch '[\\/](bin|obj)[\\/]' })) {
            $t = [IO.File]::ReadAllText($f.FullName, $enc).Replace("`r", '')
            $h = [BitConverter]::ToString($sha.ComputeHash($enc.GetBytes($t))).Replace('-', '')
            $lines += ($f.FullName.Substring($base.Length + 1).Replace('\', '/') + ' ' + $h)
        }
    }
    $sorted = [string[]]$lines; [Array]::Sort($sorted, [StringComparer]::Ordinal)
    return [BitConverter]::ToString($sha.ComputeHash($enc.GetBytes(($sorted -join "`n")))).Replace('-', '')
}
$PrebuiltSources = @{ harness = @('framework/tools/maxwell/spirv_harness', 'framework/tools/maxwell/apply_patch.py'); nsextract = @('tools/nsextract') }
$script:SourceOk = @{}
function PrebuiltAsset([string]$k) {
    if (-not $script:Prebuilt -or -not $script:Prebuilt.PSObject.Properties['assets']) { return $null }
    $p = $script:Prebuilt.assets.PSObject.Properties[$k]
    if (-not ($p -and "$($p.Value.url)" -match '^https://' -and "$($p.Value.sha256)" -match '^[0-9A-Fa-f]{64}$')) { return $null }
    # harness / nsextract: only if this repo has the same sources it was built from (otherwise it is built here, as before)
    if ($PrebuiltSources.ContainsKey($k) -and $p.Value.PSObject.Properties['source']) {
        if (-not $script:SourceOk.ContainsKey($k)) {
            $repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # <root>\tools\internal -> <root>
            $script:SourceOk[$k] = ((SourceStamp $repo $PrebuiltSources[$k]) -eq "$($p.Value.source)")
            if (-not $script:SourceOk[$k]) { Write-Host "   WARNING: prebuilt $k of $($script:Prebuilt.tag) is from other sources than this repo: it is built here" -ForegroundColor Yellow }
        }
        if (-not $script:SourceOk[$k]) { return $null }
    }
    return $p.Value
}

$script:UseXwin = ($Toolchain -eq 'xwin') -or ($Toolchain -eq 'auto' -and [bool](PrebuiltAsset 'deps'))
if (-not $script:UseXwin) { return }   # VS Build Tools, as before
# The chains of step 1 run install_requirements.ps1 again: inside the functions of this file $PSCommandPath is THIS file.
$NxMainScript = $MyInvocation.PSCommandPath
if (-not $NxMainScript -or $NxMainScript -like '*install_requirements_xwin.ps1') { $NxMainScript = Join-Path (Split-Path -Parent $PSScriptRoot) 'install_requirements.ps1' }

# ---------------------------------------------------------------------------------------------- references
$script:T = if ($env:NX_TOOLS_DIR) { $env:NX_TOOLS_DIR } else { Join-Path $env:LOCALAPPDATA 'tsuki-tools' }
# SHA-256 measured 2026-11-02 on the downloaded files (LLVM: the same as the winget manifest LLVM.LLVM 23.1.3).
$WinTools = [ordered]@{
    llvm   = @{ Name = 'LLVM (clang-cl, lld-link, llvm-rc)'; Pin = '23.1.3'; Dir = 'llvm-23.1.3'; Kind = 'msi'; Probe = 'bin\clang-cl.exe'
                Url = 'https://github.com/llvm/llvm-project/releases/download/llvmorg-23.1.3/LLVM-23.1.3-win64.msi'
                Sha = '5153F848AC87553118340F16F8D3CD4351D729BCBA6E4785C6A4A9AF29676DE9' }
    cmake  = @{ Name = 'CMake'; Pin = '4.4.4'; Dir = 'cmake-4.4.4'; Kind = 'zip'; Strip = 1; Probe = 'bin\cmake.exe'
                Url = 'https://github.com/Kitware/CMake/releases/download/v4.4.4/cmake-4.4.4-windows-x86_64.zip'
                Sha = 'BACE36E94B31C68AB6FA295F26DFA11219E0701CF7C94B0284A7D1CB13DAC536' }
    ninja  = @{ Name = 'Ninja'; Pin = '1.13.2'; Dir = 'ninja-1.13.2'; Kind = 'zip'; Strip = 0; Probe = 'ninja.exe'
                Url = 'https://github.com/ninja-build/ninja/releases/download/v1.13.2/ninja-win.zip'
                Sha = '07FC8261B42B20E71D1720B39068C2E14FFCEE6396B76FB7A795FB460B78DC65' }
    python = @{ Name = 'Python'; Pin = '3.12.10'; Dir = 'python-3.12.10'; Kind = 'tar'; Strip = 1; Probe = 'python.exe'
                Url = 'https://github.com/astral-sh/python-build-standalone/releases/download/20250409/cpython-3.12.10+20250409-x86_64-pc-windows-msvc-install_only.tar.gz'
                Sha = '5AC66AE49A2104EFEBA985C1DC1CB40987757EE81CC6C7F218D10B801F3276ED' }
    xwin   = @{ Name = 'xwin'; Pin = '0.10.0'; Dir = 'xwin-0.10.0'; Kind = 'tar'; Strip = 1; Probe = 'xwin.exe'
                Url = 'https://github.com/Jake-Shadle/xwin/releases/download/0.10.0/xwin-0.10.0-x86_64-pc-windows-msvc.tar.gz'
                Sha = '96E83665EF5C1406AABD0BE603A8D08E67E604F36FDA630B6807EE948A614E26' }
    dotnet = @{ Name = '.NET SDK'; Pin = '10.0.401'; Dir = 'dotnet-10.0.401'; Kind = 'zip'; Strip = 0; Probe = 'dotnet.exe'; Hash = 'dotnet'
                Url = 'https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json' }
}
$XwinManifest = '18'   # VS 2026 (Build Tools 18.x), as on Linux
# Pieces of the prebuilt pack and where they leave their programs (inside the folder each zip is unpacked to).
$PrebuiltParts = [ordered]@{
    deps      = @{ Name = 'prebuilt libraries (opus, libjpeg-turbo, libpng, zlib, SDL3, spirv-val, Vulkan headers)'; Probe = 'deps\include\opus\opus.h' }
    harness   = @{ Name = 'prebuilt spirv_harness'; Probe = 'spirv_harness\spirv_harness.exe' }
    nsextract = @{ Name = 'prebuilt nsextract'; Probe = 'nsextract\nsextract.exe' }
}
$SpaceC = @{ WithDebug = 8GB; ReleaseOnly = 8GB }   # drive of NX_TOOLS_DIR: LLVM ~3 GB, CRT/SDK ~1.5 GB, the rest < 1 GB (estimate)

# ---------------------------------------------------------------------------------------------- detection
function WinDir([string]$k) { Join-Path $script:T $WinTools[$k].Dir }
function StampOk([string]$dir, [string]$expect) {
    $m = Join-Path $dir '.nx-hash'
    return ((Test-Path -LiteralPath $m) -and ([IO.File]::ReadAllText($m).Trim() -eq $expect))
}
function ExpectedStamp([string]$k) { if ($WinTools[$k].ContainsKey('Sha')) { "SHA256 $($WinTools[$k].Sha)" } else { "pin $($WinTools[$k].Pin)" } }
function DetectWin([string]$k) {
    $d = WinDir $k
    if (-not (Test-Path -LiteralPath (Join-Path $d $WinTools[$k].Probe))) { return $null }
    if (-not (StampOk $d (ExpectedStamp $k))) { return $null }
    return @{ Version = (ParseVersion $WinTools[$k].Pin); Path = $d; Text = $WinTools[$k].Pin }
}
function PrebuiltDir([string]$k) {
    $a = PrebuiltAsset $k
    if (-not $a) { return $null }
    return (Join-Path $script:T ("prebuilt\{0}-{1}" -f $k, "$($a.sha256)".Substring(0, 12).ToLower()))
}
function DetectPrebuilt([string]$k) {
    $d = PrebuiltDir $k
    if (-not $d -or -not (Test-Path -LiteralPath (Join-Path $d $PrebuiltParts[$k].Probe))) { return $null }
    if (-not (StampOk $d "SHA256 $("$((PrebuiltAsset $k).sha256)".ToUpper())")) { return $null }
    $tag = if ($script:Prebuilt.PSObject.Properties['tag']) { "$($script:Prebuilt.tag)" } else { '?' }
    return @{ Version = [version]'1.0'; Path = $d; Text = $tag }
}
function XwinDir { Join-Path $script:T 'xwin' }
function XwinRequest { "manifest=$XwinManifest crt=$XwinCrt sdk=$XwinSdk" }
function XwinOk {
    $m = Join-Path (XwinDir) 'VERSIONS.txt'
    if (-not (Test-Path -LiteralPath $m)) { return $false }
    return (@(Get-Content -LiteralPath $m) -contains "request: $(XwinRequest)")
}
function DetectCrt { if (XwinOk) { @{ Version = [version]$XwinSdk; Path = (XwinDir); Text = "crt $XwinCrt, sdk $XwinSdk" } } else { $null } }
function DetectPython {
    $d = DetectWin 'python'
    if (-not $d) { return $null }
    return (FirstThatRuns @((Join-Path $d.Path 'python.exe')) @('-c', 'import sys;print(sys.version.split()[0])') '^(\d+\.\d+\.\d+)')
}
function DetectDotnet {   # the per-user zip of this script first, then the usual places (same rule as before: 10.0.x)
    $best = $null
    $cands = @()
    if (DetectWin 'dotnet') { $cands += (Join-Path (WinDir 'dotnet') 'dotnet.exe') }
    $cands += @("$env:ProgramFiles\dotnet\dotnet.exe", (InPath 'dotnet'))
    foreach ($c in $cands) {
        if (-not $c -or -not (Test-Path -LiteralPath $c)) { continue }
        $r = RunCapture $c @('--list-sdks')
        if ($r.Code -ne 0) { continue }
        foreach ($l in $r.Output) {
            $v = ParseVersion (($l -split ' ')[0])
            if (-not $v) { continue }
            $ok = ($v.Major -eq 10 -and $v.Minor -eq 0)
            if (-not $best -or ($ok -and -not $best.Ok) -or ($ok -eq $best.Ok -and $v -gt $best.Version)) { $best = @{ Version = $v; Path = $c; Text = "$v"; Ok = $ok } }
        }
        if ($best -and $best.Ok) { break }
    }
    return $best
}
function NeedDotnet { -not (PrebuiltAsset 'harness') -or -not (PrebuiltAsset 'nsextract') }

$ToolSpecs = [ordered]@{}
foreach ($k in @('llvm', 'cmake', 'ninja', 'python', 'xwin')) {
    $ToolSpecs[$k] = @{ Name = $WinTools[$k].Name; Pin = $WinTools[$k].Pin; Rule = "$($WinTools[$k].Pin) (verified archive, $script:T)"
                        Detect = [scriptblock]::Create("DetectWin '$k'"); Meets = { param($v) $true }; Admin = $false; Winget = '' }
}
$ToolSpecs['python'].Detect = 'DetectPython'
$ToolSpecs['python'].Meets = { param($v) $v.Major -eq 3 -and $v.Minor -eq 12 }
$ToolSpecs['crt'] = @{ Name = 'Windows CRT/SDK (xwin)'; Pin = $XwinSdk; Rule = "crt $XwinCrt, sdk $XwinSdk (Microsoft license)"; Detect = 'DetectCrt'; Meets = { param($v) $true }; Admin = $false; Winget = '' }
foreach ($k in $PrebuiltParts.Keys) {
    if (-not (PrebuiltAsset $k)) { continue }
    $ToolSpecs["pre_$k"] = @{ Name = $PrebuiltParts[$k].Name; Pin = ''; Rule = 'tools\internal\prebuilt.json (verified zip)'
                              Detect = [scriptblock]::Create("DetectPrebuilt '$k'"); Meets = { param($v) $true }; Admin = $false; Winget = '' }
}
if (NeedDotnet) {
    $ToolSpecs['dotnet'] = @{ Name = '.NET SDK (builds what the pack does not bring)'; Pin = $WinTools.dotnet.Pin; Rule = '10.0.x'; Detect = 'DetectDotnet'
                              Meets = { param($v) $v.Major -eq 10 -and $v.Minor -eq 0 }; Admin = $false; Winget = '' }
}
$ToolSpecs['git'] = @{ Name = 'Git (optional: only to update the repo)'; Pin = ''; Rule = '>= 2.40'; Detect = 'DetectGit'; Meets = { param($v) $v -ge [version]'2.40' }; Admin = $false; Winget = '' }

# PATH of this session: the tools of NX_TOOLS_DIR first (the per-user ones of this flow), then what was there.
function RefreshEnv {
    $add = @((Join-Path (WinDir 'llvm') 'bin'), (Join-Path (WinDir 'cmake') 'bin'), (WinDir 'ninja'), (WinDir 'python'), "$env:ProgramFiles\Git\cmd")
    if (DetectWin 'dotnet') { $add += (WinDir 'dotnet'); $env:DOTNET_ROOT = WinDir 'dotnet' }
    $parts = @($add | Where-Object { Test-Path -LiteralPath $_ }) + @($env:Path -split ';' | Where-Object { $_ })
    $seen = @{}; $final = @()
    foreach ($x in $parts) { $k = $x.TrimEnd('\').ToLower(); if (-not $seen.ContainsKey($k)) { $seen[$k] = 1; $final += $x } }
    $env:Path = $final -join ';'
    $env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'; $env:DOTNET_NOLOGO = '1'
}

# ---------------------------------------------------------------------------------------------- installation
function TarExe { "$env:SystemRoot\System32\tar.exe" }
# Official .NET SDK zip: URL and SHA-512 from Microsoft's releases.json (as on Linux).
function DotnetZip {
    try { $rel = Invoke-RestMethod -UseBasicParsing -Uri $WinTools.dotnet.Url }
    catch { Fail "could not read the .NET SDK metadata ($($WinTools.dotnet.Url)): $($_.Exception.Message)" }
    foreach ($r in @($rel.releases)) {
        $sdks = @()
        if ($r.PSObject.Properties['sdks']) { $sdks += @($r.sdks) }
        if ($r.PSObject.Properties['sdk']) { $sdks += @($r.sdk) }
        foreach ($s in $sdks) {
            if ("$($s.version)" -ne $WinTools.dotnet.Pin) { continue }
            $f = @($s.files | Where-Object { $_.rid -eq 'win-x64' -and $_.name -like '*.zip' }) | Select-Object -First 1
            if ($f -and "$($f.hash)" -match '^[0-9a-fA-F]{128}$') { return @{ Url = $f.url; Algorithm = 'SHA512'; Hash = "$($f.hash)".ToUpper() } }
        }
    }
    Fail ".NET releases.json does not have SDK $($WinTools.dotnet.Pin) for win-x64 with its SHA-512"
}
# Downloads to <T>\downloads (checked before anything is unpacked) and returns the path.
function FetchVerified([string]$url, [string]$alg, [string]$hash, [string]$file) {
    $dl = Join-Path $script:T 'downloads'
    New-Item -ItemType Directory -Path $dl -Force | Out-Null
    $arch = Join-Path $dl $file
    if (-not ((Test-Path -LiteralPath $arch) -and (Get-FileHash -Algorithm $alg -LiteralPath $arch).Hash -eq $hash)) {
        Download $url $arch
        $got = (Get-FileHash -Algorithm $alg -LiteralPath $arch).Hash
        if ($got -ne $hash) { Remove-Item -LiteralPath $arch -Force; Fail "${file}: $alg $got, expected $hash (deleted)" }
    }
    Info "${file}: $alg OK"
    return $arch
}
function UnpackTo([string]$arch, [string]$tmp, [int]$strip) {
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $a = @('-xf', $arch, '-C', $tmp)
    if ($strip -gt 0) { $a += "--strip-components=$strip" }
    $r = RunCapture (TarExe) $a
    if ($r.Code -ne 0) { Fail "tar could not unpack $(Split-Path -Leaf $arch): $($r.Output | Select-Object -Last 2)" }
}
# LLVM: "msiexec /a" unpacks the MSI to a folder without installing it (no administrator in general). If Windows asks
# for elevation anyway, the same command is run elevated (one UAC).
function UnpackMsi([string]$msi, [string]$tmp) {
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $log = "$tmp.msiexec.log"
    $args_ = "/a `"$msi`" /qn TARGETDIR=`"$tmp`" /l*v `"$log`""
    $p = Start-Process -FilePath "$env:SystemRoot\System32\msiexec.exe" -ArgumentList $args_ -Wait -PassThru
    if ($p.ExitCode -ne 0) {
        Warn "msiexec /a finished with code $($p.ExitCode): trying again elevated (one UAC)"
        try { $p = Start-Process -FilePath "$env:SystemRoot\System32\msiexec.exe" -ArgumentList $args_ -Verb RunAs -Wait -PassThru }
        catch { Fail "administrator permission was not granted for msiexec ($($_.Exception.Message))" }
        if ($p.ExitCode -ne 0) { Fail "msiexec /a could not unpack $(Split-Path -Leaf $msi) (code $($p.ExitCode); log $log)" }
    }
    Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
}
function InstallWin([string]$k) {
    $h = $WinTools[$k]
    $dst = WinDir $k
    if (DetectWin $k) { Info "$($h.Name) $($h.Pin) in $dst"; return }
    if (Test-Path -LiteralPath $dst) { Fail "$dst exists and is not the verified $($h.Name) $($h.Pin): set it aside by hand" }
    if ($h.ContainsKey('Hash')) { $o = DotnetZip } else { $o = @{ Url = $h.Url; Algorithm = 'SHA256'; Hash = $h.Sha } }
    $file = Split-Path -Leaf ($o.Url -replace '\?.*$', '')
    $arch = FetchVerified $o.Url $o.Algorithm $o.Hash $file
    $tmp = "$dst.tmp"
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
    if ($h.Kind -eq 'msi') {
        UnpackMsi $arch $tmp
        $cl = @(Get-ChildItem -LiteralPath $tmp -Recurse -Filter 'clang-cl.exe' -File | Select-Object -First 1)
        if (-not $cl.Count) { Fail "the LLVM MSI unpacked without bin\clang-cl.exe (left in $tmp)" }
        $llvmRoot = Split-Path -Parent (Split-Path -Parent $cl[0].FullName)
        Move-Item -LiteralPath $llvmRoot -Destination $dst
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        UnpackTo $arch $tmp $h.Strip
        if (-not (Test-Path -LiteralPath (Join-Path $tmp $h.Probe))) { Fail "$file does not have $($h.Probe) (left in $tmp)" }
        Move-Item -LiteralPath $tmp -Destination $dst
    }
    if (-not (Test-Path -LiteralPath (Join-Path $dst $h.Probe))) { Fail "$dst does not have $($h.Probe)" }
    [IO.File]::WriteAllText((Join-Path $dst '.nx-hash'), (ExpectedStamp $k))
    Remove-Item -LiteralPath $arch -Force -ErrorAction SilentlyContinue   # the archive is not needed any more
    Info "$($h.Name) $($h.Pin) in $dst"
}
function InstallPrebuilt([string]$k) {
    $a = PrebuiltAsset $k
    if (-not $a) { return }
    if (DetectPrebuilt $k) { Info "$($PrebuiltParts[$k].Name) in $(PrebuiltDir $k)"; return }
    $dst = PrebuiltDir $k
    if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force }   # made by this script (its name carries the SHA)
    $file = if ($a.PSObject.Properties['file'] -and "$($a.file)") { "$($a.file)" } else { Split-Path -Leaf "$($a.url)" }
    $arch = FetchVerified "$($a.url)" 'SHA256' "$($a.sha256)".ToUpper() $file
    $tmp = "$dst.tmp"
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
    UnpackTo $arch $tmp 0
    if (-not (Test-Path -LiteralPath (Join-Path $tmp $PrebuiltParts[$k].Probe))) { Fail "$file does not have $($PrebuiltParts[$k].Probe) (left in $tmp)" }
    [IO.File]::WriteAllText((Join-Path $tmp '.nx-hash'), "SHA256 $("$($a.sha256)".ToUpper())")
    Move-Item -LiteralPath $tmp -Destination $dst
    Remove-Item -LiteralPath $arch -Force -ErrorAction SilentlyContinue
    Info "$($PrebuiltParts[$k].Name) in $dst"
}
# MSVC CRT/STL + Windows SDK with xwin (as on Linux, without the case overlay: Windows is not case sensitive). The license
# question is asked by step 1 in the main window (the chains run without a console of their own).
function XwinSplat {
    $xw = XwinDir
    if (XwinOk) { Info "Windows CRT/SDK in $xw ($(XwinRequest))"; return }
    if (Test-Path -LiteralPath $xw) { Fail "$xw exists and is from another request (now: $(XwinRequest)): set it aside by hand" }
    if ($env:XWIN_ACCEPT_LICENSE -ne 'true') { Fail 'the Microsoft license of the CRT/SDK was not accepted (https://go.microsoft.com/fwlink/?LinkId=2086102)' }
    $tmp = "$xw.tmp"
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
    $cache = Join-Path $script:T 'xwin-cache'
    $xa = @('--manifest-version', $XwinManifest, '--cache-dir', $cache, '--http-retry', '3', '--crt-version', $XwinCrt, '--sdk-version', $XwinSdk,
            'splat', '--use-winsysroot-style', '--preserve-ms-arch-notation', '--output', $tmp)
    Run (Join-Path (WinDir 'xwin') 'xwin.exe') $xa 'xwin (CRT/SDK)'
    $crt = @(Get-ChildItem -LiteralPath (Join-Path $tmp 'VC\Tools\MSVC') -Directory)[0].Name
    $sdk = @(Get-ChildItem -LiteralPath (Join-Path $tmp 'Windows Kits\10\Include') -Directory)[0].Name
    $stl = Select-String -LiteralPath (Join-Path $tmp "VC\Tools\MSVC\$crt\include\yvals_core.h") -Pattern '#define _MSVC_STL_UPDATE\s+(\S+)' | Select-Object -First 1
    $stlTxt = if ($stl) { $stl.Matches[0].Groups[1].Value } else { '?' }
    [IO.File]::WriteAllText((Join-Path $tmp 'VERSIONS.txt'), "request: $(XwinRequest)`ncrt: $crt`nsdk: $sdk`nstl: $stlTxt`nxwin: $($WinTools.xwin.Pin)`ndate: $(Get-Date -Format s)`n")
    Move-Item -LiteralPath $tmp -Destination $xw
    Remove-Item -LiteralPath $cache -Recurse -Force -ErrorAction SilentlyContinue   # the downloaded packages (~1 GB): not needed after splat
    if (-not (Test-Path -LiteralPath (Join-Path $xw "VC\Tools\MSVC\$crt\lib\x64\delayimp.lib"))) { Warn 'the xwin CRT does not have delayimp.lib: /DELAYLOAD:SDL3.dll will fail when linking the game' }
    Info "CRT $crt (_MSVC_STL_UPDATE $stlTxt), SDK $sdk in $xw"
}

# What tools\dev_env.ps1 and recomp_tsukihime.ps1 read (absolute paths of this PC; _prepare is not in the repo).
function WriteToolchainJson {
    $deps = PrebuiltDir 'deps'
    $j = [ordered]@{
        mode = 'xwin'; tools_dir = $script:T
        llvm_bin = (Join-Path (WinDir 'llvm') 'bin'); cmake_bin = (Join-Path (WinDir 'cmake') 'bin'); ninja_dir = (WinDir 'ninja')
        python = (WinDir 'python'); xwin = (XwinDir)
        deps = $(if ($deps) { Join-Path $deps 'deps' } else { '' }); vulkan = $(if ($deps) { Join-Path $deps 'vulkan' } else { '' })
        harness = $(if (DetectPrebuilt 'harness') { Join-Path (PrebuiltDir 'harness') 'spirv_harness\spirv_harness.exe' } else { '' })
        nsextract = $(if (DetectPrebuilt 'nsextract') { Join-Path (PrebuiltDir 'nsextract') 'nsextract\nsextract.exe' } else { '' })
        harness_source = $(if ((DetectPrebuilt 'harness') -and (PrebuiltAsset 'harness').PSObject.Properties['source']) { "$((PrebuiltAsset 'harness').source)" } else { '' })
        nsextract_source = $(if ((DetectPrebuilt 'nsextract') -and (PrebuiltAsset 'nsextract').PSObject.Properties['source']) { "$((PrebuiltAsset 'nsextract').source)" } else { '' })
        dotnet_root = $(if (DetectWin 'dotnet') { WinDir 'dotnet' } else { '' })
        prebuilt_tag = $(if ($script:Prebuilt -and $script:Prebuilt.PSObject.Properties['tag']) { "$($script:Prebuilt.tag)" } else { '' })
    }
    [IO.File]::WriteAllText((PathOf $WorkDir 'toolchain.json'), ($j | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
}

# ---------------------------------------------------------------------------------------------- chains of step 1
# Each chain runs in its own process (this script with -Chain <name>); they do not depend on each other.
$XwinChains = [ordered]@{
    llvm   = { InstallWin 'llvm' }
    crt    = { InstallWin 'xwin'; XwinSplat }
    python = { InstallWin 'python'; if ($From -le 6 -and $To -ge 6) { Step6 } }
    misc   = { InstallWin 'cmake'; InstallWin 'ninja'; foreach ($k in $PrebuiltParts.Keys) { InstallPrebuilt $k }
               if (NeedDotnet) { InstallWin 'dotnet' }
               if ($From -le 5 -and $To -ge 5) { EnsureFfmpeg }
               # no prebuilt harness: the ryubing tree of step 8 comes now (step 8 itself builds the harness, with the venv)
               if (-not (PrebuiltAsset 'harness') -and $From -le 8 -and $To -ge 8) { $script:RyubingOnly = $true; try { NxVsStep8 } finally { $script:RyubingOnly = $false } } }
}
function ChainPending([string]$name) {
    switch ($name) {
        'llvm' { return (-not (DetectWin 'llvm')) }
        'crt' { return (-not (DetectWin 'xwin') -or -not (XwinOk)) }
        'python' {
            if (-not (DetectWin 'python')) { return $true }
            $venvPy = PathOf $Framework 'private\venv\Scripts\python.exe'
            return ($From -le 6 -and $To -ge 6 -and -not (Test-Path -LiteralPath $venvPy))
        }
        'misc' {
            if (-not (DetectWin 'cmake') -or -not (DetectWin 'ninja')) { return $true }
            foreach ($k in $PrebuiltParts.Keys) { if ((PrebuiltAsset $k) -and -not (DetectPrebuilt $k)) { return $true } }
            if ((NeedDotnet) -and -not (DetectWin 'dotnet')) { return $true }
            return ($From -le 5 -and $To -ge 5 -and -not (FfmpegReady (PathOf $WorkDir "ffmpeg\$FfmpegDirName")))
        }
    }
    return $false
}
function RunChain([string]$name) {
    if (-not $XwinChains.Contains($name)) { Write-Host "ERROR: unknown chain $name"; return 1 }
    New-Item -ItemType Directory -Path $script:T -Force | Out-Null
    RefreshEnv
    try { & $XwinChains[$name] | Out-Host; return 0 }   # Out-Host: nothing of the chain leaks into the exit code
    catch { Write-Host "ERROR: $($_.Exception.Message)"; return 1 }
}

# ---------------------------------------------------------------------------------------------- steps (xwin)
function StartVcpkgWorker { }   # no vcpkg in this flow: step 5 runs in line (it only checks the prebuilt libraries)
${function:NxVsStep3} = ${function:Step3}
${function:NxVsStep7} = ${function:Step7}
${function:NxVsStep8} = ${function:Step8}

function Step0 {
    $os = [Environment]::OSVersion.Version
    Info ("PowerShell {0}; Windows {1}; {2}; administrator: {3}" -f $PSVersionTable.PSVersion, $os, $env:PROCESSOR_ARCHITECTURE, $(if (IsAdmin) { 'yes' } else { 'no' }))
    if ($os.Build -lt 17763) { Fail 'Windows 10 1809 (build 17763) or later is needed (tar.exe)' }
    if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { Fail "the port is x64; this Windows is $env:PROCESSOR_ARCHITECTURE" }
    if (-not (PrebuiltAsset 'deps')) { Fail '-Toolchain xwin needs the prebuilt pack (tools\internal\prebuilt.json with a "deps" asset; tools\dev\build_prebuilt.ps1 makes it): use -Toolchain vs' }
    if ($script:T -match '\s') { Warn "NX_TOOLS_DIR ($script:T) has spaces: if the build complains, set NX_TOOLS_DIR to a folder without spaces" }
    Info "toolchain: clang-cl + xwin, per user in $script:T (no VS Build Tools; -Toolchain vs for the previous flow); prebuilt pack $($script:Prebuilt.tag)"
    RefreshEnv
    if ($To -ge 1 -and -not $Check) {
        try { Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com' -Method Head -TimeoutSec 15 | Out-Null; Info 'network: github.com responds' }
        catch { Warn "github.com does not respond ($($_.Exception.Message)): the steps that download will fail" }
    }
    $eds = @(Editions)
    $nRoot = $SpaceExtract; foreach ($e in $eds) { $nRoot += $SpacePerEdition[$e] }
    foreach ($u in @(@($Root, $nRoot, "data and build of $($eds -join ' + ')"), @($script:T, $SpaceC.ReleaseOnly, 'tools (LLVM, CRT/SDK, CMake, Python, prebuilt pack)'))) {
        $q = $null
        try { $q = Split-Path -Qualifier ([IO.Path]::GetFullPath($u[0])) } catch { }
        if (-not $q) { continue }
        $d = Get-PSDrive -Name $q.TrimEnd(':') -ErrorAction SilentlyContinue
        if ($d -and $d.Free -lt $u[1]) { Warn ('{0}: {1:N0} GB free; ~{2:N0} GB recommended ({3}; estimate, does not block)' -f $q, ($d.Free / 1GB), ($u[1] / 1GB), $u[2]) }
        elseif ($d) { Info ('{0} {1:N0} GB free ({2})' -f $q, ($d.Free / 1GB), $u[2]) }
    }
    Info 'status of the tools:'
    foreach ($c in $ToolSpecs.Keys) {
        $e = Status $c
        $t = switch ($e.State) { 'ok' { "OK $($e.Det.Text)" } 'missing' { 'not present' } default { "$($e.Det.Text): does not meet the rule" } }
        Info ('  {0,-44} {1,-22} {2}' -f $ToolSpecs[$c].Name, $t, $ToolSpecs[$c].Rule)
    }
}

# Step 1: everything per user and in parallel (chains); the venv goes in the python chain.
function Step1 {
    New-Item -ItemType Directory -Path $script:T -Force | Out-Null
    $todo = @($XwinChains.Keys | Where-Object { ChainPending $_ })
    if ($Check) {
        foreach ($c in $ToolSpecs.Keys) { if ((Status $c).State -ne 'ok' -and $c -ne 'git') { Problem "$($ToolSpecs[$c].Name): not present ($($ToolSpecs[$c].Rule))" } }
        return
    }
    if ($todo -contains 'crt' -and -not (XwinOk) -and $env:XWIN_ACCEPT_LICENSE -ne 'true') {
        Info 'xwin downloads the MSVC CRT/STL and the Windows SDK from Microsoft. Their license:'
        Info '  https://go.microsoft.com/fwlink/?LinkId=2086102'
        if (-not (Ask 'Do you accept the Microsoft license of the CRT/SDK (it is the same one VS Build Tools asks for)?')) {
            Fail 'license not accepted: without the CRT/SDK the game cannot be built (or use -Toolchain vs; with -NonInteractive set XWIN_ACCEPT_LICENSE=true yourself)'
        }
        $env:XWIN_ACCEPT_LICENSE = 'true'   # only for this run and the chains it starts
    }
    if ($todo.Count) {
        $host_ = (Get-Process -Id $PID).Path
        $runs = @()
        foreach ($c in $todo) {
            $out = PathOf $WorkDir "chain_${c}_$(Stamp).log"
            $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $NxMainScript, '-Chain', $c, '-Toolchain', 'xwin', '-NonInteractive',
                   '-Root', $Root, '-Edition', $Edition, '-From', "$From", '-To', "$To")
            $p = Start-Process -FilePath $host_ -ArgumentList (($a | ForEach-Object { Quote $_ }) -join ' ') -NoNewWindow -PassThru -RedirectStandardOutput $out -RedirectStandardError "$out.err"
            $null = $p.Handle   # without this ExitCode can stay empty in PowerShell 5.1
            $runs += @{ C = $c; P = $p; Out = $out }
        }
        Info "in parallel: $($todo -join ', ') (the output of each one is shown when it finishes)"
        $failed = @()
        foreach ($r in $runs) {
            $r.P.WaitForExit()
            $code = -1; try { $code = $r.P.ExitCode } catch { }
            if ($null -eq $code) { $code = -1 }
            $took = ''; try { $took = ' ({0})' -f ($r.P.ExitTime - $r.P.StartTime).ToString('hh\:mm\:ss\.f') } catch { }
            Write-Host "   -- $($r.C)$took$(if ($code -ne 0) { ' FAILED' })" -ForegroundColor $(if ($code -ne 0) { 'Red' } else { 'Gray' })
            foreach ($f in @($r.Out, "$($r.Out).err")) {
                if (Test-Path -LiteralPath $f) { Get-Content -LiteralPath $f | Where-Object { $_ -match '\S' } | ForEach-Object { Write-Host "      $_" }; Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
            }
            if ($code -ne 0) { $failed += $r.C }
        }
        if ($failed.Count) { Fail "step 1: $($failed -join ', ') failed (output above); run again: what is done is kept" }
    } else { Info 'everything already in place' }
    RefreshEnv
    foreach ($c in $ToolSpecs.Keys) {
        if ($c -eq 'git') { continue }
        $e = Status $c
        if ($e.State -ne 'ok') { Fail "$($ToolSpecs[$c].Name): not present after step 1" }
        Info ('{0,-44} {1} ({2})' -f $ToolSpecs[$c].Name, $e.Det.Text, $e.Det.Path)
        $script:Done.Add("$($ToolSpecs[$c].Name) $($e.Det.Text)")
    }
    WriteToolchainJson
    Info "toolchain: $(PathOf $WorkDir 'toolchain.json') (read by tools\dev_env.ps1 and recomp_tsukihime.ps1)"
}
function Step2 { Info 'nothing: Python, Ninja and the venv come with step 1 (-Toolchain xwin)' }
function Step3 {
    if (-not (DetectGit)) {
        Warn 'Git is not installed: fine for -Toolchain xwin (it is only needed to update the repo with git pull)'
        if (-not (Test-Path -LiteralPath (PathOf $Framework 'runtime\src') -PathType Container)) { Problem "$Framework\runtime\src is missing: the framework must be inside the repo" }
        return
    }
    NxVsStep3
}
function Step4 { Info 'not needed: the libraries come from the prebuilt pack (-Toolchain xwin)' }
function Step5 {
    EnsureFfmpeg
    $d = DetectPrebuilt 'deps'
    if (-not $d) { Problem "prebuilt libraries missing ($(PrebuiltDir 'deps')): step 1"; return }
    Info "libraries: $(Join-Path $d.Path 'deps') (prebuilt pack $($d.Text))"
    $script:Done.Add("libraries (prebuilt pack $($d.Text))")
}
function Step7 {
    if (DetectPrebuilt 'nsextract') { Info "nsextract: $(Join-Path (PrebuiltDir 'nsextract') 'nsextract\nsextract.exe') (prebuilt)"; $script:Done.Add('nsextract (prebuilt)'); WriteToolchainJson; return }
    NxVsStep7
    WriteToolchainJson
}
function Step8 {
    if (DetectPrebuilt 'harness') { Info "spirv_harness: $(Join-Path (PrebuiltDir 'harness') 'spirv_harness\spirv_harness.exe') (prebuilt; no ryubing or .NET needed)"; $script:Done.Add('spirv_harness (prebuilt)'); WriteToolchainJson; return }
    NxVsStep8
    WriteToolchainJson
}
