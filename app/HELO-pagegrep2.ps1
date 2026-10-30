<#
  HELO-pagegrep2.ps1 - read-only. Downloads the HELO web page and its scripts, then lists every place that
  mentions the SMB path / share / folder, so we can find the setting that really holds the record path.
  Nothing is changed on the unit.

  Usage:  powershell -ExecutionPolicy Bypass -File .\HELO-pagegrep.ps1
          (defaults to 192.168.185.51; add -Ip x.x.x.x for another unit)
  Output: helo-pagegrep2.txt next to the script (also printed on screen).
#>
param([string]$Ip = '192.168.185.51')

trap { Write-Host ('CRASH: ' + $_ + "`n" + $_.ScriptStackTrace) -ForegroundColor Red; [void](Read-Host 'Press Enter to close'); break }

$outDir = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Add-Type -AssemblyName System.Net.Http
$h = New-Object System.Net.Http.HttpClientHandler
$h.UseProxy = $false
$client = New-Object System.Net.Http.HttpClient($h)
$client.Timeout = [TimeSpan]::FromSeconds(8)
$client.DefaultRequestHeaders.ConnectionClose = $true

function Get-Text($path) {
    try { return $client.GetStringAsync("http://$Ip$path").GetAwaiter().GetResult() } catch { return $null }
}

$files = @{}
$queue = New-Object System.Collections.Queue
$queue.Enqueue('/')
while ($queue.Count -gt 0 -and $files.Count -lt 80) {
    $p = [string]$queue.Dequeue()
    if ($files.ContainsKey($p)) { continue }
    $body = Get-Text $p
    $files[$p] = $body
    if (-not $body) { continue }
    foreach ($m in [regex]::Matches($body, '(?:src|href)\s*=\s*["'']([^"'']+\.(?:js|html?))["'']')) {
        $u = $m.Groups[1].Value
        if ($u -match '^https?://') { continue }
        if (-not $u.StartsWith('/')) { $u = '/' + $u }
        if (-not $files.ContainsKey($u)) { $queue.Enqueue($u) }
    }
}

# Save the page files so they can be searched properly (only the HELO's own files, nothing is sent anywhere)
$saveDir = Join-Path $outDir 'helo-page-files'
[void](New-Item -ItemType Directory -Force -Path $saveDir)
foreach ($k in $files.Keys) {
    if (-not $files[$k]) { continue }
    $leaf = ($k -replace '[\\/:*?"<>|]', '_').Trim('_')
    if ($leaf -eq '') { $leaf = 'index.html' }
    [System.IO.File]::WriteAllText((Join-Path $saveDir $leaf), $files[$k], (New-Object System.Text.UTF8Encoding($false)))
}

$out = New-Object System.Collections.ArrayList
[void]$out.Add("Unit $Ip - files read: $($files.Count)")
foreach ($k in ($files.Keys | Sort-Object)) { [void]$out.Add(("  {0}  ({1} chars)" -f $k, $(if ($files[$k]) { $files[$k].Length } else { 'not readable' }))) }

# 1) every parameter name that looks related
$names = @{}
foreach ($k in $files.Keys) {
    if (-not $files[$k]) { continue }
    foreach ($m in [regex]::Matches($files[$k], 'eParamID_[A-Za-z0-9_]*(?:Path|Share|SMB|Folder|Director|Network|Dest)[A-Za-z0-9_]*')) { $names[$m.Value] = $true }
}
[void]$out.Add("`n=== parameter names mentioning Path/Share/SMB/Folder/Network/Dest ===")
foreach ($n in ($names.Keys | Sort-Object)) { [void]$out.Add($n) }

# 2) how the page SAVES settings: look for the set call, the path label and anything SMB-ish
$patterns = [ordered]@{
    'action=set'      = 30
    'Record Path'     = 20
    'Network Record'  = 20
    'RecordPath'      = 20
    'SMB'             = 40
    'smb_'            = 20
    'browse'          = 15
    'mount'           = 15
}
foreach ($pat in $patterns.Keys) {
    [void]$out.Add("`n=== mentions of '$pat' (max $($patterns[$pat])) ===")
    $count = 0
    foreach ($k in ($files.Keys | Sort-Object)) {
        $t = $files[$k]
        if (-not $t -or $k -match 'scheduler') { continue }
        foreach ($m in [regex]::Matches($t, [regex]::Escape($pat), 'IgnoreCase')) {
            if ($count -ge $patterns[$pat]) { break }
            $count++
            $s0 = [math]::Max(0, $m.Index - 200)
            $len = [math]::Min(500, $t.Length - $s0)
            [void]$out.Add("[$k @ $($m.Index)] " + ($t.Substring($s0, $len) -replace '\s+', ' '))
        }
    }
}

$text = ($out -join "`r`n")
Write-Host $text
[System.IO.File]::WriteAllText((Join-Path $outDir 'helo-pagegrep2.txt'), $text, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "`nSaved helo-pagegrep2.txt in $outDir - send me THAT file (helo-pagegrep2.txt)."
[void](Read-Host 'Press Enter to close')
