# install_requirements.ps1 - Gets a Windows PC ready to recompile Tsukihime (both editions) with recomp_tsukihime.ps1.
# On Linux it is launched by tools/linux/install_requirements_linux.sh (PowerShell 7): same steps, with the Linux tools
# defined in tools/linux/install_requirements_linux.ps1 (tarballs with a fixed SHA-256, xwin, nstool built from
# source) and the Windows exe cross-compiled.
#
# Before: clone the port repo into <root> (it includes framework\). This script does not clone.
#   powershell -ExecutionPolicy Bypass -File <root>\tools\install_requirements.ps1    # the root is the folder above tools\
#   .\install_requirements.ps1 -Root <clone-dir>           # only if the script is somewhere else
#   .\install_requirements.ps1 -Check                  # only checks: installs nothing, clones nothing, creates nothing
#   .\install_requirements.ps1 -From 5                 # resume (each step checks its own part again)
#   .\install_requirements.ps1 -CrossRecomp            # at the end, recomp_tsukihime.ps1 -From 0 -To 0 of the chosen editions
#   .\install_requirements.ps1 -Edition global         # only one edition (global | japan | both): space, cross check and summary
#   .\install_requirements.ps1 -VcpkgCache D:\vcpkgbin # vcpkg binary cache (another copy of %LOCALAPPDATA%\vcpkg\archives)
#
# Steps (if what the step does is already there and meets the rule, it is skipped):
#   0 precheck       Windows, PowerShell, winget, network, space; status of each tool
#                    (if winget is missing, step 1 or 2 installs it: App Installer per user, no Store or administrator)
#   1 system         VS Build Tools (C++), LLVM, CMake, Git, .NET SDK 10, Vulkan SDK  -> ONE single UAC for everything
#   2 user           Python 3.12 and Ninja (no administrator)
#   3 repo           checks the already cloned port repo (origin, branch, uncommitted changes) and that framework\ is inside
#   4 vcpkg          clone at the reference commit + bootstrap
#   5 libraries      vcpkg install ffmpeg opus libjpeg-turbo libpng zlib sdl3 (x64-windows Release only; the longest)
#   6 venv           framework\private\venv with tools\requirements.txt (exact versions)
#   7 nstool         tools\nstool.exe 1.9.2 (fixed SHA-256)
#   8 ryubing        private\third_party\ryubing (Codeberg tarball, verified tree) + spirv_harness
#   9 gpu            Vulkan 1.3 + dynamicRendering + synchronization2 (warning only)
#  10 cross check    recomp_tsukihime.ps1 -From 0 -To 0 (only with -CrossRecomp)
#  11 summary        what is ready, what is left to do by hand and the recomp_tsukihime.ps1 command
#
# After: keys in <root>\data\prod.keys and the .xci/.nsp of each edition in <root>\ (which one is which does not matter).
# Rules: does not read, copy or print prod.keys (it only checks whether the file exists); does not download dumps or
# anything of the game; does not modify the framework or Wonder (it creates the venv, extracts ryubing into private\ and
# builds the harness, which leaves its output in private\build). What is installed with a version that does not meet
# the rule is reported and not replaced without asking (-NonInteractive: does not ask and fails).
# Log in <root>\_prepare\install_requirements_<date>.log.
param(
    [string]$Root = '',
    [ValidateRange(0, 11)][int]$From = 0,
    [ValidateRange(0, 11)][int]$To = 11,
    [switch]$Check,
    [switch]$NonInteractive,
    [switch]$CrossRecomp,
    [string]$Vcpkg = 'C:\dev\vcpkg',
    [string]$NstoolFrom = '',
    [ValidateSet('global', 'japan', 'both')][string]$Edition = 'both',
    [switch]$VcpkgWithDebug,
    [string]$VcpkgCache = '',
    # Linux: versions of the MSVC CRT/STL and of the Windows SDK that xwin downloads (those of the reference PC:
    # VCToolsVersion 14.51.36231 -> 14.51, WindowsSDKVersion 10.0.26100.0 -> 10.0.26100). Not used on Windows.
    [string]$XwinCrt = '14.51',
    [string]$XwinSdk = '10.0.26100',
    # internal: the elevated instance that does step 1, and the tests
    [switch]$AdminOnly,
    [string]$List = '',
    [string]$ResultFile = '',
    [switch]$FunctionsOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$ProgressPreference = 'SilentlyContinue'   # the Invoke-WebRequest progress bar of PowerShell 5.1 makes downloads very slow

# ---------------------------------------------------------------------------------------------- references
# Versions of the reference PC (2026-10-19). Pin = the one installed; Meets = what is accepted if already present.
$ToolSpecs = [ordered]@{
    vs     = @{ Name = 'VS Build Tools (C++)'; Admin = $true; Winget = 'Microsoft.VisualStudio.BuildTools'; Pin = ''
                Rule = '17.x or 18.x with VC.Tools x64 (reference 18.9)'; Detect = 'DetectVS'; Meets = { param($v) $v.Major -ge 17 }
                Override = '--quiet --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended' }
    llvm   = @{ Name = 'LLVM (clang-cl)'; Admin = $true; Winget = 'LLVM.LLVM'; Pin = '23.1.3'
                Rule = '23.x'; Detect = 'DetectLLVM'; Meets = { param($v) $v.Major -eq 23 } }
    cmake  = @{ Name = 'CMake'; Admin = $true; Winget = 'Kitware.CMake'; Pin = '4.4.4'
                Rule = '4.x'; Detect = 'DetectCMake'; Meets = { param($v) $v.Major -eq 4 } }
    git    = @{ Name = 'Git'; Admin = $true; Winget = 'Git.Git'; Pin = '2.55.0.3'
                Rule = '>= 2.40'; Detect = 'DetectGit'; Meets = { param($v) $v -ge [version]'2.40' } }
    dotnet = @{ Name = '.NET SDK'; Admin = $true; Winget = 'Microsoft.DotNet.SDK.10'; Pin = '10.0.401'
                Rule = '10.0.x'; Detect = 'DetectDotnet'; Meets = { param($v) $v.Major -eq 10 -and $v.Minor -eq 0 } }
    vulkan = @{ Name = 'Vulkan SDK (spirv-val)'; Admin = $true; Winget = ''; Pin = '1.4.363.0'
                Rule = '>= 1.3.250 with Bin\spirv-val.exe'; Detect = 'DetectVulkan'; Meets = { param($v) $v -ge [version]'1.3.250' } }
    python = @{ Name = 'Python'; Admin = $false; Winget = 'Python.Python.3.12'; Pin = '3.12.10'; Scope = 'user'
                Rule = '3.12.x (the venv one)'; Detect = 'DetectPython'; Meets = { param($v) $v.Major -eq 3 -and $v.Minor -eq 12 } }
    ninja  = @{ Name = 'Ninja'; Admin = $false; Winget = 'Ninja-build.Ninja'; Pin = '1.13.2'
                Rule = '>= 1.11'; Detect = 'DetectNinja'; Meets = { param($v) $v -ge [version]'1.11' } }
}
$VcpkgCommit = 'bf19794383e8fb95eba5aa553235bc8ec2271688'
$VcpkgPorts = @('ffmpeg', 'opus', 'libjpeg-turbo', 'libpng', 'zlib', 'sdl3')
$VcpkgRepo = 'https://github.com/microsoft/vcpkg.git'
$NstoolTag = 'v1.9.2'
$NstoolSha256 = '844648190DD8688098887A369DA1126C1B3AB1C2A664FB603B01CB22E76B6CA9'
$RyubingUrl = 'https://codeberg.org/Software_arch/Ryubing/archive/a82350bb77.tar.gz'
# Ryubing tree of the reference PC: "path  SHA256" per file (without the bin\ and obj\ the build leaves), ordinal order,
# separated by LF, final LF; SHA-256 of that text in UTF-8.
$RyubingFiles = 4310
$RyubingHash = '20D9B5394F6B24316980BE1870ED22375CF684C88ADBB1D8782083F2905D8D8F'
# Space in the root per edition (romfs + exefs + gen + analysis + build; recomp first extracts the dump). Estimate:
# calibrate with what data\tsuki_global and data\tsuki + framework\build\<edition> take on the reference PC.
# Root drive (measured 2026-10-22): what stays per edition (extract\romfs + exefs, gen, analysis, build) plus, once,
# what step 2 of recomp uses while it extracts one edition (encrypted container/NCA, deleted at the end). The dumps are not
# counted: they can be elsewhere (-InputFile); in the root, add their size (recomp never deletes them).
$SpacePerEdition = @{ global = 24GB; japan = 22GB }
$SpaceExtract = 24GB
$SpaceC = @{ WithDebug = 25GB; ReleaseOnly = 18GB }   # VS Build Tools, SDKs, vcpkg with ffmpeg (buildtrees during the build)
$WingetStore = 'https://apps.microsoft.com/detail/9NBLGGH4NNS1'   # App Installer (winget), Microsoft Store

# ---------------------------------------------------------------------------------------------- utilities
$Sep = [IO.Path]::DirectorySeparatorChar
$OnWindows = ($env:OS -eq 'Windows_NT')
$OnLinux = [bool]((Test-Path variable:IsLinux) -and $IsLinux)   # PowerShell 7 on Linux (in 5.1 the variable does not exist)
function PathOf([string]$base, [string]$rel) { Join-Path $base ($rel -replace '[\\/]', $Sep) }

$script:Problems = New-Object System.Collections.Generic.List[string]
$script:Done = New-Object System.Collections.Generic.List[string]
$script:CurrentStep = 0
$script:GpuOk = $null
function Step([int]$n, [string]$name) { Write-Host ''; Write-Host "== step $n`: $name" -ForegroundColor Cyan }
function Info([string]$t) { Write-Host "   $t" }
function Warn([string]$t) { Write-Host "   WARNING: $t" -ForegroundColor Yellow }
function Fail([string]$t) { throw $t }
# With -Check a problem is noted and the run goes on; otherwise the step fails.
function Problem([string]$t) {
    if ($Check) { $script:Problems.Add("step $($script:CurrentStep): $t"); Write-Host "   MISSING: $t" -ForegroundColor Yellow }
    else { Fail $t }
}
function Ask([string]$t) {
    if ($NonInteractive) { Info "$t -> no (-NonInteractive)"; return $false }
    $r = Read-Host "   $t [y/N]"
    return ($r -match '^\s*(y|yes)\s*$')
}
function Quote([string]$a) { if ($a -match '[\s"]' -or $a -eq '') { '"' + $a.Replace('"', '\"') + '"' } else { $a } }
function Stamp { Get-Date -Format 'yyyyMMdd-HHmmss' }

# External program with the output captured (not shown).
function RunCapture([string]$exe, [string[]]$arguments) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $out = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" }); $code = $LASTEXITCODE }
    catch { $out = @("$($_.Exception.Message)"); $code = -1 }
    finally { $ErrorActionPreference = $prev }
    if ($null -eq $code) { $code = 0 }
    return @{ Code = $code; Output = $out }
}
# External program with the output visible (and in the log): git clone, vcpkg, pip, dotnet build.
function Run([string]$exe, [string[]]$arguments, [string]$what) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $exe @arguments 2>&1 | ForEach-Object { Write-Host "     $_" }; $code = $LASTEXITCODE }
    finally { $ErrorActionPreference = $prev }
    if ($code -ne 0) { Fail "$what failed (code $code)" }
}
function ParseVersion([string]$s) {
    $m = [regex]::Match("$s", '\d+(\.\d+){1,3}')
    if (-not $m.Success) { return $null }
    return [version]$m.Value
}
function Sha256([string]$p) { (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash }
function Download([string]$url, [string]$dst) {
    if ($OnWindows) { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 }
    Info "downloading $url"
    $tmp = "$dst.partial"
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $tmp
    Move-Item -LiteralPath $tmp -Destination $dst -Force
}
function IsAdmin {
    if (-not $OnWindows) { return $false }
    try { return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) }
    catch { return $false }
}
function Editions { if ($Edition -eq 'both') { @('global', 'japan') } else { @($Edition) } }
function NormalizeUrl([string]$u) { $u.Trim().TrimEnd('/').ToLower() -replace '\.git$', '' }

