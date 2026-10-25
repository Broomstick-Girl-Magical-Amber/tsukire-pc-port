param(
    [string]$Dir = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'data\tsuki\shaders_extracted\misses')
)
# manifest.tsv for spirv_harness --batch: 13 columns; the harness reads hash (10), code_file (11), ctrl_file (12)
$lines = @("id`tstage`tcode_off`tcode_size`tctrl_off`tctrl_size`tprogram_start`tprogram_bytes`tconst_off`tconst_size`tcode_sha1_16`tcode_file`tctrl_file")
foreach ($f in Get-ChildItem $Dir -Filter code_*.bin | Sort-Object Name) {
    $hash = $f.BaseName.Substring(5)
    $lines += "$hash`t-`t0`t$($f.Length)`t0`t2176`t0`t0`t0`t0`t$hash`tcode_$hash.bin`tctrl_$hash.bin"
}
[IO.File]::WriteAllText("$Dir\manifest.tsv", ($lines -join "`n") + "`n")
"$($lines.Count - 1) rows in $Dir\manifest.tsv"