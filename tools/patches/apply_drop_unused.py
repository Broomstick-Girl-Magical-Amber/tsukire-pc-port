r"""apply_drop_unused.py - removes dependencies the port no longer uses. Marker AP_DROP_UNUSED.

1. nstool (the fallback extractor). Since nsextract (tools\nsextract, C# + LibHac) is the default, nstool only ran when
   nsextract could not be built or with -NoNsExtract. Gone:
   - recomp_tsukihime.ps1: -NoNsExtract, the nstool path of steps 1-2 (Nstool, NstoolLong, ExtractFile, PartitionSize,
     FromContainer, MetasOfPartition, ReadCnmt: ~300 lines) and the check for tools\nstool.exe in step 0. nsextract is
     required (a build failure stops the run, it no longer falls back). Step 2 now reports the progress of nsextract
     every -ProgressSeconds (it used to be silent for ~3.5 min).
   - install_requirements.ps1: step 7 no longer downloads tools\nstool.exe (Windows) nor builds tools/nstool from source
     on Linux (git clone with submodules + make): it builds tools\nsextract (dotnet build), so a problem with .NET or
     NuGet shows up during the install and not in the middle of a recompile. -NstoolFrom is gone. An old
     tools\nstool.exe / tools/nstool is reported ("you can delete it"), not deleted.
   - dev_env.ps1 (tools\ out of the PATH: it was there for nstool), readme.md (2 mentions).
   - The space warning of install_requirements.ps1 step 0 no longer adds 24 GB for the intermediate NCA nstool wrote
     while extracting ($SpaceExtract 24 GB -> 2 GB): nsextract writes only exefs + romfs, already counted per edition.
2. Python packages that nothing imports: fonttools and zstandard (tools\requirements.txt). zstandard is for the .zs
   files of Wonder; Tsukihime's shaders are romfs\shader\*.bnsh. Checked with grep over tools\ and framework\
   (recomp, nso2bin, maxwell, hud, internal, dev). A venv that already has them keeps working.
3. Linux leftovers of the ffmpeg build (FFmpeg is prebuilt now): the per-port ffmpeg toolchain of the cross triplet
   (vcpkg-xwin-ffmpeg.cmake) and the llvm-ar exception NX_AR_GNU of tools/linux/clangcl-xwin.cmake.
Kept on purpose: Git, CMake, Ninja, LLVM, VS Build Tools, .NET (harness and nsextract), Vulkan SDK (spirv-val,
headers, vulkaninfo), Python, the five vcpkg ports, and on Linux make / g++ / pkg-config / nasm / zip (vcpkg asks for
them; nasm is for libjpeg-turbo).

tools\requirements.txt is UTF-16 (with BOM), which _patch.py does not read: this script changes it after the text files
(only if it is exactly the reference file, or exactly the patched one for --remove), and --remove restores it byte for
byte.

Run: cd <root>\framework; private\venv\Scripts\python ..\tools\patches\apply_drop_unused.py [--remove]
Then (optional) delete tools\nstool.exe (Windows) or tools/nstool (Linux), and tools/tests/test_vcpkg_cross.sh (the
isolated test of 2026-10-21 that built ffmpeg with vcpkg; install_requirements_linux.sh replaced it).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import FRAMEWORK_ROOT, run  # noqa: E402

MARK = "AP_DROP_UNUSED"
RECOMP = "../tools/recomp_tsukihime.ps1"
INSTALL = "../tools/install_requirements.ps1"
LINUX = "../tools/linux/install_requirements_linux.ps1"
XWIN = "../tools/linux/clangcl-xwin.cmake"
DEVENV = "../tools/dev_env.ps1"
README = "../readme.md"
REQUIREMENTS = "../tools/requirements.txt"

REQ_BEFORE = ("capstone==5.0.9\r\nfonttools==4.67.0\r\nlz4==4.4.5\r\nnumpy==2.2.6\r\npillow==13.0.0\r\npycryptodome==4.2.0\r\n"
              "texture2ddecoder==1.0.5\r\nzstandard==0.25.0\r\n")
REQ_AFTER = ("# AP_DROP_UNUSED: fonttools and zstandard removed (nothing in the repo imports them)\r\n"
             "capstone==5.0.9\r\nlz4==4.4.5\r\nnumpy==2.2.6\r\npillow==13.0.0\r\npycryptodome==4.2.0\r\ntexture2ddecoder==1.0.5\r\n")

EDITS = [
    (RECOMP,
     r"""#   1 identify       CNMT of the Meta NCA: program ID, Application type, version v0, program NCA. The container is
#                    recognized by its header (PFS0 = NSP, HEAD = XCI), not by the extension or the edition.
#   2 extract        program NCA -> exefs + romfs (in _recomp\, moved at the end); deletes the intermediate NCA
""",
     r"""#   1 identify       program ID of the base application, read with tools\nsextract (C#, LibHac). The container is
#                    recognized by its header (PFS0 = NSP, HEAD = XCI), not by the extension or the edition.
#   2 extract        exefs + romfs straight from the input with nsextract (in _recomp\, moved at the end); AP_DROP_UNUSED:
#                    nstool, the fallback until 2026-11-02, is gone
"""),
    (RECOMP,
     r"""# -ProgressSeconds: how often the progress of long extractions is reported (30 by default).
""",
     r"""# -ProgressSeconds: how often the progress of the extraction (step 2) is reported (30 by default).
"""),
    (RECOMP,
     r"""    [switch]$Replace,
    [switch]$NoNsExtract,
""",
     r"""    [switch]$Replace,
"""),
    (RECOMP,
     r"""$Nstool = if ($OnLinux) { Under $Tools 'nstool' } else { Under $Tools 'nstool.exe' }   # Linux: built by install_requirements_linux.sh