# PATH of this session: the Machine + User one from the registry (what the installers just left), plus the
# known folders of each tool, plus what the session already had. This way no new console is needed.
function RefreshEnv {
    if (-not $OnWindows) { return }
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($a in 'Machine', 'User') {
        $p = [Environment]::GetEnvironmentVariable('Path', $a)
        if ($p) { foreach ($x in $p -split ';') { if ($x) { $parts.Add([Environment]::ExpandEnvironmentVariables($x)) } } }
    }
    foreach ($x in @("$env:ProgramFiles\LLVM\bin", "$env:LOCALAPPDATA\Microsoft\WinGet\Links", "$env:ProgramFiles\CMake\bin",
                     "$env:ProgramFiles\Git\cmd", "$env:ProgramFiles\dotnet", "$env:LOCALAPPDATA\Microsoft\WindowsApps")) { if (Test-Path -LiteralPath $x) { $parts.Add($x) } }
    $vk = [Environment]::GetEnvironmentVariable('VULKAN_SDK', 'Machine')
    if ($vk) { $env:VULKAN_SDK = $vk; $parts.Add("$vk\Bin") }
    foreach ($x in $env:Path -split ';') { if ($x) { $parts.Add($x) } }
    $seen = @{}; $final = @()
    foreach ($x in $parts) { $k = $x.TrimEnd('\').ToLower(); if (-not $seen.ContainsKey($k)) { $seen[$k] = 1; $final += $x } }
    $env:Path = $final -join ';'
}

# ---------------------------------------------------------------------------------------------- detection
# Each Detect* returns @{ Version = [version]; Path; Text } or $null if it is not there.
function FirstThatRuns([string[]]$candidates, [string[]]$arguments, [string]$regex) {
    foreach ($c in $candidates) {
        if (-not $c) { continue }
        if (-not (Test-Path -LiteralPath $c)) { continue }
        $r = RunCapture $c $arguments
        if ($r.Code -ne 0) { continue }
        $m = [regex]::Match(($r.Output -join "`n"), $regex)
        if ($m.Success) { return @{ Version = (ParseVersion $m.Groups[1].Value); Path = $c; Text = $m.Groups[1].Value } }
    }
    return $null
}
function InPath([string]$name) {
    $c = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source } else { return $null }
}
function DetectVS {
    $vw = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vw)) { return $null }
    $req = @('-products', '*', '-latest', '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64')
    $p = (RunCapture $vw ($req + @('-property', 'installationPath'))).Output | Select-Object -First 1
    if ($p) {
        $v = (RunCapture $vw ($req + @('-property', 'installationVersion'))).Output | Select-Object -First 1
        return @{ Version = (ParseVersion $v); Path = $p; Text = $v }
    }
    # Without C++: only a Build Tools counts (the component gets added to it). Other products of the VS installer
    # (SSMS, for example) are not VS: if there is no Build Tools, VS 'is not there'.
    $bt = 'Microsoft.VisualStudio.Product.BuildTools'
    $p = (RunCapture $vw @('-products', $bt, '-latest', '-property', 'installationPath')).Output | Select-Object -First 1
    if (-not $p) { return $null }
    $prod = $bt
    return @{ Version = [version]'0.0'; Path = $p; Text = 'without the C++ component (VC.Tools x64)'; NoVC = $true; Product = $prod }
}
function DetectLLVM { FirstThatRuns @("$env:ProgramFiles\LLVM\bin\clang-cl.exe", (InPath 'clang-cl')) @('--version') 'clang version (\d+\.\d+\.\d+)' }
function DetectCMake {
    FirstThatRuns @("$env:ProgramFiles\CMake\bin\cmake.exe", "$env:LOCALAPPDATA\Microsoft\WinGet\Links\cmake.exe", (InPath 'cmake')) @('--version') 'cmake version (\d+\.\d+\.\d+)'
}
function DetectGit { FirstThatRuns @("$env:ProgramFiles\Git\cmd\git.exe", (InPath 'git')) @('--version') 'git version (\d+\.\d+\.\d+(\.windows\.\d+)?)' }
function DetectNinja { FirstThatRuns @("$env:LOCALAPPDATA\Microsoft\WinGet\Links\ninja.exe", (InPath 'ninja')) @('--version') '(\d+\.\d+\.\d+)' }
function DetectDotnet {
    $best = $null
    foreach ($c in @("$env:ProgramFiles\dotnet\dotnet.exe", (InPath 'dotnet'))) {
        if (-not $c -or -not (Test-Path -LiteralPath $c)) { continue }
        $r = RunCapture $c @('--list-sdks')
        if ($r.Code -ne 0) { continue }
        foreach ($l in $r.Output) {
            $v = ParseVersion (($l -split ' ')[0])
            if (-not $v) { continue }
            $ok = & $ToolSpecs.dotnet.Meets $v
            if (-not $best -or ($ok -and -not $best.Ok) -or ($ok -eq $best.Ok -and $v -gt $best.Version)) {
                $best = @{ Version = $v; Path = $c; Text = "$v"; Ok = $ok }
            }
        }
        if ($best) { break }
    }
    return $best
}
function DetectVulkan {
    $cands = @()
    if ($OnWindows) { $cands += [Environment]::GetEnvironmentVariable('VULKAN_SDK', 'Machine') }
    $cands += $env:VULKAN_SDK
    if (Test-Path -LiteralPath 'C:\VulkanSDK') { $cands += @(Get-ChildItem -LiteralPath 'C:\VulkanSDK' -Directory | ForEach-Object { $_.FullName }) }
    $best = $null
    foreach ($d in $cands) {
        if (-not $d -or -not (Test-Path -LiteralPath (PathOf $d 'Bin\spirv-val.exe'))) { continue }
        $v = ParseVersion (Split-Path $d -Leaf)
        if ($v -and (-not $best -or $v -gt $best.Version)) { $best = @{ Version = $v; Path = $d; Text = "$v" } }
    }
    return $best
}
function DetectPython {
    $cands = @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:ProgramFiles\Python312\python.exe")
    $py = InPath 'py'
    if ($py) {
        $r = RunCapture $py @('-3.12', '-c', 'import sys;print(sys.executable)')
        if ($r.Code -eq 0 -and $r.Output.Count) { $cands += $r.Output[-1].Trim() }
    }
    return (FirstThatRuns $cands @('-c', 'import sys;print(sys.version.split()[0])') '^(\d+\.\d+\.\d+)')
}
# Status of a tool: ok | version (present but does not meet the rule) | missing.
function Status([string]$key) {
    $h = $ToolSpecs[$key]
    $d = & $h.Detect
    if (-not $d) { return @{ State = 'missing'; Det = $null } }
    if ($d.Version -and (& $h.Meets $d.Version)) { return @{ State = 'ok'; Det = $d } }
    return @{ State = 'version'; Det = $d }
}

