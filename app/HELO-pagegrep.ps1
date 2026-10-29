<#
  HELO-pagegrep.ps1 - read-only. Downloads the HELO web page and its scripts, then lists every place that
  mentions the SMB path / share / folder, so we can find the setting that really holds the record path.
  Nothing is changed on the unit.

  Usage:  powershell -ExecutionPolicy Bypass -File .\HELO-pagegrep.ps1
          (defaults to 192.168.185.51; add -Ip x.x.x.x for another unit)
  Output: helo-pagegrep.txt next to the script (also printed on screen).
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

# 2) the text around each mention
[void]$out.Add("`n=== context ===")
foreach ($k in ($files.Keys | Sort-Object)) {
    $t = $files[$k]
    if (-not $t) { continue }
    foreach ($m in [regex]::Matches($t, '(?i)(RecordingPath|ShareNetworkSMB|SMBPath|NetworkPath|RecordPath)')) {
        $s = [math]::Max(0, $m.Index - 120)
        $len = [math]::Min(300, $t.Length - $s)
        $snippet = ($t.Substring($s, $len) -replace '\s+', ' ')
        [void]$out.Add("[$k @ $($m.Index)] $snippet")
    }
}

$text = ($out -join "`r`n")
Write-Host $text
[System.IO.File]::WriteAllText((Join-Path $outDir 'helo-pagegrep.txt'), $text, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "`nSaved helo-pagegrep.txt in $outDir - send me that file."
[void](Read-Host 'Press Enter to close')