# nsextract (tools\nsextract, C# + LibHac): reads exefs and romfs straight from the NSP/XCI, without the intermediate copy.
# If it is not built (dotnet build tools/nsextract/nsextract.csproj -c Release) or -NoNsExtract is given, nstool is used as before.
$NsExtract = Under $Tools 'nsextract\bin\Release\net10.0\nsextract.dll'
$NsProj = Under $Tools 'nsextract\nsextract.csproj'
$script:NsBroken = $false   # the build failed: nstool is used
function UseNsExtract { return ((-not $NoNsExtract) -and (-not $script:NsBroken) -and (Test-Path -LiteralPath $NsExtract) -and [bool](Get-Command dotnet -ErrorAction SilentlyContinue)) }
# Builds nsextract if the dll is missing or older than its sources (needs dotnet and network the first time, for the LibHac
# package). If the build fails it only warns: the script goes on with nstool (what it did before).
function EnsureNsExtract {
    if ($NoNsExtract -or -not (Test-Path -LiteralPath $NsProj)) { return }
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { Warn 'dotnet not found: nsextract cannot be built, using nstool'; return }
    $src = @(Get-ChildItem -LiteralPath (Split-Path -Parent $NsProj) -File | Where-Object { $_.Extension -in '.cs', '.csproj' })
    $newest = ($src | Measure-Object LastWriteTimeUtc -Maximum).Maximum
    if ((Test-Path -LiteralPath $NsExtract) -and (Get-Item -LiteralPath $NsExtract).LastWriteTimeUtc -ge $newest) { Info 'nsextract already built'; return }
    Info 'building nsextract (tools\nsextract)'
    try { RunVisible 'dotnet' @('build', $NsProj, '-c', 'Release', '--nologo', '-v', 'q') 'dotnet build nsextract' }
    catch { $script:NsBroken = $true; Warn "nsextract was not built ($($_.Exception.Message)): using nstool (-NoNsExtract to skip this)" }
}
""",
     r"""# AP_DROP_UNUSED: nsextract (tools\nsextract, C# + LibHac) is the only extractor: it reads exefs and romfs straight from the
# NSP/XCI, without the intermediate copy. nstool (the fallback until 2026-11-02) is gone.
$NsExtract = Under $Tools 'nsextract\bin\Release\net10.0\nsextract.dll'
$NsProj = Under $Tools 'nsextract\nsextract.csproj'
# Builds nsextract if the dll is missing or older than its sources (needs dotnet, and network the first time for the
# LibHac package; tools\install_requirements.ps1 step 7 already builds it).
function EnsureNsExtract {
    if (-not (Test-Path -LiteralPath $NsProj)) { Fail "$NsProj is missing (it comes with the repo)" }
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { Fail 'dotnet (.NET SDK 10) not found: it builds and runs tools\nsextract (tools\install_requirements.ps1 installs it)' }
    $src = @(Get-ChildItem -LiteralPath (Split-Path -Parent $NsProj) -File | Where-Object { $_.Extension -in '.cs', '.csproj' })
    $newest = ($src | Measure-Object LastWriteTimeUtc -Maximum).Maximum
    if ((Test-Path -LiteralPath $NsExtract) -and (Get-Item -LiteralPath $NsExtract).LastWriteTimeUtc -ge $newest) { Info 'nsextract already built'; return }
    Info 'building nsextract (tools\nsextract)'
    RunVisible 'dotnet' @('build', $NsProj, '-c', 'Release', '--nologo', '-v', 'q', '-p:NuGetAudit=false') 'dotnet build nsextract'
    if (-not (Test-Path -LiteralPath $NsExtract)) { Fail "the build finished but $NsExtract does not appear" }
}
"""),
    (RECOMP,
     r"""# External program; captures the output without printing it (nstool: none of its output goes to the console or a file).
""",
     r"""# External program; captures the output without printing it.
"""),
    (RECOMP,
     r"""function Nstool([string[]]$arguments, [string]$what) {
    $r = RunCapture $Nstool (@('-k', $Keys) + $arguments)
    if ($r.Code -ne 0) {
        $err = @($r.Output | Where-Object { $_ -match 'ERROR' } | Select-Object -First 3)
        Fail "nstool failed to $what (code $($r.Code)): $($err -join ' | ')"
    }
    return $r.Output
}
""",
     r"""# AP_DROP_UNUSED: function Nstool removed (nstool is gone)
"""),
    (RECOMP,
     r"""# nstool for long extractions: runs separately and every -ProgressSeconds reports how much it has written (I/O counter
# of the process; if it cannot be read, the size of the destination folder). Its output goes to two temporary files in
# _recomp\ that are read only to look for ERROR lines and deleted right away (they are not shown).
""",
     r"""# nsextract for the extraction (step 2): runs separately and every -ProgressSeconds reports how much it has written (I/O
