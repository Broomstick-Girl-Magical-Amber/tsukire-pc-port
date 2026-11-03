# build_prebuilt.ps1 - Makes the prebuilt pack that install_requirements.ps1 -Toolchain xwin downloads instead of
# installing VS Build Tools, vcpkg, the Vulkan SDK and (optionally) the .NET SDK. Run by the MAINTAINER on a PC that already
# did the usual install (-Toolchain vs: vcpkg with the 5 ports, Vulkan SDK, .NET SDK 10, venv, ryubing tree).
#
#   powershell -ExecutionPolicy Bypass -File tools\dev\build_prebuilt.ps1 -Repo <owner>/<repo> -Tag prebuilt-2026.11.02
#   ... -NoHarness          without spirv_harness (users then build it: .NET SDK + ryubing)
#   ... -WithNsExtract      also nsextract (it embeds LibHac: read the warning below before publishing it)
#   ... -Vcpkg D:\vcpkg     another vcpkg folder (default C:\dev\vcpkg, the one of install_requirements.ps1)
#
# Output in <root>\_prepare\prebuilt\<tag>\ (zips) and tools\internal\prebuilt.json (URL + SHA-256 + size of each zip):
#   tsukire-deps-win-x64.zip       deps\ (include, lib, bin, share\sdl3 of opus, libjpeg-turbo, libpng, zlib, sdl3: x64-windows
#                                  Release from vcpkg), vulkan\Bin\spirv-val.exe, vulkan\Include\ (vulkan, vk_video headers),
#                                  redist\ (Visual C++ runtime DLLs: the game and those DLLs are /MD; VS Build Tools used to
#                                  install the runtime, the xwin flow does not: recomp copies them next to the exe),
#                                  licenses\, MANIFEST.txt (versions and SHA-256 of every file)
#   tsukire-harness-win-x64.zip    spirv_harness\spirv_harness.exe (self-contained .NET 10, single file; Ryujinx shader
#                                  translator, MIT) + licenses\
#   tsukire-nsextract-win-x64.zip  nsextract\nsextract.exe (self-contained; LibHac) + licenses\   (only with -WithNsExtract)
# Nothing of the game goes into any zip (no keys, no dumps, no generated code, no shaders): only third-party libraries and
# tools built from source. Then publish the zips as a GitHub release with that tag and commit tools\internal\prebuilt.json
# (the script prints the commands). Until prebuilt.json is committed, install_requirements.ps1 keeps using VS Build Tools.
param(
    [string]$Repo = '',
    [string]$Tag = ('prebuilt-' + (Get-Date -Format 'yyyy.MM.dd')),
    [string]$BaseUrl = '',
    [string]$Vcpkg = 'C:\dev\vcpkg',
    [switch]$NoHarness,
    [switch]$WithNsExtract,
    [string]$Root = '',
    [string]$MinCrt = '14.51'   # the xwin CRT of install_requirements.ps1 (-XwinCrt): the runtime DLLs must not be older
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$ProgressPreference = 'SilentlyContinue'

function Info([string]$t) { Write-Host "   $t" }
function Warn([string]$t) { Write-Host "   WARNING: $t" -ForegroundColor Yellow }
function Step([string]$t) { Write-Host ''; Write-Host "== $t" -ForegroundColor Cyan }
function Run([string]$exe, [string[]]$arguments, [string]$what) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $exe @arguments 2>&1 | ForEach-Object { Write-Host "     $_" }; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
    if ($code -ne 0) { throw "$what failed (code $code)" }
}
function Sha256([string]$p) { (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash }
function CopyTree([string]$src, [string]$dst) {
    if (-not (Test-Path -LiteralPath $src)) { throw "$src does not exist" }
    New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
    Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force
}
function Zip([string]$stage, [string]$zip) {
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Run "$env:SystemRoot\System32\tar.exe" @('-a', '-c', '-f', $zip, '-C', $stage, '.') "tar (zip $(Split-Path -Leaf $zip))"
}
function Manifest([string]$stage, [string[]]$header) {
    $base = (Resolve-Path -LiteralPath $stage).Path.TrimEnd('\')
    $lines = @($header) + @('', 'SHA-256 of every file:')
    foreach ($f in Get-ChildItem -LiteralPath $base -Recurse -File | Sort-Object FullName) {
        $lines += ('{0}  {1}' -f (Sha256 $f.FullName), $f.FullName.Substring($base.Length + 1))
    }
    [IO.File]::WriteAllText((Join-Path $base 'MANIFEST.txt'), (($lines -join "`r`n") + "`r`n"), (New-Object Text.UTF8Encoding($false)))
}
# A .NET program published as one exe: it must start without the SDK (prints its usage with no arguments).
function SmokeTest([string]$exe, [string]$expect) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $savedPath = $env:PATH; $savedRoot = $env:DOTNET_ROOT
    try {
        $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"; Remove-Item Env:DOTNET_ROOT -ErrorAction SilentlyContinue
        $out = @(& $exe 2>&1 | ForEach-Object { "$_" })
    } finally { $env:PATH = $savedPath; if ($savedRoot) { $env:DOTNET_ROOT = $savedRoot }; $ErrorActionPreference = $prev }
    if (-not (($out -join "`n") -match [regex]::Escape($expect))) { throw "$exe does not start on its own (output: $(($out | Select-Object -First 3) -join ' | '))" }
    Info "$(Split-Path -Leaf $exe) starts without the .NET SDK (OK)"
}

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

# A copy of vcruntime140*.dll next to a tool (harmless if it does not use them; a PC without Visual C++ cannot start a
# /MD program without them: that is how xwin.exe failed on the clean VM, 2026-11-02).
function VcRuntimeNextTo([string]$dir) {
    foreach ($f in @($rdFiles | Where-Object { $_.Name -match '^vcruntime140(_1)?\.dll$' })) { Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $dir $f.Name) -Force }
}
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }   # <root>\tools\dev -> <root>
$Root = [IO.Path]::GetFullPath($Root).TrimEnd('\')
if (-not $BaseUrl) {
    if (-not $Repo) { throw '-Repo <owner>/<repo> (where the release will be) or -BaseUrl <URL of the folder with the zips> is needed' }
    $BaseUrl = "https://github.com/$Repo/releases/download/$Tag"
}
$BaseUrl = $BaseUrl.TrimEnd('/')
$Out = Join-Path $Root "_prepare\prebuilt\$Tag"
if (Test-Path -LiteralPath $Out) { Remove-Item -LiteralPath $Out -Recurse -Force }
New-Item -ItemType Directory -Path $Out -Force | Out-Null
$assets = [ordered]@{}
$sources = @{}   # harness / nsextract: fingerprint of the sources (install_requirements.ps1 ignores the exe if the repo has other ones)
Write-Host "build_prebuilt: root $Root, tag $Tag, output $Out"
Write-Host "release URL: $BaseUrl"

# ---------------------------------------------------------------------------------------------- deps
Step 'deps: vcpkg libraries + spirv-val + Vulkan headers'
$inst = Join-Path $Vcpkg 'installed\x64-windows'
$need = @('include\opus\opus.h', 'lib\opus.lib', 'bin\opus.dll', 'include\jpeglib.h', 'lib\jpeg.lib', 'bin\jpeg62.dll', 'include\png.h',
          'lib\libpng16.lib', 'bin\libpng16.dll', 'include\zlib.h', 'include\SDL3\SDL.h', 'lib\SDL3.lib', 'bin\SDL3.dll',
          'share\sdl3\SDL3Config.cmake')
foreach ($f in $need) { if (-not (Test-Path -LiteralPath (Join-Path $inst $f))) { throw "$inst\$f is missing: run tools\install_requirements.ps1 -Toolchain vs -From 4 -To 5 first" } }
# zlib: the DLL name depends on the port version (zlib1.dll; tsukihime_common.cmake copies z.dll or zlib1.dll)
if (-not @('bin\zlib1.dll', 'bin\z.dll' | Where-Object { Test-Path -LiteralPath (Join-Path $inst $_) }).Count) { throw "$inst\bin has neither zlib1.dll nor z.dll (vcpkg zlib)" }
if (Test-Path -LiteralPath (Join-Path $inst 'debug')) { Warn "$inst has a debug\ folder (vcpkg with Debug): it is not copied" }
$vk = if ($env:VULKAN_SDK) { $env:VULKAN_SDK } else { [Environment]::GetEnvironmentVariable('VULKAN_SDK', 'Machine') }
if (-not $vk -or -not (Test-Path -LiteralPath (Join-Path $vk 'Bin\spirv-val.exe'))) { throw 'Vulkan SDK with Bin\spirv-val.exe not found (VULKAN_SDK)' }
$stage = Join-Path $Out 'stage_deps'
# Only the files of the 5 ports (vcpkg's own list of each port), not everything installed\x64-windows has (another port,
# such as an old ffmpeg, would end up in deps\include ahead of the prebuilt FFmpeg headers).
$info = Join-Path $Vcpkg 'installed\vcpkg\info'
$copied = 0
foreach ($port in 'opus', 'libjpeg-turbo', 'libpng', 'zlib', 'sdl3') {
    $lst = @(Get-ChildItem -LiteralPath $info -Filter "${port}_*_x64-windows.list" -File -ErrorAction SilentlyContinue)
    if ($lst.Count -ne 1) { throw "vcpkg list of the port $port in $info\${port}_*_x64-windows.list: $($lst.Count) files (1 expected)" }
    foreach ($l in [IO.File]::ReadAllLines($lst[0].FullName)) {
        if ($l -notmatch '^x64-windows/((include|lib|bin)/.+|share/sdl3/.+)$' -or $l.EndsWith('/') -or $l -match '^x64-windows/lib/pkgconfig/' -or $l -match '\.pdb$') { continue }
        $rel = $Matches[1] -replace '/', '\'
        $src = Join-Path $inst $rel
        if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { continue }
        $dst = Join-Path $stage "deps\$rel"
        New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
        Copy-Item -LiteralPath $src -Destination $dst -Force
        $copied++
    }
}
foreach ($f in $need) { if (-not (Test-Path -LiteralPath (Join-Path $stage "deps\$f"))) { throw "deps\$f did not come from the vcpkg lists of the 5 ports" } }
Info "$copied files of opus, libjpeg-turbo, libpng, zlib, sdl3 (include, lib, bin, share\sdl3)"
New-Item -ItemType Directory -Path (Join-Path $stage 'vulkan\Bin'), (Join-Path $stage 'vulkan\Include'), (Join-Path $stage 'licenses') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $vk 'Bin\spirv-val.exe') -Destination (Join-Path $stage 'vulkan\Bin\spirv-val.exe')
foreach ($d in 'vulkan', 'vk_video') { if (Test-Path -LiteralPath (Join-Path $vk "Include\$d")) { CopyTree (Join-Path $vk "Include\$d") (Join-Path $stage "vulkan\Include\$d") } }
if (-not (Test-Path -LiteralPath (Join-Path $stage 'vulkan\Include\vulkan\vulkan.h'))) { throw "$vk\Include\vulkan\vulkan.h is missing" }
foreach ($port in 'opus', 'libjpeg-turbo', 'libpng', 'zlib', 'sdl3') {
    $c = Join-Path $inst "share\$port\copyright"
    if (Test-Path -LiteralPath $c) { Copy-Item -LiteralPath $c -Destination (Join-Path $stage "licenses\$port.txt") } else { Warn "no license file for $port ($c)" }
}
foreach ($n in 'LICENSE.txt', 'license.txt') { $l = Join-Path $vk $n; if (Test-Path -LiteralPath $l) { Copy-Item -LiteralPath $l -Destination (Join-Path $stage 'licenses\VulkanSDK-spirv-val-headers.txt'); break } }
# Visual C++ runtime (app-local deployment, allowed by the VS license for the redist files): from the VC\Redist folder of
# VS (the newest one), otherwise the copies installed in System32.
$rdSrc = $null; $rdVer = ''
$vsw = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (Test-Path -LiteralPath $vsw) {
    foreach ($vsPath in @(& $vsw -all -products * -property installationPath 2>$null)) {
        $base = Join-Path "$vsPath" 'VC\Redist\MSVC'
        if (-not (Test-Path -LiteralPath $base)) { continue }
        foreach ($vd in @(Get-ChildItem -LiteralPath $base -Directory | Where-Object { $_.Name -match '^\d+\.\d+\.\d+$' } | Sort-Object { [version]$_.Name } -Descending)) {
            $crt = @(Get-ChildItem -LiteralPath (Join-Path $vd.FullName 'x64') -Directory -Filter 'Microsoft.VC*.CRT' -ErrorAction SilentlyContinue)
            if ($crt.Count -and (Test-Path -LiteralPath (Join-Path $crt[0].FullName 'msvcp140.dll'))) {
                if (-not $rdSrc -or [version]$vd.Name -gt [version]$rdVer) { $rdSrc = $crt[0].FullName; $rdVer = $vd.Name }
                break
            }
        }
    }
}
$rdFiles = @()
if ($rdSrc) { $rdFiles = @(Get-ChildItem -LiteralPath $rdSrc -Filter '*.dll' -File) }
else {
    Warn 'no VC\Redist folder in VS: the Visual C++ runtime is taken from System32'
    $rdFiles = @('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll', 'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll', 'concrt140.dll') |
               ForEach-Object { Join-Path "$env:SystemRoot\System32" $_ } | Where-Object { Test-Path -LiteralPath $_ } | ForEach-Object { Get-Item -LiteralPath $_ }
}
if (-not @($rdFiles | Where-Object { $_.Name -eq 'msvcp140.dll' }).Count -or -not @($rdFiles | Where-Object { $_.Name -eq 'vcruntime140.dll' }).Count) { throw 'Visual C++ runtime (msvcp140.dll, vcruntime140.dll) not found in VS nor in System32' }
New-Item -ItemType Directory -Path (Join-Path $stage 'redist') -Force | Out-Null
foreach ($f in $rdFiles) {
    Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $stage "redist\$($f.Name)")
    # spirv-val too (if it is /MD it would not start on a PC without the runtime)
    if ($f.Name -match '^(vcruntime140(_1)?|msvcp140)\.dll$') { Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $stage "vulkan\Bin\$($f.Name)") }
}
$msvcp = (Get-Item -LiteralPath (Join-Path $stage 'redist\msvcp140.dll')).VersionInfo.FileVersion
$rdText = "Visual C++ runtime $msvcp ($(if ($rdSrc) { $rdSrc } else { 'System32' }); $(@($rdFiles).Count) DLLs)"
Info $rdText
$mv = [version]'0.0'; if ("$msvcp" -match '^(\d+)\.(\d+)') { $mv = [version]"$($Matches[1]).$($Matches[2])" }
if ($mv -lt [version]$MinCrt) { Warn "msvcp140.dll $msvcp is older than the xwin CRT ${MinCrt}: the STL headers of the build could need a newer runtime (update VS Build Tools)" }
[IO.File]::WriteAllText((Join-Path $stage 'licenses\VisualCpp-runtime.txt'), "redist\*.dll: Microsoft Visual C++ runtime ($msvcp), redistributable files of Visual Studio deployed next to the application (https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files).`r`n")
# spirv-val must run without the SDK in the PATH (no DLL of the SDK next to it)
$savedPath = $env:PATH
$code = -1; $v = '?'
$prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
try {
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $vo = @(& (Join-Path $stage 'vulkan\Bin\spirv-val.exe') --version 2>&1 | ForEach-Object { "$_" }); $code = $LASTEXITCODE
    if ($vo.Count) { $v = $vo[0] }
} finally { $env:PATH = $savedPath; $ErrorActionPreference = $prev }
if ($code -ne 0) { throw "the copied spirv-val.exe does not run on its own (code $code, $v)" }
Info "spirv-val runs on its own: $v"
$git = Get-Command git -ErrorAction SilentlyContinue
$prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'   # stderr of a native program is not an error here
try {
    $vcpkgHead = if ($git) { "$(& git -C $Vcpkg rev-parse HEAD 2>$null)" } else { '?' }
    $ports = @(& (Join-Path $Vcpkg 'vcpkg.exe') list 2>$null | ForEach-Object { "$_" } | Where-Object { $_ -match ':x64-windows' })
} finally { $ErrorActionPreference = $prev }
Manifest $stage (@("tsukire prebuilt deps $Tag (x64-windows, Release)", "built $(Get-Date -Format s) by tools\dev\build_prebuilt.ps1", $rdText,
                  "vcpkg $vcpkgHead", "Vulkan SDK $(Split-Path -Leaf $vk) (spirv-val: $v)", '', 'vcpkg list:') + $ports)
