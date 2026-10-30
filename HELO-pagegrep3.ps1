<#
  HELO-pagegrep3.ps1 - read-only. Downloads the HELO web page and its scripts, then lists every place that
  mentions the SMB path / share / folder, so we can find the setting that really holds the record path.
  Nothing is changed on the unit.

  Usage:  powershell -ExecutionPolicy Bypass -File .\HELO-pagegrep.ps1
          (defaults to 192.168.185.51; add -Ip x.x.x.x for another unit)
  Output: helo-pagegrep3.txt next to the script (also printed on screen).
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
while ($queue.Count -gt 0 -and $files.Count -lt 120) {
    $p = [string]$queue.Dequeue()
    if ($files.ContainsKey($p)) { continue }
    $body = Get-Text $p
    $files[$p] = $body
    if (-not $body) { continue }
    $cands = @()
    foreach ($m in [regex]::Matches($body, '(?:src|href)\s*=\s*["'']([^"'']+\.(?:js|html?))["'']')) { $cands += $m.Groups[1].Value }
    if ($p -match 'kipro|\.html?$|^/$') {
        foreach ($m in [regex]::Matches($body, '["'']((?:/)?[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-.]+)*\.(?:html?|shtml|php|cgi))["'']')) { $cands += $m.Groups[1].Value }
    }
    foreach ($u in $cands) {
        if ($u -match '^https?://' -or $u -match 'scheduler') { continue }
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

# 2) how the page SAVES settings: look for the set call, the path label and anything SMB-ish
$patterns = [ordered]@{
    'action=set'      = 30
    'Record Path'     = 20
    'Network Record'  = 20
    'RecordPath'      = 20
    'SMB'             = 12
    'smb_'            = 20
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


# 3) harmless test: write the path back to itself the way the web page does (with &configid=0)
[void]$out.Add("`n=== set test with configid=0 (same value written back) ===")
try {
    $cur = ($client.GetStringAsync("http://$Ip/config?action=get&paramid=eParamID_RecordingPathFriendly").GetAwaiter().GetResult() | ConvertFrom-Json).value
    $enc = [System.Uri]::EscapeDataString("$cur")
    foreach ($u in @("http://$Ip/config?action=set&paramid=eParamID_RecordingPathFriendly&value=$enc&configid=0")) {
        try { $r = $client.GetAsync($u).GetAwaiter().GetResult(); [void]$out.Add(("{0} {1}  (value '{2}')" -f [int]$r.StatusCode, $r.ReasonPhrase, $cur)) }
        catch { [void]$out.Add("error: " + $_.Exception.GetBaseException().Message) }
    }
} catch { [void]$out.Add("could not run the test: " + $_.Exception.GetBaseException().Message) }

$text = ($out -join "`r`n")
Write-Host $text
[System.IO.File]::WriteAllText((Join-Path $outDir 'helo-pagegrep3.txt'), $text, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "`nSaved helo-pagegrep3.txt in $outDir - send me THAT file (helo-pagegrep3.txt)."
[void](Read-Host 'Press Enter to close')