# ---------------------------------------------------------------------------------------------- installation
# winget: the reference version if the winget repository still has it; otherwise the highest one that meets the rule.
function PickVersion([string]$key, [string[]]$showOutput) {
    $h = $ToolSpecs[$key]
    $vs = @($showOutput | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^\d+(\.\d+)+$' })
    if ($vs -contains $h.Pin) { return $h.Pin }
    $ok = @($vs | Where-Object { $v = ParseVersion $_; $v -and (& $h.Meets $v) } | Sort-Object { ParseVersion $_ } -Descending)
    if ($ok.Count) { return $ok[0] }
    return $null
}
function WingetAvailable { [bool](InPath 'winget') }
# If there is no winget it installs it for the current user (no administrator or Store): App Installer and its
# dependencies, from the microsoft/winget-cli releases. Add-AppxPackage checks the signature; it is also required to be from Microsoft.
function EnsureWinget {
    if (WingetAvailable) { return }
    if (-not $OnWindows) { return }
    Warn 'there is no winget: installing App Installer (winget) for this user, from github.com/microsoft/winget-cli'
    $dir = PathOf $WorkDir 'winget'
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $base = 'https://github.com/microsoft/winget-cli/releases/latest/download'
    $bundle = PathOf $dir 'Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle'
    $depsZip = PathOf $dir 'DesktopAppInstaller_Dependencies.zip'
    $depsDir = PathOf $dir 'dependencies'
    $prevProg = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'
    try {
        if (-not (Test-Path -LiteralPath $bundle)) { Download "$base/Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle" $bundle }
        if (-not (Test-Path -LiteralPath $depsZip)) { Download "$base/DesktopAppInstaller_Dependencies.zip" $depsZip }
    } catch { Fail "could not download winget ($($_.Exception.Message)); install it from the Microsoft Store ($WingetStore) and run again" }
    finally { $ProgressPreference = $prevProg }
    $sig = Get-AuthenticodeSignature -LiteralPath $bundle
    if (-not $sig.SignerCertificate -or $sig.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
        Fail "the downloaded winget package is not signed by Microsoft (signature: $($sig.Status)); not installed"
    }
    if (Test-Path -LiteralPath $depsDir) { Remove-Item -LiteralPath $depsDir -Recurse -Force }
    Expand-Archive -LiteralPath $depsZip -DestinationPath $depsDir -Force
    $deps = @(Get-ChildItem -LiteralPath (PathOf $depsDir 'x64') -Filter '*.appx' -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    if (-not $deps.Count) { Fail "the winget dependencies zip does not have x64\*.appx (see $depsDir)" }
    Info "installing App Installer with $($deps.Count) dependencies (x64)"
    try {
        if ($PSVersionTable.PSEdition -eq 'Core') { Import-Module Appx -UseWindowsPowerShell -WarningAction SilentlyContinue }
        Add-AppxPackage -Path $bundle -DependencyPath $deps -ErrorAction Stop
    } catch { Fail "Add-AppxPackage could not install winget ($($_.Exception.Message)); install it from the Microsoft Store ($WingetStore) and run again" }
    RefreshEnv
    if (-not (WingetAvailable)) {
        # the WindowsApps alias sometimes does not appear until a new console: the package folder is used
        $pk = Get-AppxPackage -Name 'Microsoft.DesktopAppInstaller' -ErrorAction SilentlyContinue | Sort-Object Version -Descending | Select-Object -First 1
        if ($pk -and (Test-Path -LiteralPath (PathOf $pk.InstallLocation 'winget.exe'))) { $env:Path = "$($pk.InstallLocation);$env:Path" }
    }
    if (-not (WingetAvailable)) { Fail "winget was installed but it does not appear in the PATH: open a new console and run again (or install it from the Store: $WingetStore)" }
    $script:Done.Add("winget $((RunCapture 'winget' @('--version')).Output -join ' ') (installed now)")
    Info "winget $((RunCapture 'winget' @('--version')).Output -join ' ') installed"
}
function InstallWinget([string]$key) {
    $h = $ToolSpecs[$key]
    EnsureWinget
    $a = @('install', '--id', $h.Winget, '--exact', '--source', 'winget', '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity')
    if ($h.Pin) {
        $show = RunCapture 'winget' @('show', '--id', $h.Winget, '--exact', '--versions', '--source', 'winget', '--accept-source-agreements')
        $ver = PickVersion $key $show.Output
        if (-not $ver) { Fail "winget offers no version of $($h.Winget) that meets '$($h.Rule)'" }
        if ($ver -ne $h.Pin) { Warn "winget no longer has $($h.Winget) $($h.Pin): installing $ver (meets '$($h.Rule)')" }
        $a += @('--version', $ver)
    }
    if ($h.ContainsKey('Scope')) { $a += @('--scope', $h.Scope) }
    if ($h.ContainsKey('Override')) { $a += @('--override', $h.Override) } else { $a += '--silent' }
    Info "winget $($a -join ' ')"
    $r = RunCapture 'winget' $a
    foreach ($l in @($r.Output | Where-Object { $_ -match '\w' } | Select-Object -Last 3)) { Info "  $l" }
    # The winget exit code is not always reliable (already installed, pending reboot): judged by detection afterwards.
    if ($r.Code -ne 0) { Warn "winget finished with code $($r.Code)" }
}
function InstallVS {
    $d = DetectVS
    if ($d -and $d.ContainsKey('NoVC') -and $d.Product -eq 'Microsoft.VisualStudio.Product.BuildTools') {
        # Build Tools already installed without C++: winget would say "already installed"; the component is added with the VS installer.
        $setup = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\setup.exe"
        Info "adding the C++ component to $($d.Path)"
        $p = Start-Process -FilePath $setup -Wait -PassThru -ArgumentList ('modify --installPath ' + (Quote $d.Path) +
             ' --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --quiet --norestart')
        if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) { Fail "the VS installer finished with code $($p.ExitCode)" }
        return
    }
    InstallWinget 'vs'
}
# Vulkan SDK: official LunarG installer with the SHA-256 its API publishes.
function InstallVulkan {
    $ver = $ToolSpecs.vulkan.Pin
    $dl = PathOf $WorkDir 'downloads'
    New-Item -ItemType Directory -Path $dl -Force | Out-Null
    $exe = PathOf $dl "vulkan_sdk-$ver.exe"
    $sha = (Invoke-RestMethod -UseBasicParsing -Uri "https://sdk.lunarg.com/sdk/sha/$ver/windows/vulkan_sdk.exe.json").sha
    if ($sha -notmatch '^[0-9a-fA-F]{64}$') { Fail "LunarG did not return a SHA-256 for SDK $ver" }
    if (-not (Test-Path -LiteralPath $exe) -or (Sha256 $exe) -ne $sha.ToUpper()) {
        Download "https://sdk.lunarg.com/sdk/download/$ver/windows/vulkan_sdk.exe" $exe
    }
    if ((Sha256 $exe) -ne $sha.ToUpper()) { Remove-Item -LiteralPath $exe -Force; Fail 'the Vulkan SDK installer does not match the LunarG SHA-256' }
    Info "Vulkan SDK ${ver}: SHA-256 OK"
    $dst = "C:\VulkanSDK\$ver"
    $p = Start-Process -FilePath $exe -Wait -PassThru -ArgumentList ('--root ' + (Quote $dst) + ' --accept-licenses --default-answer --confirm-command install')
    if ($p.ExitCode -ne 0) { Fail "the Vulkan SDK installer finished with code $($p.ExitCode)" }
}
function InstallTool([string]$key) {
    switch ($key) {
        'vs' { InstallVS }
        'vulkan' { InstallVulkan }
        default { InstallWinget $key }
    }
}

# Elevated instance: this same script is relaunched with -AdminOnly (a single UAC) and leaves the result in a JSON.
function ElevateAndInstall([string[]]$keys) {
    $res = PathOf $WorkDir ("admin_$(Stamp).json")
    $host_ = (Get-Process -Id $PID).Path
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-AdminOnly', '-List', ($keys -join ','),
           '-ResultFile', $res, '-Root', $Root)
    Info "asking for administrator permission (one UAC) to install: $($keys -join ', ')"
    Info 'another window opens with the progress; this one waits until it finishes'
    try { $p = Start-Process -FilePath $host_ -Verb RunAs -Wait -PassThru -ArgumentList (($a | ForEach-Object { Quote $_ }) -join ' ') }
    catch { Fail "administrator permission was not granted ($($_.Exception.Message))" }
    if (-not (Test-Path -LiteralPath $res)) { Fail "the administrator installation left no result (code $($p.ExitCode)); see the admin_*.log in $WorkDir" }
    $j = Get-Content -LiteralPath $res -Raw | ConvertFrom-Json
    foreach ($c in $keys) {
        $pr = $j.PSObject.Properties[$c]
        $v = if ($pr) { "$($pr.Value)" } else { 'no result' }
        if ($v -eq 'ok') { Info "$($ToolSpecs[$c].Name): installed" } else { Warn "$($ToolSpecs[$c].Name): $v" }
    }
}
function AdminMode {
    $log = PathOf $WorkDir "admin_$(Stamp).log"
    try { Start-Transcript -Path $log | Out-Null } catch { }
    RefreshEnv
    $r = [ordered]@{}
    foreach ($c in ($List -split ',' | Where-Object { $_ })) {
        Write-Host "== $($ToolSpecs[$c].Name)" -ForegroundColor Cyan
        try { InstallTool $c; $r[$c] = 'ok' } catch { $r[$c] = "error: $($_.Exception.Message)"; Write-Host "   ERROR: $($_.Exception.Message)" -ForegroundColor Red }
    }
    [IO.File]::WriteAllText($ResultFile, ($r | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    try { Stop-Transcript | Out-Null } catch { }
}

# Steps 1 and 2: checks, decides what to install (asking about what is present in a version that does not meet the
# rule), installs and checks again with the refreshed PATH.
function StepTools([string[]]$keys, [bool]$admin) {
    $install = @()
    foreach ($c in $keys) {
        $h = $ToolSpecs[$c]; $e = Status $c
        switch ($e.State) {
            'ok' { Info ('{0,-24} {1} OK ({2})' -f $h.Name, $e.Det.Text, $e.Det.Path) }
            'missing' { if ($Check) { Problem "$($h.Name): not present" } else { Info ('{0,-24} not present: installing' -f $h.Name); $install += $c } }
            'version' {
                $t = "$($h.Name) $($e.Det.Text) in $($e.Det.Path) does not meet '$($h.Rule)'"
                if ($Check) { Problem $t }
                else {
                    Warn $t
                    $q = if ($c -eq 'vs') { 'Install VS Build Tools with C++ (besides what is already there)?' } else { "Install $($h.Name) $($h.Pin) (it may replace the installed one)?" }
                    if (Ask $q) { $install += $c } else { Fail "$t (not replaced without permission)" }
                }
            }
        }
    }
    if (-not $install.Count) { return }
    if (@($install | Where-Object { $ToolSpecs[$_].Winget }).Count) { EnsureWinget }   # before the UAC: it is per user
    if ($admin -and -not (IsAdmin)) { ElevateAndInstall $install }
    else { foreach ($c in $install) { Write-Host "   -- $($ToolSpecs[$c].Name)"; InstallTool $c } }
    RefreshEnv
    foreach ($c in $install) {
        $e = Status $c
        if ($e.State -ne 'ok') {
            $t = if ($e.Det) { "$($e.Det.Text) does not meet '$($ToolSpecs[$c].Rule)'" } else { 'does not appear after installing' }
            Fail "$($ToolSpecs[$c].Name): $t (if it asks to restart Windows, restart and come back with -From $($script:CurrentStep))"
        }
        Info ('{0,-24} {1} installed ({2})' -f $ToolSpecs[$c].Name, $e.Det.Text, $e.Det.Path)
        $script:Done.Add("$($ToolSpecs[$c].Name) $($e.Det.Text) (installed now)")
    }
}

# ---------------------------------------------------------------------------------------------- ryubing tree
function TreeInventory([string]$dir) {
    $base = [IO.Path]::GetFullPath($dir).TrimEnd('\', '/')
    $l = New-Object System.Collections.Generic.List[string]
    foreach ($f in Get-ChildItem -LiteralPath $base -Recurse -File -Force) {
        $p = $f.FullName.Substring($base.Length + 1).Replace('\', '/')
        if ($p -match '(^|/)(bin|obj)/') { continue }
        $l.Add("$p  " + (Sha256 $f.FullName))
    }
    $a = $l.ToArray(); [Array]::Sort($a, [StringComparer]::Ordinal)
    $txt = ($a -join "`n") + "`n"
    $sha = [Security.Cryptography.SHA256]::Create()
    $h = (($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($txt)) | ForEach-Object { $_.ToString('X2') }) -join '')
    return @{ Files = $a.Count; Hash = $h; Text = $txt }
}

# ---------------------------------------------------------------------------------------------- vulkaninfo
# Full vulkaninfo text: one block per GPU that starts with a "GPU<n>:" line.
function ParseVulkaninfo([string[]]$lines) {
    $gpus = @(); $cur = $null
    foreach ($l in $lines) {
        if ($l -match '^GPU(\d+):\s*$') { if ($cur) { $gpus += $cur }; $cur = @{ Id = [int]$Matches[1]; Name = '?'; Api = $null; Driver = '?'; Type = '?'; Dyn = $false; Sync2 = $false }; continue }
        if (-not $cur) { continue }
        if (-not $cur.Api -and $l -match '^\s*apiVersion\s*=\s*(\d+\.\d+\.\d+)') { $cur.Api = [version]$Matches[1] }
        elseif ($cur.Name -eq '?' -and $l -match '^\s*deviceName\s*=\s*(.+?)\s*$') { $cur.Name = $Matches[1] }
        elseif ($cur.Type -eq '?' -and $l -match '^\s*deviceType\s*=\s*(\S+)') { $cur.Type = $Matches[1] }
        elseif ($l -match '^\s*driverInfo\s*=\s*(.+?)\s*$') { $cur.Driver = $Matches[1] }
        elseif ($cur.Driver -eq '?' -and $l -match '^\s*driverVersion\s*=\s*(.+?)\s*$') { $cur.Driver = $Matches[1] }
        elseif ($l -match '^\s*dynamicRendering\s*=\s*true') { $cur.Dyn = $true }
        elseif ($l -match '^\s*synchronization2\s*=\s*true') { $cur.Sync2 = $true }
    }
    if ($cur) { $gpus += $cur }
    # vulkaninfo lists the same device once per registered ICD/driver: it is counted once.
    $seen = @{}; $unique = @()
    foreach ($g in $gpus) { $k = "$($g.Name)|$($g.Api)|$($g.Driver)"; if (-not $seen.ContainsKey($k)) { $seen[$k] = 1; $unique += $g } }
    return $unique
}

# ---------------------------------------------------------------------------------------------- steps
function Step0 {
    $os = [Environment]::OSVersion.Version
    Info ("PowerShell {0}; Windows {1}; {2}; administrator: {3}" -f $PSVersionTable.PSVersion, $os, $env:PROCESSOR_ARCHITECTURE, $(if (IsAdmin) { 'yes' } else { 'no' }))
    if ($OnWindows) {
        if ($os.Build -lt 17763) { Fail 'Windows 10 1809 (build 17763) or later is needed (winget, tar.exe)' }
        if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { Fail "the port is x64; this Windows is $env:PROCESSOR_ARCHITECTURE" }
    }
    if (IsAdmin) { Info 'running as administrator: it works, but the user parts (Python, venv, clones) are better done without elevation' }
    RefreshEnv
    $w = InPath 'winget'
    if ($w) { Info "winget $((RunCapture $w @('--version')).Output -join ' ')" }
    else { Warn "there is no winget: step 1/2 installs it (App Installer, no administrator); with -Check it is not installed" }
    if ($To -ge 3 -and -not $Check) {
        try { Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com' -Method Head -TimeoutSec 15 | Out-Null; Info 'network: github.com responds' }
        catch { Warn "github.com does not respond ($($_.Exception.Message)): the steps that download will fail" }
    }
    $eds = Editions
    $nRoot = $SpaceExtract; foreach ($e in $eds) { $nRoot += $SpacePerEdition[$e] }
    $nC = if ($VcpkgWithDebug) { $SpaceC.WithDebug } else { $SpaceC.ReleaseOnly }
    Info "editions: $($eds -join ' + ')$(if ($Edition -eq 'both') { ' (-Edition global|japan for only one)' })"
    $uses = @(@($Root, $nRoot, "data and build of $($eds -join ' + ')"), @('C:\', $nC, 'VS Build Tools, SDKs, vcpkg with ffmpeg'))
    $same = $false
    try { $same = (Split-Path -Qualifier ([IO.Path]::GetFullPath($Root))) -eq 'C:' } catch { }
    if ($same) { $uses = @(, @('C:\', ($nRoot + $nC), "data and build of $($eds -join ' + ') + tools")) }
    foreach ($u in $uses) {
        $q = $null
        try { $q = Split-Path -Qualifier ([IO.Path]::GetFullPath($u[0])) } catch { }
        if (-not $q) { continue }
        $d = Get-PSDrive -Name $q.TrimEnd(':') -ErrorAction SilentlyContinue
        if ($d -and $d.Free -lt $u[1]) { Warn ('{0}: {1:N0} GB free; ~{2:N0} GB recommended ({3}; estimate, does not block)' -f $q, ($d.Free / 1GB), ($u[1] / 1GB), $u[2]) }
        elseif ($d) { Info ('{0} {1:N0} GB free' -f $q, ($d.Free / 1GB)) }
    }
    Info 'status of the tools:'
    foreach ($c in $ToolSpecs.Keys) {
        $e = Status $c
        $t = switch ($e.State) { 'ok' { "OK $($e.Det.Text)" } 'missing' { 'not present' } default { "$($e.Det.Text): does not meet the rule" } }
        Info ('  {0,-24} {1,-28} rule: {2}' -f $ToolSpecs[$c].Name, $t, $ToolSpecs[$c].Rule)
    }
}

function Step1 { StepTools @('vs', 'llvm', 'cmake', 'git', 'dotnet', 'vulkan') $true }
function Step2 { StepTools @('python', 'ninja') $false }

function GitExe {
    $d = DetectGit
    if (-not $d) { Fail 'Git is missing (step 1)' }
    return $d.Path
}
# A single repo (the framework goes inside as framework\): the user clones it beforehand; here it is only checked. No clone or pull.
function Step3 {
    $git = GitExe
    if (-not (Test-Path -LiteralPath $Root)) { Fail "${Root} does not exist: clone the port repo there and run this script from <root>\tools" }
    if (-not (Test-Path -LiteralPath (PathOf $Root '.git'))) { Problem "$Root is not a git repo: clone the port repo into that folder" }
    else {
        $o = (RunCapture $git @('-C', $Root, 'remote', 'get-url', 'origin')).Output | Select-Object -First 1
        $b = (RunCapture $git @('-C', $Root, 'rev-parse', '--abbrev-ref', 'HEAD')).Output | Select-Object -First 1
        $dirty = @((RunCapture $git @('-C', $Root, 'status', '--porcelain')).Output | Where-Object { $_ })
        $c = (RunCapture $git @('-C', $Root, 'log', '-1', '--format=%h %cs %s')).Output | Select-Object -First 1
        Info "repo $Root`: origin $o, branch $b, $c$(if ($dirty.Count) { "; $($dirty.Count) uncommitted changes" })"
        $script:Done.Add("repo $Root ($b)")
    }
    if (-not (Test-Path -LiteralPath (PathOf $Framework 'runtime\src') -PathType Container)) {
        Problem "$Framework\runtime\src is missing: the framework must be inside the repo, in <root>\framework"
    }
    if (Test-Path -LiteralPath (PathOf $Framework '.git')) {
        Warn "$Framework has its own .git (nested repo of the old layout): the port repo already includes it; move that folder aside if git in the root does not see its files"
    }
    foreach ($f in 'tools\recomp_tsukihime.ps1', 'tools\dev_env.ps1', 'tools\requirements.txt') {
        if (-not (Test-Path -LiteralPath (PathOf $Root $f))) { Warn "$f is missing in the repo" }
    }
}

function VcpkgExe { if ($OnLinux) { PathOf $Vcpkg 'vcpkg' } else { PathOf $Vcpkg 'vcpkg.exe' } }
function Step4 {
    $git = GitExe
    if (Test-Path -LiteralPath (PathOf $Vcpkg '.git')) {
        $head = ((RunCapture $git @('-C', $Vcpkg, 'rev-parse', 'HEAD')).Output | Select-Object -First 1)
        if ($head -eq $VcpkgCommit) { Info "vcpkg in $Vcpkg, reference commit" }
        else {
            $t = "vcpkg in $Vcpkg is at $head, not at $VcpkgCommit (the versions of ffmpeg and the rest may differ)"
            if ($Check) { Problem $t }
            elseif (Ask "$t. Move that vcpkg to the reference commit?") {
                Run $git @('-C', $Vcpkg, 'fetch', 'origin') 'git fetch (vcpkg)'
                Run $git @('-C', $Vcpkg, 'checkout', '-q', $VcpkgCommit) 'git checkout (vcpkg)'
            } else { Warn "$t; going on with that one" }
        }
    } elseif (Test-Path -LiteralPath $Vcpkg) {
        Problem "$Vcpkg exists and is not a git clone: check it or use -Vcpkg <another folder>"; return
    } elseif ($Check) {
        Problem "there is no vcpkg in $Vcpkg"; return
    } else {
        $tmp = "$Vcpkg.tmp"
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
        New-Item -ItemType Directory -Path (Split-Path $Vcpkg -Parent) -Force | Out-Null
        try {
            Run $git @('clone', $VcpkgRepo, $tmp) 'git clone vcpkg'
            Run $git @('-C', $tmp, 'checkout', '-q', $VcpkgCommit) 'git checkout (vcpkg)'
            Move-Item -LiteralPath $tmp -Destination $Vcpkg
        } catch { if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }; throw }
        Info "vcpkg cloned into $Vcpkg ($VcpkgCommit)"
    }
    if (-not (Test-Path -LiteralPath (VcpkgExe))) {
        if ($Check) { Problem "vcpkg without bootstrap ($(VcpkgExe))"; return }
        $boot = if ($OnLinux) { 'bootstrap-vcpkg.sh' } else { 'bootstrap-vcpkg.bat' }
        Run (PathOf $Vcpkg $boot) @('-disableMetrics') 'bootstrap-vcpkg'
    }
    if (-not (Test-Path -LiteralPath (VcpkgExe))) { Fail 'bootstrap-vcpkg did not leave vcpkg.exe' }
    $script:Done.Add("vcpkg $Vcpkg")
}

function MissingPorts {
    if (-not (Test-Path -LiteralPath (VcpkgExe))) { return $VcpkgPorts }
    $r = RunCapture (VcpkgExe) @('list')
    $have = @{}
    foreach ($l in $r.Output) { if ($l -match '^([a-z0-9-]+)(\[[^\]]*\])?:x64-windows\s') { $have[$Matches[1]] = 1 } }
    return @($VcpkgPorts | Where-Object { -not $have.ContainsKey($_) })
}
function Step5 {
    $env:VCPKG_ROOT = $Vcpkg                 # a VS console points it to its internal copy
    $env:VCPKG_DISABLE_METRICS = '1'
    $missing = @(MissingPorts)
    if (-not $missing.Count) { Info "x64-windows libraries present: $($VcpkgPorts -join ', ')"; $script:Done.Add('vcpkg libraries'); return }
    if ($Check) { Problem "vcpkg libraries missing: $($missing -join ', ')"; return }
    if (-not (Test-Path -LiteralPath (VcpkgExe))) { Fail "vcpkg is missing (step 4)" }
    $a = @('install') + $missing + @('--triplet', 'x64-windows', '--clean-after-build')
    if ($OnWindows) {
        try {
            $mp = Get-MpPreference -ErrorAction Stop
            $ex = @($mp.ExclusionPath | Where-Object { $_ })
            if (-not $mp.DisableRealtimeMonitoring -and -not ($ex | Where-Object { $Vcpkg.ToLower().StartsWith($_.TrimEnd('\').ToLower()) })) {
                Warn "Defender scans every file the ffmpeg build creates (thousands of MSYS2 processes): it can make it several times slower. If you want, as administrator: Add-MpPreference -ExclusionPath '$Vcpkg'"
            }
        } catch { }
    }
    if ($OnLinux) {
        # Cross build: x64-windows overlay triplet with the clang-cl + xwin toolchain (TripletLinux, in
        # tools/linux/install_requirements_linux.ps1). Always Release only: xwin does not have the Debug CRT.
        if ($VcpkgWithDebug) { Warn '-VcpkgWithDebug does not apply on Linux (xwin does not download the Debug CRT): Release only' }
        $ov = PathOf $WorkDir 'triplets'
        TripletLinux $ov
        $a += @("--overlay-triplets=$ov", '--host-triplet', 'x64-linux')
    } elseif (-not $VcpkgWithDebug) {
        # Overlay triplet with the SAME name (x64-windows) and VCPKG_BUILD_TYPE release: does not build the Debug half
        # (ffmpeg takes almost twice as long with it) and installed\x64-windows stays where the build and dev.local.ps1 look for it.
        $ov = PathOf $WorkDir 'triplets'
        New-Item -ItemType Directory -Path $ov -Force | Out-Null
        $base = PathOf $Vcpkg 'triplets\x64-windows.cmake'
        if (-not (Test-Path -LiteralPath $base)) { Fail "$base does not exist" }
        $txt = [IO.File]::ReadAllText($base).TrimEnd() + "`r`nset(VCPKG_BUILD_TYPE release)`r`n"
        [IO.File]::WriteAllText((PathOf $ov 'x64-windows.cmake'), $txt, (New-Object Text.UTF8Encoding($false)))
        $a += "--overlay-triplets=$ov"
        Info 'Release only (-VcpkgWithDebug to also build Debug)'
    }
    if ($VcpkgCache) {
        if (-not (Test-Path -LiteralPath $VcpkgCache)) { Fail "the cache $VcpkgCache does not exist" }
        # Only useful if the whole ABI matches: vcpkg commit, triplet and exact version of the VS compiler.
        $env:VCPKG_BINARY_SOURCES = "clear;default,readwrite;files,$VcpkgCache,read"
        Info "binary cache: $VcpkgCache (what matches is not built)"
    }
    Info "installing $($missing -join ', ') (ffmpeg builds from source: from 1 to several hours depending on the PC; if it is interrupted, -From 5 continues where it stopped)"
    Run (VcpkgExe) $a 'vcpkg install'
    $missing = @(MissingPorts)
    if ($missing.Count) { Fail "vcpkg finished but these are missing: $($missing -join ', ')" }
    $script:Done.Add('vcpkg libraries (installed now)')
}

function ReadRequirements {
    $req = PathOf $Root 'tools\requirements.txt'
    if (-not (Test-Path -LiteralPath $req)) { Fail "$req is missing (it comes with the repo: check the .gitignore and git add tools/requirements.txt)" }
    $h = [ordered]@{}
    foreach ($l in Get-Content -LiteralPath $req) {
        $l = $l.Trim(); if (-not $l -or $l.StartsWith('#')) { continue }
        if ($l -notmatch '^([A-Za-z0-9_.-]+)==(\S+)$') { Fail "requirements.txt: '$l' is not name==version (exact versions are pinned)" }
        $h[$Matches[1].ToLower().Replace('_', '-')] = $Matches[2]
    }
    return $h
}
# What requirements.txt asks for and is not there (or is in another version) according to pip freeze.
function FreezeDiff($req, [string[]]$freeze) {
    $have = @{}
    foreach ($l in $freeze) { if ($l -match '^([A-Za-z0-9_.-]+)==(\S+)$') { $have[$Matches[1].ToLower().Replace('_', '-')] = $Matches[2] } }
    $d = @()
    foreach ($k in $req.Keys) {
        if (-not $have.ContainsKey($k)) { $d += "$k (missing)" }
        elseif ($have[$k] -ne $req[$k]) { $d += "$k $($have[$k]) ($($req[$k]) requested)" }
    }
    return $d
}
function Step6 {
    $venv = PathOf $Framework 'private\venv'
    $py = if ($OnLinux) { PathOf $venv 'bin/python' } else { PathOf $venv 'Scripts\python.exe' }
    if ($Check -and -not (Test-Path -LiteralPath (PathOf $Root 'tools\requirements.txt'))) { Problem "$(PathOf $Root 'tools\requirements.txt') is missing"; return }
    $req = ReadRequirements
    if (Test-Path -LiteralPath $venv) {
        $cfg = PathOf $venv 'pyvenv.cfg'
        $ver = ''
        if (Test-Path -LiteralPath $cfg) { foreach ($l in Get-Content -LiteralPath $cfg) { if ($l -match '^\s*version(_info)?\s*=\s*(\S+)') { $ver = $Matches[2] } } }
        $v = ParseVersion $ver
        if (-not $v -or -not (& $ToolSpecs.python.Meets $v) -or -not (Test-Path -LiteralPath $py)) {
            $t = "the venv $venv is from Python '$ver' ($($ToolSpecs.python.Rule) requested)"
            if ($Check) { Problem $t; return }
            if (-not (Ask "$t. Set it aside as venv.old and create a new one?")) { Fail $t }
            if (Test-Path -LiteralPath "$venv.old") { Fail "$venv.old already exists: delete it by hand" }
            Move-Item -LiteralPath $venv -Destination "$venv.old"
            Warn "the previous venv was kept in $venv.old"
        }
    }
    if (-not (Test-Path -LiteralPath $venv)) {
        if ($Check) { Problem "there is no venv in $venv"; return }
        $base = DetectPython
        if (-not $base -or -not (& $ToolSpecs.python.Meets $base.Version)) { Fail 'Python 3.12 is missing (step 2)' }
        try { Run $base.Path @('-m', 'venv', $venv) 'python -m venv' }
        catch { if (Test-Path -LiteralPath $venv) { Remove-Item -LiteralPath $venv -Recurse -Force -ErrorAction SilentlyContinue }; throw }
        Info "venv created with $($base.Path) ($($base.Text))"
    }
    $d = @(FreezeDiff $req (RunCapture $py @('-m', 'pip', 'freeze')).Output)
    if ($d.Count) {
        if ($Check) { Problem "venv: $($d -join ', ')"; return }
        Info "venv: $($d -join ', ')"
        Run $py @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (PathOf $Root 'tools\requirements.txt')) 'pip install'
        $d = @(FreezeDiff $req (RunCapture $py @('-m', 'pip', 'freeze')).Output)
        if ($d.Count) { Fail "pip finished but the venv is still different: $($d -join ', ')" }
    }
    $lz = RunCapture $py @('-c', 'import lz4.block, capstone; print(capstone.__version__)')
    if ($lz.Code -ne 0) { Problem "the venv cannot import lz4/capstone: $($lz.Output -join ' | ')"; return }
    Info "venv OK: $(($req.Keys | ForEach-Object { "$_ $($req[$_])" }) -join ', ')"
    $script:Done.Add('venv (exact versions of requirements.txt)')
}

function Step7 {
    $dst = PathOf $Root 'tools\nstool.exe'
    if (Test-Path -LiteralPath $dst) {
        if ((Sha256 $dst) -eq $NstoolSha256) { Info "nstool.exe $NstoolTag OK (SHA-256)"; $script:Done.Add("nstool $NstoolTag"); return }
        $t = "$dst is not the reference nstool $NstoolTag (different SHA-256)"
        if ($Check) { Problem $t; return }
        if (-not (Ask "$t. Set it aside as nstool.exe.old and put the reference one?")) { Fail $t }
        if (Test-Path -LiteralPath "$dst.old") { Fail "$dst.old already exists: delete it by hand" }
        Move-Item -LiteralPath $dst -Destination "$dst.old"
    }
    if ($Check) { Problem "$dst is missing"; return }
    $tmp = PathOf $WorkDir "nstool_$(Stamp)"
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        if ($NstoolFrom) {
            $exe = $NstoolFrom
        } else {
            if ($OnWindows) { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 }
            $rel = Invoke-RestMethod -UseBasicParsing -Uri "https://api.github.com/repos/jakcron/nstool/releases/tags/$NstoolTag" -Headers @{ 'User-Agent' = 'install_requirements' }
            $as = @($rel.assets | Where-Object { $_.name -match 'win' -and $_.name -match 'x64' -and $_.name -match '\.zip$' })
            if ($as.Count -ne 1) { Fail "the $NstoolTag release of jakcron/nstool does not have a single win x64 zip ($(@($rel.assets | ForEach-Object { $_.name }) -join ', ')); use -NstoolFrom <nstool.exe>" }
            $zip = PathOf $tmp $as[0].name
            Download $as[0].browser_download_url $zip
            $dg = $as[0].PSObject.Properties['digest']
            if ($dg -and "$($dg.Value)" -match '^sha256:([0-9a-f]{64})$' -and (Sha256 $zip) -ne $Matches[1].ToUpper()) { Fail 'the nstool zip does not match the GitHub digest' }
            Expand-Archive -LiteralPath $zip -DestinationPath (PathOf $tmp 'x')
            $exe = @(Get-ChildItem -LiteralPath (PathOf $tmp 'x') -Recurse -Filter 'nstool.exe' -File | ForEach-Object { $_.FullName }) | Select-Object -First 1
            if (-not $exe) { Fail 'the nstool zip does not have nstool.exe' }
        }
        if (-not (Test-Path -LiteralPath $exe)) { Fail "$exe does not exist" }
        if ((Sha256 $exe) -ne $NstoolSha256) { Fail "nstool.exe from $(if ($NstoolFrom) { $NstoolFrom } else { 'GitHub' }) with SHA-256 $(Sha256 $exe), not the reference $NstoolSha256 (pass your copy with -NstoolFrom)" }
        New-Item -ItemType Directory -Path (Split-Path $dst -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $exe -Destination $dst
    } finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
    Info "nstool.exe $NstoolTag in $dst (SHA-256 OK)"
    $script:Done.Add("nstool $NstoolTag (placed now)")
}

function Step8 {
    $ry = PathOf $Framework 'private\third_party\ryubing'
    if (Test-Path -LiteralPath $ry) {
        $inv = TreeInventory $ry
        if ($inv.Hash -eq $RyubingHash) { Info "ryubing a82350bb77: tree identical to the reference one ($($inv.Files) files)" }
        else {
            $f = PathOf $WorkDir "ryubing_inv_$(Stamp).txt"
            if (-not $Check) { [IO.File]::WriteAllText($f, $inv.Text, (New-Object Text.UTF8Encoding($false))) }
            $t = "ryubing in $ry is not the reference tree ($($inv.Files) files, $RyubingFiles expected)"
            if ($Check) { Problem $t; return }
            Warn "$t; inventory in $f"
            if (-not (Ask 'Set it aside as ryubing.old and download the Codeberg one?')) { Fail $t }
            if (Test-Path -LiteralPath "$ry.old") { Fail "$ry.old already exists: delete it by hand" }
            Move-Item -LiteralPath $ry -Destination "$ry.old"
        }
    }
    if (-not (Test-Path -LiteralPath $ry)) {
        if ($Check) { Problem "$ry is missing" }
        else {
            $dl = PathOf $WorkDir 'downloads'; New-Item -ItemType Directory -Path $dl -Force | Out-Null
            $tgz = PathOf $dl 'ryubing-a82350bb77.tar.gz'
            if (-not (Test-Path -LiteralPath $tgz)) { Download $RyubingUrl $tgz }
            $tmp = PathOf $WorkDir 'ryubing.tmp'
            if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
            New-Item -ItemType Directory -Path $tmp | Out-Null
            try {
                $tar = if ($OnWindows) { "$env:SystemRoot\System32\tar.exe" } else { 'tar' }
                Run $tar @('-xzf', $tgz, '-C', $tmp, '--strip-components=1') 'tar (ryubing)'
                $inv = TreeInventory $tmp
                if ($inv.Hash -ne $RyubingHash) {
                    $f = PathOf $WorkDir "ryubing_inv_$(Stamp).txt"
                    [IO.File]::WriteAllText($f, $inv.Text, (New-Object Text.UTF8Encoding($false)))
                    Remove-Item -LiteralPath $tgz -Force
                    Fail "the Codeberg tarball does not give the reference tree ($($inv.Files) files, $RyubingFiles expected); inventory in $f"
                }
                New-Item -ItemType Directory -Path (Split-Path $ry -Parent) -Force | Out-Null
                Move-Item -LiteralPath $tmp -Destination $ry
            } catch { if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }; throw }
            Info "ryubing extracted into $ry ($RyubingFiles files, verified tree)"
        }
    }
    $dll = PathOf $Framework 'private\build\spirv_harness\bin\Release\net10.0\spirv_harness.dll'
    if (Test-Path -LiteralPath $dll) { Info "spirv_harness.dll present ($((Get-Item -LiteralPath $dll).LastWriteTime))"; $script:Done.Add('ryubing + spirv_harness'); return }
    if ($Check) { Problem "$dll is missing"; return }
    $dn = DetectDotnet
    if (-not $dn -or -not $dn.Ok) { Fail '.NET SDK 10 is missing (step 1)' }
    $py = if ($OnLinux) { PathOf $Framework 'private/venv/bin/python' } else { PathOf $Framework 'private\venv\Scripts\python.exe' }
    if (-not (Test-Path -LiteralPath $py)) { Fail 'the venv is missing (step 6): the harness build applies its patches with apply_patch.py' }
    $env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'; $env:DOTNET_NOLOGO = '1'
    Push-Location $Framework
    try { Run $dn.Path @('build', 'tools/maxwell/spirv_harness/SpirvHarness.csproj', '-c', 'Release', '-m:2', "-p:PatchPython=$py", '-p:NuGetAudit=false') 'dotnet build (spirv_harness)' }
    finally { Pop-Location }
    if (-not (Test-Path -LiteralPath $dll)) { Fail "the build finished but $dll does not appear" }
    Info 'spirv_harness built'
    $script:Done.Add('ryubing + spirv_harness (built now)')
}

function Step9 {
    $vi = $null
    $vk = DetectVulkan
    $viNames = if ($OnLinux) { @('bin/vulkaninfo') } else { @('Bin\vulkaninfoSDK.exe', 'Bin\vulkaninfo.exe') }
    if ($vk) { foreach ($n in $viNames) { $p = PathOf $vk.Path $n; if (Test-Path -LiteralPath $p) { $vi = $p; break } } }
    if (-not $vi -and $OnWindows -and (Test-Path -LiteralPath "$env:SystemRoot\System32\vulkaninfo.exe")) { $vi = "$env:SystemRoot\System32\vulkaninfo.exe" }
    if (-not $vi) { $vi = InPath 'vulkaninfo' }
    if (-not $vi) { Warn 'cannot find vulkaninfo (it comes with the Vulkan SDK): the GPU could not be checked'; $script:GpuOk = $null; return }
    $r = RunCapture $vi @()
    $gpus = @(ParseVulkaninfo $r.Output)
    if (-not $gpus.Count) { Warn "vulkaninfo lists no GPU (code $($r.Code)): driver without Vulkan?"; $script:GpuOk = $false; return }
    $any = $false
    foreach ($g in $gpus) {
        $ok = $g.Api -and $g.Api -ge [version]'1.3' -and $g.Dyn -and $g.Sync2
        $missing = @()
        if (-not $g.Api -or $g.Api -lt [version]'1.3') { $missing += "Vulkan $($g.Api)" }
        if (-not $g.Dyn) { $missing += 'no dynamicRendering' }
        if (-not $g.Sync2) { $missing += 'no synchronization2' }
        $t = "GPU $($g.Id): $($g.Name) ($($g.Type)), Vulkan $($g.Api), driver $($g.Driver)"
        if ($ok) { Info "$t -> OK"; if ($g.Type -notmatch 'CPU') { $any = $true } } else { Warn "$t -> not usable to play: $($missing -join ', ')" }
    }
    $script:GpuOk = $any
    if ($any) { $script:Done.Add('GPU with Vulkan 1.3 to play') }
    elseif ($OnLinux) { Warn 'no GPU is usable to play (building is possible): in a VM it is normal (llvmpipe); on a PC, Mesa or NVIDIA Vulkan driver' }
    else { Warn 'no GPU is usable to play (building is possible): update the driver from the manufacturer (Intel Driver & Support Assistant, NVIDIA, AMD)' }
}

function Step10 {
    if (-not $CrossRecomp) { Info 'skipped (no -CrossRecomp)'; return }
    $m = PathOf $Root 'tools\recomp_tsukihime.ps1'
    if (-not (Test-Path -LiteralPath $m)) { Problem "$m is missing"; return }
    $host_ = (Get-Process -Id $PID).Path
    foreach ($ed in (Editions)) {
        Info "recomp_tsukihime.ps1 -Edition $ed -From 0 -To 0"
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $m, '-Edition', $ed, '-From', '0', '-To', '0', '-Root', $Root)
        $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        try { & $host_ @a 2>&1 | ForEach-Object { Write-Host "     $_" }; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
        if ($code -ne 0) { Problem "recomp_tsukihime.ps1 -Edition $ed (step 0) failed" } else { $script:Done.Add("recomp_tsukihime.ps1 step 0 ($ed) OK") }
    }
}

function Step11 {
    $data = PathOf $Root 'data'
    Write-Host ''
    Write-Host '   Status:' -ForegroundColor Green
    foreach ($c in $ToolSpecs.Keys) {
        $e = Status $c
        $t = switch ($e.State) { 'ok' { "OK $($e.Det.Text)" } 'missing' { 'MISSING' } default { "$($e.Det.Text) (does not meet $($ToolSpecs[$c].Rule))" } }
        Write-Host ('     {0,-24} {1}' -f $ToolSpecs[$c].Name, $t)
    }
    foreach ($l in $script:Done) { if ($l -notmatch '^(VS |LLVM|CMake|Git |\.NET|Vulkan|Python|Ninja)') { Write-Host "     $l" } }
    if ($null -ne $script:GpuOk) { Write-Host ('     GPU to play: {0}' -f $(if ($script:GpuOk) { 'OK' } else { 'NO (see the warning of step 9)' })) }

    Write-Host ''
    Write-Host '   By hand (the script does not touch it):' -ForegroundColor Yellow
    $keys = PathOf $data 'prod.keys'
    Write-Host ("     prod.keys   {0}" -f $(if (Test-Path -LiteralPath $keys) { "present in $keys (not read)" } else { "copy your keys to $keys" }))
    # The dumps go in the ROOT (an .xci or an .nsp, whichever and of whichever edition: recomp_tsukihime.ps1 looks at the program ID).
    $dumps = @()
    if (Test-Path -LiteralPath $Root) {
        $dumps = @(Get-ChildItem -LiteralPath $Root -File | Where-Object { $_.Extension -in '.xci', '.nsp' } | ForEach-Object { $_.Name })
    }
    $eds = Editions
    $needed = if ($eds.Count -gt 1) { 'one dump per edition' } else { 'the dump' }
    Write-Host ("     dumps       {0}" -f $(if ($dumps.Count) { "in $Root\: " + ($dumps -join ', ') } else { "copy $needed (.xci or .nsp) to $Root\ (the root, not data\)" }))
    $font = @((PathOf $data 'tsuki_global\fonts\shared_0.ttf'), (PathOf $data 'tsuki\fonts\shared_0.ttf')) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    Write-Host ("     font        {0}" -f $(if ($font) { "present ($font)" } else { 'shared_0.ttf in any folder; passed with -Fonts the first time' }))

    $m = PathOf $Root 'tools\recomp_tsukihime.ps1'
    # recomp_tsukihime.ps1 works out the root from its own location (<root>\tools); -Root only if another one was asked here.
    $extra = ''
    $hereTools = Split-Path -Parent $PSCommandPath
    if ((Split-Path -Parent $hereTools).TrimEnd('\', '/') -ne [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')) { $extra = " -Root `"$Root`"" }
    $fo = if ($font) { '' } else { ' -Fonts <folder with shared_0.ttf>' }
    Write-Host ''
    Write-Host '   Next (in this same console):' -ForegroundColor Cyan
    $dev = @((PathOf $Root 'tools\dev.local.ps1'), (PathOf $Root 'tools\dev_env.ps1')) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $dev) { $dev = PathOf $Root 'tools\dev_env.ps1' }
    Write-Host "     . `"$dev`"    (optional: recomp loads it by itself)"
    # -Fonts goes with the first edition recompiled; the second one takes the font from the first.
    if ($eds -contains 'global') { Write-Host "     & `"$m`" -Edition global$fo$extra"; $fo = '' }
    if ($eds -contains 'japan') { Write-Host "     & `"$m`" -Edition japan$fo$extra" }
    if ($script:Problems.Count) {
        Write-Host ''
        Write-Host '   Problems:' -ForegroundColor Yellow
        foreach ($p in $script:Problems) { Write-Host "     $p" }
    }
}

# ---------------------------------------------------------------------------------------------- main
function ResolveRoot {
    if ($Root) { return [IO.Path]::GetFullPath($Root) }
    $here = Split-Path -Parent $PSCommandPath
    $up = Split-Path -Parent $here
    if ((Split-Path -Leaf $here) -eq 'tools' -and ((Test-Path -LiteralPath (PathOf $up '.git')) -or (Test-Path -LiteralPath (PathOf $up 'framework')))) { return $up }
    throw '-Root is missing (the port folder, e.g. -Root C:\src\tsukire-pc-recomp): this script is not in <root>\tools of a clone'
}

# Linux: tools, steps 0-2, 7 and 11 of its own (they redefine the functions above; the rest is shared).
if ($OnLinux) {
    $nxLinux = Join-Path $PSScriptRoot 'linux/install_requirements_linux.ps1'
    if (-not (Test-Path -LiteralPath $nxLinux)) { Write-Host "ERROR: $nxLinux is missing (is it in the repo? check that the .gitignore lets tools/linux/*.ps1 through)" -ForegroundColor Red; exit 1 }
    . $nxLinux
}

$StepNames = @('precheck', 'system (administrator)', 'user', 'repos', 'vcpkg', 'vcpkg libraries', 'venv', 'nstool',
               'ryubing + harness', 'gpu', 'cross check with recomp_tsukihime', 'summary')

function Main {
    if ($From -gt $To) { throw "-From $From greater than -To $To" }
    $script:Root = ResolveRoot
    $script:Framework = PathOf $script:Root 'framework'
    $script:WorkDir = if ((Test-Path -LiteralPath $script:Root) -or -not $Check) { PathOf $script:Root '_prepare' } else { PathOf ([IO.Path]::GetTempPath()) 'tsuki_prepare' }
    New-Item -ItemType Directory -Path $script:WorkDir -Force | Out-Null
    if ($AdminOnly) { AdminMode; return 0 }
    $log = PathOf $script:WorkDir "install_requirements_$(Stamp).log"
    try { Start-Transcript -Path $log | Out-Null } catch { $log = '(no log)' }
    Write-Host "install_requirements: root $script:Root, edition $Edition, steps $From..$To$(if ($Check) { ', check only' })$(if ($NonInteractive) { ', no questions' }); log $log"
    $code = 0
    $encPrev = $null
    try { $encPrev = [Console]::OutputEncoding; [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false) } catch { }
    try {
        $script:CurrentStep = 0; Step 0 $StepNames[0]; Step0
        for ($n = [Math]::Max(1, $From); $n -le $To; $n++) {
            $script:CurrentStep = $n
            Step $n $StepNames[$n]
            & "Step$n"
        }
        if ($To -lt 11) { Write-Host ''; Write-Host "   (summary: step 11)" }
        if ($script:Problems.Count) { $code = 1 }
    } catch {
        Write-Host ''
        Write-Host "ERROR in step $($script:CurrentStep): $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Resume with -From $($script:CurrentStep) once it is fixed (log: $log)." -ForegroundColor Red
        $code = 1
    } finally {
        if ($encPrev) { try { [Console]::OutputEncoding = $encPrev } catch { } }
        try { Stop-Transcript | Out-Null } catch { }
    }
    if ($code -eq 0) { Write-Host ''; Write-Host 'Done.' -ForegroundColor Green }
    return $code
}

if ($FunctionsOnly) { return }
$c = Main
exit $c