$zip = Join-Path $Out 'tsukire-deps-win-x64.zip'
Zip $stage $zip
$assets['deps'] = $zip

# ---------------------------------------------------------------------------------------------- harness
$dotnet = (Get-Command dotnet -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1)
$py = Join-Path $Root 'framework\private\venv\Scripts\python.exe'
if (-not $NoHarness) {
    Step 'spirv_harness: self-contained single-file exe'
    if (-not $dotnet) { throw 'dotnet (.NET SDK 10) not found' }
    $ry = Join-Path $Root 'framework\private\third_party\ryubing'
    if (-not (Test-Path -LiteralPath $ry) -or -not (Test-Path -LiteralPath $py)) { throw "ryubing ($ry) or the venv is missing: tools\install_requirements.ps1 -Toolchain vs -From 6 -To 8" }
    $stage = Join-Path $Out 'stage_harness'
    $proj = Join-Path $Root 'framework\tools\maxwell\spirv_harness\SpirvHarness.csproj'
    $env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'; $env:DOTNET_NOLOGO = '1'
    Run $dotnet.Source @('publish', $proj, '-c', 'Release', '-r', 'win-x64', '--self-contained', 'true', '-p:PublishSingleFile=true',
                         '-p:IncludeNativeLibrariesForSelfExtract=true', "-p:PatchPython=$py", '-p:NuGetAudit=false', '-o', (Join-Path $stage 'spirv_harness')) 'dotnet publish (spirv_harness)'
    Get-ChildItem -LiteralPath (Join-Path $stage 'spirv_harness') -Filter '*.pdb' | Remove-Item -Force
    SmokeTest (Join-Path $stage 'spirv_harness\spirv_harness.exe') 'spirv_harness --batch'
    VcRuntimeNextTo (Join-Path $stage 'spirv_harness')
    New-Item -ItemType Directory -Path (Join-Path $stage 'licenses') -Force | Out-Null
    foreach ($n in 'LICENSE.txt', 'LICENSE') { $l = Join-Path $ry $n; if (Test-Path -LiteralPath $l) { Copy-Item -LiteralPath $l -Destination (Join-Path $stage 'licenses\Ryujinx-shader-translator-MIT.txt'); break } }
    [IO.File]::WriteAllText((Join-Path $stage 'licenses\dotnet-runtime.txt'), "This exe embeds the .NET 10 runtime (MIT License): https://github.com/dotnet/runtime/blob/main/LICENSE.TXT`r`n")
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $rev = if ($git) { "$(& git -C $Root log -1 --format='%h %cs' -- framework/tools/maxwell 2>$null)" } else { '?' } } finally { $ErrorActionPreference = $prev }
    Manifest $stage @("tsukire prebuilt spirv_harness $Tag (win-x64, self-contained)", "built $(Get-Date -Format s) by tools\dev\build_prebuilt.ps1",
                      "source: framework\tools\maxwell\spirv_harness ($rev) + ryubing a82350bb77 (Codeberg, tree verified by install_requirements.ps1)")
    $zip = Join-Path $Out 'tsukire-harness-win-x64.zip'
    Zip $stage $zip
    $assets['harness'] = $zip
    $sources['harness'] = SourceStamp $Root $PrebuiltSources.harness
}