# counter of the process; if it cannot be read, the size of the destination folder). Its output goes to two temporary
# files in _recomp\ that are read only to look for ERROR lines and deleted right away (they are not shown).
"""),
    (RECOMP,
     r"""function NstoolLong([string[]]$arguments, [string]$what, [string]$dest, [int64]$total) {
    $id = [guid]::NewGuid().ToString('N')
    $fo = Under $Stage "nstool_$id.out"; $fe = Under $Stage "nstool_$id.err"
    $argStr = ((@('-k', $Keys) + $arguments) | ForEach-Object { Quote $_ }) -join ' '
    $p = Start-Process -FilePath $Nstool -ArgumentList $argStr -NoNewWindow -PassThru -RedirectStandardOutput $fo -RedirectStandardError $fe
    $null = $p.Handle   # without this ExitCode can stay empty in PowerShell 5.1
    $t0 = Get-Date; $before = [int64]0; $tBefore = $t0
    while (-not $p.WaitForExit($ProgressSeconds * 1000)) {
        $now = Get-Date; $b = BytesWritten $p.Id $dest
        $speed = ($b - $before) / [Math]::Max(1, ($now - $tBefore).TotalSeconds)
        $pct = if ($total -gt 0) { ' ({0:N0} %)' -f [Math]::Min(100.0, 100.0 * $b / $total) } else { '' }
        $left = if ($speed -gt 0 -and $total -gt $b) { ', ~{0:N0} min left' -f (($total - $b) / $speed / 60) } else { '' }
        Info ('... {0}: {1:N1} of ~{2:N1} GB{3}, {4:N0} MB/s{5} [{6:mm\:ss}]' -f $what, ($b / 1GB), ($total / 1GB), $pct, ($speed / 1MB), $left, ($now - $t0))
        $before = $b; $tBefore = $now
    }
    $p.WaitForExit()
    $code = $p.ExitCode
    $lines = @()
    foreach ($f in $fo, $fe) { if (Test-Path -LiteralPath $f) { $lines += @(Get-Content -LiteralPath $f); Remove-Item -LiteralPath $f -Force } }
    if ($code -ne 0) {
        $err = @($lines | Where-Object { $_ -match 'ERROR' } | Select-Object -First 3)
        Fail "nstool failed to $what (code $code): $($err -join ' | ')"
    }
    Info ('{0}: done in {1:mm\:ss}' -f $what, ((Get-Date) - $t0))
}
""",
     r"""function ExtractLong([string[]]$arguments, [string]$what, [string]$dest, [int64]$total) {   # AP_DROP_UNUSED: was NstoolLong
    $id = [guid]::NewGuid().ToString('N')
    $fo = Under $Stage "nsextract_$id.out"; $fe = Under $Stage "nsextract_$id.err"
    $argStr = ((@($NsExtract) + $arguments) | ForEach-Object { Quote $_ }) -join ' '
    $dotnet = (Get-Command dotnet -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $p = Start-Process -FilePath $dotnet -ArgumentList $argStr -NoNewWindow -PassThru -RedirectStandardOutput $fo -RedirectStandardError $fe
    $null = $p.Handle   # without this ExitCode can stay empty in PowerShell 5.1
    $t0 = Get-Date; $before = [int64]0; $tBefore = $t0
    while (-not $p.WaitForExit($ProgressSeconds * 1000)) {
        $now = Get-Date; $b = BytesWritten $p.Id $dest
        $speed = ($b - $before) / [Math]::Max(1, ($now - $tBefore).TotalSeconds)
        $pct = if ($total -gt 0) { ' ({0:N0} %)' -f [Math]::Min(100.0, 100.0 * $b / $total) } else { '' }
        $left = if ($speed -gt 0 -and $total -gt $b) { ', ~{0:N0} min left' -f (($total - $b) / $speed / 60) } else { '' }
        Info ('... {0}: {1:N1} of ~{2:N1} GB{3}, {4:N0} MB/s{5} [{6:mm\:ss}]' -f $what, ($b / 1GB), ($total / 1GB), $pct, ($speed / 1MB), $left, ($now - $t0))
        $before = $b; $tBefore = $now
    }
    $p.WaitForExit()
    $code = $p.ExitCode
    $lines = @()
    foreach ($f in $fo, $fe) { if (Test-Path -LiteralPath $f) { $lines += @(Get-Content -LiteralPath $f); Remove-Item -LiteralPath $f -Force } }
    if ($code -ne 0) {
        $err = @($lines | Where-Object { $_ -match 'ERROR' } | Select-Object -First 3)
        Fail "nsextract failed to $what (code $code): $($err -join ' | ')"
    }
    Info ('{0}: done in {1:mm\:ss}' -f $what, ((Get-Date) - $t0))
}
"""),
    (RECOMP,
     r"""# ---------------------------------------------------------------------------------------------- CNMT
# Format (binary, already decrypted by nstool -x /0 of the Meta NCA): +0x00 title ID u64, +0x08 version u32,
# +0x0C type u8 (0x80 Application), +0x0E size of the extended header u16, +0x10 number of contents u16;
# records of 0x38 at 0x20 + ext: hash 0x20, NCA ID 0x10, size 6 bytes, type u8 (1 = Program).
function ReadCnmt([string]$p) {
    $b = [IO.File]::ReadAllBytes($p)
    $r = @{ TitleId = (U64 $b 0); Version = (U32 $b 8); Type = $b[0x0C]; Contents = @() }
    $ext = [BitConverter]::ToUInt16($b, 0x0E); $n = [BitConverter]::ToUInt16($b, 0x10)
    for ($i = 0; $i -lt $n; $i++) {
        $o = 0x20 + $ext + $i * 0x38
        if ($o + 0x38 -gt $b.Length) { Fail "short CNMT ($p)" }
        $id = (($b[($o + 0x20)..($o + 0x2F)] | ForEach-Object { $_.ToString('x2') }) -join '')
        $size = [int64]0; for ($k = 5; $k -ge 0; $k--) { $size = $size * 256 + $b[$o + 0x30 + $k] }
        $r.Contents += @{ Id = $id; Size = $size; Type = $b[$o + 0x36] }
    }
    return $r
}

""",
     r"""# AP_DROP_UNUSED: ReadCnmt removed (it read the CNMT that nstool extracted)

"""),
    (RECOMP,
     r"""# The keys do not leave data\: only their path is passed to nstool, nsextract and mods_nsp.py.
""",
     r"""# The keys do not leave data\: only their path is passed to nsextract and mods_nsp.py.
"""),
    (RECOMP,
     r"""    if ($From -le 2 -and $To -ge 1 -and -not (Test-Path -LiteralPath $Nstool)) { $missing += $Nstool }
""",
     r"""    if ($From -le 2 -and $To -ge 1 -and -not (Get-Command dotnet -ErrorAction SilentlyContinue)) { $missing += 'dotnet (.NET SDK 10: builds and runs tools\nsextract)' }   # AP_DROP_UNUSED
"""),
    (RECOMP,
     r"""# nstool -x <internal path of a file> <output>: depending on the version it leaves <output> as a file or as a folder
# with the file inside; both are accepted. Returns the path of the file or $null if nstool failed.
$script:LastNstoolError = ''
function ExtractFile([string]$internal, [string]$output, [string]$container) {
    $r = RunCapture $Nstool @('-k', $Keys, '-x', $internal, $output, $container)
    $script:LastNstoolError = "code $($r.Code): " + ((@($r.Output | Where-Object { $_ -match 'ERROR' } | Select-Object -First 3)) -join ' | ')
    if ($r.Code -ne 0) { return $null }
    if (Test-Path -LiteralPath $output -PathType Leaf) { return $output }
    $inside = Under $output (Split-Path $internal -Leaf)
    if (Test-Path -LiteralPath $inside -PathType Leaf) { return $inside }
    $script:LastNstoolError += ' (finished without error but left neither the file nor a folder with it)'
    return $null
}

# nstool 1.9.2 on Windows does not extract a single file from a PFS0 (tested 20:27: with a missing output it fails in
# getDirectoryListing, with an existing folder FileStream::open "Access denied"). Folders do work (/0, /1, /):
# if the single file fails, the whole partition (NSP: /, XCI: /secure) is extracted once to _recomp\container
# and step 2 takes the program NCA from there (same space: the NCA was going to be copied anyway).
# Size of what -x <partition> writes: NSP = the whole file; XCI = the secure partition, read from the root HFS0 of
# the header (an untrimmed XCI is padded up to the cartridge size, and it may carry update and normal partitions).
# Root HFS0: offset u64 at 0x130; 'HFS0', file count u32, string table size u32, reserved u32, then entries of
# 0x40 bytes (offset u64, size u64, name offset u32, ...) and the string table; data after the table.
# Only used to estimate free space: if anything does not fit, the size of the whole file (as before).
function PartitionSize([string]$p) {
    $len = (Get-Item -LiteralPath $p).Length
    if ((ContainerType $p) -ne 'xci') { return $len }
    try {
        $fs = [IO.File]::OpenRead($p)
        try {
            $br = New-Object IO.BinaryReader($fs)
            $fs.Position = 0x130; $off = $br.ReadInt64()
            if ($off -le 0 -or $off + 0x10 -gt $len) { return $len }
            $fs.Position = $off
            if ([Text.Encoding]::ASCII.GetString($br.ReadBytes(4)) -ne 'HFS0') { return $len }
            $count = $br.ReadInt32(); $strSize = $br.ReadInt32()
            if ($count -lt 1 -or $count -gt 8 -or $strSize -lt 0 -or $strSize -gt 0x10000) { return $len }
            $fs.Position = $off + 0x10
            $ents = @(); for ($i = 0; $i -lt $count; $i++) { $ents += , @($br.ReadInt64(), $br.ReadInt64(), $br.ReadInt32()); [void]$br.ReadBytes(0x2C) }
            $strs = $br.ReadBytes($strSize)
            foreach ($e in $ents) {
                $end = [Array]::IndexOf($strs, [byte]0, $e[2]); if ($end -lt 0) { $end = $strs.Length }
                if ([Text.Encoding]::ASCII.GetString($strs, $e[2], $end - $e[2]) -eq 'secure' -and $e[1] -gt 0 -and $e[1] -le $len) { return [int64]$e[1] }
            }
        } finally { $fs.Dispose() }
    } catch { }
    return $len
}

$script:Container = $null
function FromContainer([string]$name, [string]$dest) {
    if (-not $script:Container) {
        $f = ExtractFile ($script:Prefix + $name) $dest $InputFile
        if ($f) { return $f }
        $size = PartitionSize $InputFile
        $needed = [int64]$size * 2 + 2GB    # container (the partition) + romfs/exefs (smaller than the partition)
        $free = FreeOn $Data
        if ($free -lt $needed) { Fail "~$(GB $needed) free are needed on the drive of $Data and there are $(GB $free)" }
        Info "space: $(GB $free) free, ~$(GB $needed) needed"
        $c = Under $Stage 'container'
        NstoolLong @('-x', $script:Partition, $c, $InputFile) "extract $($script:Partition) from the input" $c $size
        $script:Container = $c
    }
    $f = Under $script:Container $name
    if (Test-Path -LiteralPath $f -PathType Leaf) { return $f }
    return $null
}

""",
     r"""# AP_DROP_UNUSED: ExtractFile, PartitionSize and FromContainer removed (nstool)
"""),
    (RECOMP,
     r"""# Meta NCAs inside a partition of the nstool --fstree tree (by indentation: "  secure/" and its files further in).
function MetasOfPartition([string[]]$tree, [string]$partition) {
    $inside = $false; $indent = -1; $r = @()
    foreach ($l in $tree) {
        if ($l -notmatch '^(\s*)(\S.*?)\s*$') { continue }
        $ind = $Matches[1].Length; $nm = $Matches[2]
        if ($inside -and $ind -le $indent) { $inside = $false }
        if (-not $inside -and $nm -eq "$partition/") { $inside = $true; $indent = $ind; continue }
        if ($inside) { foreach ($m in [regex]::Matches($nm, '[0-9a-f]{32}\.cnmt\.nca')) { $r += $m.Value } }
    }
    return @($r | Sort-Object -Unique)
}

""",
     r"""# AP_DROP_UNUSED: MetasOfPartition removed (nstool --fstree)
"""),
    (RECOMP,
     r"""function IdentifyInput {
    NewStage
    $script:Container = $null
    if (UseNsExtract) {
        # Direct path: nsextract reports the program ID of the base application; the NCA is not extracted.
        $r = RunCapture 'dotnet' @($NsExtract, 'info', '--keys', $Keys, '--input', $InputFile)
        if ($r.Code -ne 0) {
            $err = @($r.Output | Where-Object { $_ -match 'ERROR' } | Select-Object -First 3)
            Fail "nsextract failed to read $(Split-Path -Leaf $InputFile) (code $($r.Code)): $($err -join ' | ')"
        }
        $kv = @{}
        foreach ($l in $r.Output) { if ($l -match '^([A-Z]+)=(.*)$') { $kv[$Matches[1]] = $Matches[2].Trim() } }
        if (-not $kv.ContainsKey('PROGRAMID')) { Fail 'nsextract did not report a PROGRAMID' }
        $progId = [Convert]::ToUInt64($kv['PROGRAMID'], 16)
        if ($progId -ne $V.ProgramId) {
            $other = ($Editions.GetEnumerator() | Where-Object { $_.Value.ProgramId -eq $progId } | Select-Object -First 1)
            $extra = if ($other) { " (it is the $($other.Key) edition)" } else { '' }
            $script:WrongEdition = $true
            Fail "$(Split-Path -Leaf $InputFile): program ID $(Hex16 $progId)$extra, not $(Hex16 $V.ProgramId) of the $Edition edition"
        }
        $script:Ident = @{ Direct = $true; Size = (Get-Item -LiteralPath $InputFile).Length; Nca = ''; Path = '' }
        Info "program ID $(Hex16 $progId); exefs and romfs will be read straight from the input (nsextract)"
        $Summary['Input'] = "$InputFile (program ID $(Hex16 $progId))"
        return
    }
    $isXci = (ContainerType $InputFile) -eq 'xci'
    $tree = Nstool @('--fstree', $InputFile) 'list the input'
    $metas = @($tree | ForEach-Object { [regex]::Matches($_, '[0-9a-f]{32}\.cnmt\.nca') } | ForEach-Object { $_.Value } | Sort-Object -Unique)
    $ncas = @($tree | ForEach-Object { [regex]::Matches($_, '[0-9a-f]{32}\.nca') } | ForEach-Object { $_.Value } | Sort-Object -Unique)
    if (-not $metas.Count) { Fail 'the input has no Meta NCA (*.cnmt.nca)' }
    # XCI: only the Meta NCAs of secure/ (update/ has ~100 system ones). Without this filter, the first one tried could
    # be from update/: nstool 1.9.2 on Linux does not find it in /secure, exits with 0 leaving nothing and it was
    # concluded that it does not extract single files (extraction of the whole /secure, ~2x the XCI in space). On
    # Windows the result does not change: there nstool fails with any single file and /secure is extracted as before.
    if ($isXci) {
        $sec = @(MetasOfPartition $tree 'secure')
        if ($sec.Count) { Info "Meta NCAs in secure/: $($sec.Count) of $($metas.Count)"; $metas = $sec }
        else { Warn "secure/ not found in the nstool --fstree tree: trying the $($metas.Count) Meta NCAs" }
    }
    # In an XCI the update partition has other CNMTs (system ones): all those of /secure are read and the
    # Application one is kept. In an NSP there must be only one.
    $prefix = if ($isXci) { '/secure/' } else { '/' }
    $script:Prefix = $prefix
    $script:Partition = if ($isXci) { '/secure' } else { '/' }
    if (-not $isXci -and $metas.Count -ne 1) { Fail "the NSP has $($metas.Count) Meta NCAs: only the base is expected (no update or DLC together)" }
    $apps = @(); $others = @(); $k = 0
    foreach ($m in $metas) {
        $k++
        $meta = FromContainer $m (Under $Stage "meta$k")
        if (-not $meta) {
            if ($isXci) { continue }   # not in /secure (update or normal)
            Fail "nstool could not extract $prefix$m ($script:LastNstoolError)"
        }
        $dir = Under $Stage "cnmt$k"
        Nstool @('-x', '/0', $dir, $meta) "open $m" | Out-Null
        $cnmt = @(Get-ChildItem -LiteralPath $dir -Recurse -Filter '*.cnmt' -File)
        if ($cnmt.Count -ne 1) { Fail "1 .cnmt was expected inside $m and there are $($cnmt.Count)" }
        $c = ReadCnmt $cnmt[0].FullName
        if ($c.Type -eq 0x80) { $apps += , $c } else { $others += ('{0} type 0x{1:x2} v{2}' -f (Hex16 $c.TitleId), $c.Type, $c.Version) }
    }
    if ($others.Count) { Info ("other CNMTs in the input (ignored): " + ($others -join ', ')) }
    if ($apps.Count -ne 1) { Fail "1 Application CNMT was expected and there are $($apps.Count)$(if ($isXci) { ' in /secure (if it is 0, send me the output of nstool --fstree of the XCI)' })" }
    $c = $apps[0]
    if ($c.TitleId -ne $V.ProgramId) {
        $other = ($Editions.GetEnumerator() | Where-Object { $_.Value.ProgramId -eq $c.TitleId } | Select-Object -First 1)
        $extra = if ($other) { " (it is the $($other.Key) edition)" } else { '' }
        $script:WrongEdition = $true
        Fail "$(Split-Path -Leaf $InputFile): program ID $(Hex16 $c.TitleId)$extra, not $(Hex16 $V.ProgramId) of the $Edition edition"
    }
    if ($c.Version -ne 0) { Fail "CNMT version $($c.Version), not the base v0" }
    $prog = @($c.Contents | Where-Object { $_.Type -eq 1 })
    if ($prog.Count -ne 1) { Fail "1 program NCA was expected in the CNMT and there are $($prog.Count)" }
    $name = "$($prog[0].Id).nca"
    if ($ncas -notcontains $name) { Fail "the CNMT names $name and the input does not have it" }
    $script:Ident = @{ Nca = $name; Size = $prog[0].Size; Path = ($prefix + $name) }
    Info "program ID $(Hex16 $c.TitleId), Application, v0; program NCA $name ($(GB $prog[0].Size))"
    $Summary['Input'] = "$InputFile (program ID $(Hex16 $c.TitleId), v0)"
}
""",
     r"""function IdentifyInput {
    NewStage
    # nsextract reports the program ID of the base application; the NCA is not extracted.
    $r = RunCapture 'dotnet' @($NsExtract, 'info', '--keys', $Keys, '--input', $InputFile)
    if ($r.Code -ne 0) {
        $err = @($r.Output | Where-Object { $_ -match 'ERROR' } | Select-Object -First 3)
        Fail "nsextract failed to read $(Split-Path -Leaf $InputFile) (code $($r.Code)): $($err -join ' | ')"
    }
    $kv = @{}
    foreach ($l in $r.Output) { if ($l -match '^([A-Z]+)=(.*)$') { $kv[$Matches[1]] = $Matches[2].Trim() } }
    if (-not $kv.ContainsKey('PROGRAMID')) { Fail 'nsextract did not report a PROGRAMID' }
    $progId = [Convert]::ToUInt64($kv['PROGRAMID'], 16)
    if ($progId -ne $V.ProgramId) {
        $other = ($Editions.GetEnumerator() | Where-Object { $_.Value.ProgramId -eq $progId } | Select-Object -First 1)
        $extra = if ($other) { " (it is the $($other.Key) edition)" } else { '' }
        $script:WrongEdition = $true
        Fail "$(Split-Path -Leaf $InputFile): program ID $(Hex16 $progId)$extra, not $(Hex16 $V.ProgramId) of the $Edition edition"
    }
    $script:Ident = @{ Direct = $true; Size = (Get-Item -LiteralPath $InputFile).Length; Nca = ''; Path = '' }
    Info "program ID $(Hex16 $progId); exefs and romfs will be read straight from the input (nsextract)"
    $Summary['Input'] = "$InputFile (program ID $(Hex16 $progId))"
}
"""),
    (RECOMP,
     r"""    if ($script:Ident.Direct) {
        # exefs and romfs go straight from the input to _recomp\: only their own size is written (no copy of the NCA).
        $needed = [int64]$script:Ident.Size + 2GB    # upper bound: romfs + exefs are smaller than the input
        $free = FreeOn $Data
        if ($free -lt $needed) { Fail "~$(GB $needed) free are needed on the drive of $Data and there are $(GB $free)" }
        Info "space: $(GB $free) free, ~$(GB $needed) needed at most"
        Info 'extracting exefs and romfs straight from the input (nsextract)'
        $t0 = Get-Date
        RunVisible 'dotnet' @($NsExtract, 'extract', '--keys', $Keys, '--input', $InputFile, '--program-id', (Hex16 $V.ProgramId),
                              '--exefs', (Under $Stage 'exefs'), '--romfs', (Under $Stage 'romfs')) 'nsextract'
        Info ('extracted in {0:mm\:ss}' -f ((Get-Date) - $t0))
    } else {
        if (-not $script:Container) {
            $needed = [int64]$script:Ident.Size * 2 + 2GB    # intermediate NCA + romfs/exefs
            $free = FreeOn $Data
            if ($free -lt $needed) { Fail "~$(GB $needed) free are needed on the drive of $Data and there are $(GB $free)" }
            Info "space: $(GB $free) free, ~$(GB $needed) needed (~$(GB $script:Ident.Size) are freed at the end)"
            Info 'extracting the program NCA'
        }
        New-Item -ItemType Directory -Path (Under $Stage 'nca') -Force | Out-Null
        $nca = FromContainer $script:Ident.Nca (Under $Stage "nca\$($script:Ident.Nca)")
        if (-not $nca) { Fail "nstool could not extract $($script:Ident.Path) ($script:LastNstoolError)" }
        if ((Get-Item -LiteralPath $nca).Length -ne $script:Ident.Size) {
            Fail "the extracted program NCA does not have the size of the CNMT ($($script:Ident.Size) bytes)"
        }
        Info 'extracting exefs (/0) and romfs (/1)'
        Nstool @('-x', '/0', (Under $Stage 'exefs'), $nca) 'extract exefs' | Out-Null
        NstoolLong @('-x', '/1', (Under $Stage 'romfs'), $nca) 'extract romfs' (Under $Stage 'romfs') ((Get-Item -LiteralPath $nca).Length)
        Remove-Item -LiteralPath (Under $Stage 'nca') -Recurse -Force
        if ($script:Container) { Remove-Item -LiteralPath $script:Container -Recurse -Force; $script:Container = $null }
    }
""",
     r"""    # exefs and romfs go straight from the input to _recomp\: only their own size is written (no copy of the NCA).
    $needed = [int64]$script:Ident.Size + 2GB    # upper bound: romfs + exefs are smaller than the input
    $free = FreeOn $Data
    if ($free -lt $needed) { Fail "~$(GB $needed) free are needed on the drive of $Data and there are $(GB $free)" }
    Info "space: $(GB $free) free, ~$(GB $needed) needed at most"
    Info 'extracting exefs and romfs straight from the input (nsextract)'
    ExtractLong @('extract', '--keys', $Keys, '--input', $InputFile, '--program-id', (Hex16 $V.ProgramId),
                  '--exefs', (Under $Stage 'exefs'), '--romfs', (Under $Stage 'romfs')) 'extract exefs and romfs' (Under $Stage 'romfs') ([int64]$script:Ident.Size)
"""),
    (INSTALL,
     r"""# defined in tools/linux/install_requirements_linux.ps1 (tarballs with a fixed SHA-256, xwin, nstool built from
# source) and the Windows exe cross-compiled.
""",
     r"""# defined in tools/linux/install_requirements_linux.ps1 (tarballs with a fixed SHA-256, xwin) and the Windows exe
# cross-compiled.
"""),
    (INSTALL,
     r"""#   7 nstool         tools\nstool.exe 1.9.2 (fixed SHA-256)
""",
     r"""#   7 nsextract      dotnet build tools\nsextract (C#, LibHac from NuGet): the extractor recomp_tsukihime.ps1 uses (AP_DROP_UNUSED)
"""),
    (INSTALL,
     r"""    [string]$NstoolFrom = '',
""",
     r"""    # AP_DROP_UNUSED: -NstoolFrom removed
"""),
    (INSTALL,
     r"""$NstoolTag = 'v1.9.2'
$NstoolSha256 = '844648190DD8688098887A369DA1126C1B3AB1C2A664FB603B01CB22E76B6CA9'
""",
     r"""# AP_DROP_UNUSED: $NstoolTag / $NstoolSha256 removed
"""),
    (INSTALL,
     r"""function Step7 {
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
""",
     r"""# AP_DROP_UNUSED: tools\nsextract is built here (recomp_tsukihime.ps1 uses only it; nstool, its fallback, is gone), so a
# problem with .NET or with NuGet shows up now and not in the middle of a recompile. Same rule as recomp: rebuilt when a
# source is newer than the dll.
function Step7 {
    foreach ($old in @((PathOf $Root 'tools\nstool.exe'), (PathOf $Root 'tools/nstool'))) {
        if (Test-Path -LiteralPath $old -PathType Leaf) { Info "$old is no longer used (nsextract replaces it): you can delete it" }
    }
    $proj = PathOf $Root 'tools\nsextract\nsextract.csproj'
    $dll = PathOf $Root 'tools\nsextract\bin\Release\net10.0\nsextract.dll'
    if (-not (Test-Path -LiteralPath $proj)) { Problem "$proj is missing (it comes with the repo)"; return }
    $src = @(Get-ChildItem -LiteralPath (Split-Path -Parent $proj) -File | Where-Object { $_.Extension -in '.cs', '.csproj' })
    $newest = ($src | Measure-Object LastWriteTimeUtc -Maximum).Maximum
    if ((Test-Path -LiteralPath $dll) -and (Get-Item -LiteralPath $dll).LastWriteTimeUtc -ge $newest) { Info "nsextract built ($dll)"; $script:Done.Add('nsextract'); return }
    if ($Check) { Problem "nsextract is not built ($dll)"; return }
    $dn = DetectDotnet
    if (-not $dn -or -not $dn.Ok) { Fail '.NET SDK 10 is missing (step 1)' }
    $env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'; $env:DOTNET_NOLOGO = '1'
    Run $dn.Path @('build', $proj, '-c', 'Release', '--nologo', '-v', 'q', '-p:NuGetAudit=false') 'dotnet build (nsextract)'
    if (-not (Test-Path -LiteralPath $dll)) { Fail "the build finished but $dll does not appear" }
    Info 'nsextract built'
    $script:Done.Add('nsextract (built now)')
}
"""),
    (INSTALL,
     r"""$StepNames = @('precheck', 'system (administrator)', 'user', 'repos', 'vcpkg', 'vcpkg libraries', 'venv', 'nstool',
""",
     r"""$StepNames = @('precheck', 'system (administrator)', 'user', 'repos', 'vcpkg', 'vcpkg libraries', 'venv', 'nsextract',
"""),
    (INSTALL,
     r"""# Linux: tools, steps 0-2, 7 and 11 of its own (they redefine the functions above; the rest is shared).
""",
     r"""# Linux: tools, steps 0-2 and 11 of its own (they redefine the functions above; the rest is shared).
"""),
    (INSTALL,
     r"""$SpaceExtract = 24GB
""",
     r"""$SpaceExtract = 2GB   # AP_DROP_UNUSED: was 24 GB, the intermediate NCA of nstool; nsextract writes only exefs + romfs (counted above)
"""),
    (LINUX,
     r"""# the Microsoft license when xwin asks), nstool built from source (commit and tree verified), vcpkg triplet to build the
# x64-windows libraries cross and the summary. Steps 3, 4, 5, 6, 8, 9 and 10 are those of install_requirements.ps1
""",
     r"""# the Microsoft license when xwin asks), vcpkg triplet to build the x64-windows libraries cross and the summary.
# Steps 3 to 10 are those of install_requirements.ps1 (AP_DROP_UNUSED: step 7 builds tools/nsextract, no nstool)
"""),
    (LINUX,
     r"""#   7 nstool         tools/nstool built from jakcron/nstool v1.9.2 (commit + tree verified)
""",
     r"""#   7 nsextract      the one of install_requirements.ps1 (dotnet build tools/nsextract)
"""),
    (LINUX,
     r"""$NstoolCommit = 'e89ea84bd70c11b67ebec9153fb4a446f18a9d3d'
$NstoolRepo = 'https://github.com/jakcron/nstool.git'
# Tree of nstool v1.9.2 with submodules (clone --depth 1 --recurse-submodules --shallow-submodules): "path  SHA256" per
# file without .git or bin/obj, ordinal order, LF; SHA-256 of that text (measured 2026-10-21).
$NstoolFiles = 2000
$NstoolTree = '2C97AE6672D5D1814C5DBCDAA8C844C790AC8CFFB13BBEA322FC281A0AA80717'
""",
     r"""# AP_DROP_UNUSED: the nstool pins (commit, tree) removed
"""),
    (LINUX,
     r"""function Step7 {
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

""",
     r"""# AP_DROP_UNUSED: the Linux step 7 (nstool built from source) removed: step 7 is the one of install_requirements.ps1

"""),
    (LINUX,
     r"""    $ff = @("# Generated by install_requirements.ps1 (Linux): like vcpkg-xwin.cmake but with llvm-ar (ffmpeg only; see clangcl-xwin.cmake).",
            'set(NX_AR_GNU ON)', "include(`"$(Join-Path $ov 'vcpkg-xwin.cmake')`")") -join "`n"
""",
     r"""    # AP_DROP_UNUSED: no vcpkg-xwin-ffmpeg.cmake toolchain (FFmpeg is prebuilt)
"""),
    (LINUX,
     r"""        "set(VCPKG_LINKER_FLAGS `"/winsysroot:$xw`")",
        'if(PORT STREQUAL "ffmpeg")',
        "    set(VCPKG_CHAINLOAD_TOOLCHAIN_FILE `"$(Join-Path $ov 'vcpkg-xwin-ffmpeg.cmake')`")",
        'endif()') -join "`n") + "`n"
""",
     r"""        "set(VCPKG_LINKER_FLAGS `"/winsysroot:$xw`")") -join "`n") + "`n"   # AP_DROP_UNUSED: no ffmpeg triplet (FFmpeg is prebuilt)
"""),
    (LINUX,
     r"""    [IO.File]::WriteAllText((Join-Path $ov 'vcpkg-xwin-ffmpeg.cmake'), $ff + "`n", $utf8)
""",
     r"""    # AP_DROP_UNUSED: (and vcpkg-xwin-ffmpeg.cmake is no longer written)
"""),
    (XWIN,
     r"""list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES NX_XWIN NX_LLVM NX_AR_GNU)
""",
     r"""list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES NX_XWIN NX_LLVM)
"""),
    (XWIN,
     r"""# CMAKE_AR: llvm-lib (lib.exe style, what CMake expects with clang-cl). Exception: with NX_AR_GNU (only the ffmpeg
# triplet) llvm-ar. ffmpeg does not use CMake to build; its compat/windows/makedef does "$AR rcs tmp.lib objs" to
# get the symbols for the .def, and with llvm-lib that fails ("rcs: no such file"). On Windows vcpkg wraps it in ar-lib.
if(NX_AR_GNU)
    set(CMAKE_AR "${_nx_bin}/llvm-ar" CACHE FILEPATH "")
else()
    set(CMAKE_AR "${_nx_bin}/llvm-lib" CACHE FILEPATH "")
endif()
""",
     r"""# CMAKE_AR: llvm-lib (lib.exe style, what CMake expects with clang-cl). AP_DROP_UNUSED: the llvm-ar exception of the
# ffmpeg triplet (NX_AR_GNU) is gone: FFmpeg is prebuilt and no other port needed it.
set(CMAKE_AR "${_nx_bin}/llvm-lib" CACHE FILEPATH "")
"""),
    (DEVENV,
     r"""# CMake and Ninja from winget, Vulkan SDK, vcpkg and nstool.""",
     r"""# CMake and Ninja from winget, Vulkan SDK and vcpkg (AP_DROP_UNUSED: no nstool)."""),
    (DEVENV,
     r"""(Join-Path $nxT $nxPin.ninja), (Join-Path $nxT $nxPin.dotnet), $nxTools)""",
     r"""(Join-Path $nxT $nxPin.ninja), (Join-Path $nxT $nxPin.dotnet))"""),
    (DEVENV,
     r"""$env:PATH = "$nxTools;$env:PATH"                                      # nstool
""",
     r"""# AP_DROP_UNUSED: tools\ is no longer put in the PATH (it was there for nstool)
"""),
    (README,
     r"""pass its path to `nstool`;""",
     r"""pass its path to `nsextract`;"""),
    (README,
     r"""LLVM, CMake, Ninja, `nstool` and `xwin`""",
     r"""LLVM, CMake, Ninja and `xwin`<!-- AP_DROP_UNUSED: no nstool -->"""),
]


def requirements(root, remove):
    """The UTF-16 requirements.txt: (path, bytes to write or None if already done). Exits if it is neither version."""
    path = os.path.join(root, REQUIREMENTS)
    if not os.path.isfile(path):
        sys.exit(f"ERROR: {path} does not exist")
    raw = open(path, "rb").read()
    before, after = b"\xff\xfe" + REQ_BEFORE.encode("utf-16-le"), b"\xff\xfe" + REQ_AFTER.encode("utf-16-le")
    want, have = (before, after) if remove else (after, before)
    if raw == want:
        return path, None
    if raw != have:
        sys.exit(f"ERROR: {path} is not the reference file (edited by hand?): nothing touched. Remove fonttools and "
                 "zstandard from it by hand if you want.")
    return path, want


if __name__ == "__main__":
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--root", default=FRAMEWORK_ROOT)
    ap.add_argument("--remove", action="store_true")
    a, _ = ap.parse_known_args()
    req_path, req_data = requirements(a.root, a.remove)   # checked BEFORE the text files are touched
    run(MARK, EDITS, __doc__)
    if req_data is not None:
        with open(req_path, "wb") as f:
            f.write(req_data)
        print(("removed: " if a.remove else "patched: ") + os.path.relpath(req_path, a.root))