# ---------------------------------------------------------------------------------------------- nsextract
if ($WithNsExtract) {
    Step 'nsextract: self-contained single-file exe'
    Warn 'nsextract embeds LibHac. Publishing its binary exposes the release to the same takedown requests LibHac and similar tools have received; without it every PC builds nsextract (needs the .NET SDK, ~1 min). Your call.'
    if (-not $dotnet) { throw 'dotnet (.NET SDK 10) not found' }
    $stage = Join-Path $Out 'stage_nsextract'
    Run $dotnet.Source @('publish', (Join-Path $Root 'tools\nsextract\nsextract.csproj'), '-c', 'Release', '-r', 'win-x64', '--self-contained', 'true',
                         '-p:PublishSingleFile=true', '-p:IncludeNativeLibrariesForSelfExtract=true', '-p:NuGetAudit=false', '-o', (Join-Path $stage 'nsextract')) 'dotnet publish (nsextract)'
    Get-ChildItem -LiteralPath (Join-Path $stage 'nsextract') -Filter '*.pdb' | Remove-Item -Force
    SmokeTest (Join-Path $stage 'nsextract\nsextract.exe') 'usage: nsextract'
    VcRuntimeNextTo (Join-Path $stage 'nsextract')
    New-Item -ItemType Directory -Path (Join-Path $stage 'licenses') -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $stage 'licenses\README.txt'), "nsextract (tools\nsextract of the repo) embeds LibHac 0.19.0 (NuGet) and the .NET 10 runtime (MIT). Add LibHac's license file here before publishing.`r`n")
    Manifest $stage @("tsukire prebuilt nsextract $Tag (win-x64, self-contained)", "built $(Get-Date -Format s) by tools\dev\build_prebuilt.ps1")
    $zip = Join-Path $Out 'tsukire-nsextract-win-x64.zip'
    Zip $stage $zip
    $assets['nsextract'] = $zip
    $sources['nsextract'] = SourceStamp $Root $PrebuiltSources.nsextract
}

# ---------------------------------------------------------------------------------------------- prebuilt.json
Step 'tools\internal\prebuilt.json'
$j = [ordered]@{ version = 1; tag = $Tag; note = 'Made by tools/dev/build_prebuilt.ps1; read by tools/install_requirements.ps1 (-Toolchain xwin).'; assets = [ordered]@{} }
foreach ($k in $assets.Keys) {
    $f = Get-Item -LiteralPath $assets[$k]
    $j.assets[$k] = [ordered]@{ file = $f.Name; url = "$BaseUrl/$($f.Name)"; sha256 = (Sha256 $f.FullName); size = $f.Length }
    if ($sources.ContainsKey($k)) { $j.assets[$k]['source'] = $sources[$k] }
    Info ('{0,-10} {1}  {2:N1} MB  {3}' -f $k, $f.Name, ($f.Length / 1MB), $j.assets[$k].sha256)
}
$pj = Join-Path $Root 'tools\internal\prebuilt.json'
[IO.File]::WriteAllText($pj, (($j | ConvertTo-Json -Depth 4) + "`n"), (New-Object Text.UTF8Encoding($false)))
Info "written: $pj"
foreach ($d in Get-ChildItem -LiteralPath $Out -Directory -Filter 'stage_*') { Remove-Item -LiteralPath $d.FullName -Recurse -Force }

Write-Host ''
Write-Host 'Next (publish, then commit the pins):' -ForegroundColor Green
$files = ($assets.Values | ForEach-Object { "`"$_`"" }) -join ' '
if ($Repo) { Write-Host "  gh release create $Tag $files --repo $Repo --title `"$Tag`" --notes `"Prebuilt third-party libraries and tools for tools\install_requirements.ps1 (-Toolchain xwin). No game files.`"" }
else { Write-Host "  upload these files so that they are at $BaseUrl/<name>: $files" }
Write-Host '  git add tools/internal/prebuilt.json'
Write-Host "  git commit -m `"Feature(Install): Prebuilt pack $Tag`""
Write-Host '  (until prebuilt.json with the URLs is committed, install_requirements.ps1 keeps using VS Build Tools)'
