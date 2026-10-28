<#
  HELO-probe.ps1 - find the REST parameter IDs for Recording Template, SMB Server Address and
  SMB Network Record Path on a HELO.

  This script only READS (HTTP "get"). YOU make the changes on the unit's web page between steps.
  Use ONE idle spare HELO (not recording), and put its settings back at the end.

  Usage:  powershell -ExecutionPolicy Bypass -File .\HELO-probe.ps1 -Ip 192.168.x.x

  1. Collects every eParamID_* name it can find (the web page's own scripts + a config dump if supported).
  2. Reads them all (baseline x2, so values that change by themselves are ignored).
  3. Walks you through: change template -> change template back -> change SMB server -> change SMB path.
     After each step it shows which parameters changed.
  Results are saved to helo-probe-*.txt next to the script. Copy the on-screen output back to me.
#>
param(
    [Parameter(Mandatory = $true)][string]$Ip,
    [string]$OutDir
)
if ($Ip -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { Write-Warning "Not a valid IP: $Ip"; return }
if (-not $OutDir) { $OutDir = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path } }

Add-Type -AssemblyName System.Net.Http
$h = New-Object System.Net.Http.HttpClientHandler
$h.UseProxy = $false
$client = New-Object System.Net.Http.HttpClient($h)
$client.Timeout = [TimeSpan]::FromSeconds(4)
$client.DefaultRequestHeaders.ConnectionClose = $true

function Get-Raw($path) {
    try   { return $client.GetStringAsync("http://$Ip$path").GetAwaiter().GetResult() }
    catch { return $null }
}

# ---- 1. collect parameter names ----
$names = @{}
function Add-Names($text) {
    if (-not $text) { return }
    foreach ($m in [regex]::Matches($text, 'eParamID_[A-Za-z0-9_]+')) { $names[$m.Value] = $true }
}

Write-Host "HELO at $Ip  (read-only)"
Write-Host 'Looking for parameter names...'
$seen = @{}
$queue = New-Object System.Collections.Queue
$queue.Enqueue('/')
while ($queue.Count -gt 0 -and $seen.Count -lt 60) {
    $p = [string]$queue.Dequeue()
    if ($seen.ContainsKey($p)) { continue }
    $seen[$p] = $true
    $body = Get-Raw $p
    if (-not $body) { continue }
    Add-Names $body
    foreach ($m in [regex]::Matches($body, '(?:src|href)\s*=\s*["'']([^"'']+\.(?:js|html?))["'']')) {
        $u = $m.Groups[1].Value
        if ($u -match '^https?://') { continue }            # only this unit
        if (-not $u.StartsWith('/')) { $u = '/' + $u }
        if (-not $seen.ContainsKey($u)) { $queue.Enqueue($u) }
    }
}
Write-Host ("  from the web page files: {0} names (pages/scripts read: {1})" -f $names.Count, $seen.Count)

$dump = Get-Raw '/config?action=get&configid=0'
if ($dump) { $before = $names.Count; Add-Names $dump; Write-Host ("  from a config dump: {0} more" -f ($names.Count - $before)) }

# keep names we know are commands out of the polling list (a "get" is harmless, but they carry no state)
$list = @($names.Keys | Where-Object { $_ -notmatch 'Command$' } | Sort-Object)
if ($list.Count -eq 0) {
    Write-Warning 'No parameter names found. Use the browser: F12 > Network, click Apply on the page, and send me the request URL.'
    return
}
Write-Host ("Will read {0} parameters each time." -f $list.Count)

function Get-Snapshot {
    $map = @{}
    $i = 0
    foreach ($n in $list) {
        $i++
        if ($i % 25 -eq 0) { Write-Host -NoNewline '.' }
        $raw = Get-Raw "/config?action=get&paramid=$n"
        if (-not $raw) { continue }
        try {
            $j = $raw | ConvertFrom-Json
            $v = "$($j.value)"
            if ($j.value_name) { $v += " ($($j.value_name))" }
            $map[$n] = $v
        } catch { $map[$n] = $raw.Trim() }
    }
    Write-Host ''
    return $map
}
function Save-Snapshot($label, $map) {
    $lines = $map.GetEnumerator() | Sort-Object Name | ForEach-Object { '{0} = {1}' -f $_.Name, $_.Value }
    [System.IO.File]::WriteAllLines((Join-Path $OutDir "helo-probe-$label.txt"), [string[]]@($lines), (New-Object System.Text.UTF8Encoding($false)))
}
function Show-Diff($old, $new, $noisy) {
    $any = $false
    foreach ($k in (@($old.Keys) + @($new.Keys) | Sort-Object -Unique)) {
        if ($noisy.ContainsKey($k)) { continue }
        if ("$($old[$k])" -ne "$($new[$k])") {
            $any = $true
            Write-Host ('  {0}:  {1}  ->  {2}' -f $k, $(if ($null -eq $old[$k]) { '(absent)' } else { $old[$k] }), $(if ($null -eq $new[$k]) { '(absent)' } else { $new[$k] }))
        }
    }
    if (-not $any) { Write-Host '  (nothing changed - did Apply/Save get pressed?)' }
}

# ---- 2. baseline ----
[void](Read-Host "`nMake sure this HELO is NOT recording. Note its current template / SMB server / path (you will put them back). Press Enter")
$a = Get-Snapshot
Start-Sleep -Seconds 2
$b = Get-Snapshot
if ($a.Count -eq 0) { Write-Warning "Could not read any parameter from $Ip."; return }
$noisy = @{}
foreach ($k in (@($a.Keys) + @($b.Keys))) { if ("$($a[$k])" -ne "$($b[$k])") { $noisy[$k] = $true } }
Write-Host ("Read {0} values; ignoring {1} that change on their own." -f $a.Count, $noisy.Count)
Save-Snapshot 'baseline' $a

# ---- 3. guided changes ----
$steps = @(
    @('template-changed', 'On the unit''s web page change the RECORDING TEMPLATE to a DIFFERENT one (e.g. a 720p one) and click Apply'),
    @('template-1080low', 'Now change the template to "1080p Low 5 Mbps" and click Apply'),
    @('smb-server',       'Now change the SMB SERVER ADDRESS slightly (e.g. add a "X" to the end) and save/apply'),
    @('smb-path',         'Now change the SMB NETWORK RECORD PATH slightly (e.g. add "/PROBE" to the end) and save/apply')
)
$prev = $a
foreach ($s in $steps) {
    $ans = Read-Host ("`n{0}`n  then press Enter   [q = stop]" -f $s[1])
    if ($ans -match '^(q|quit)$') { break }
    $snap = Get-Snapshot
    Save-Snapshot $s[0] $snap
    Write-Host ("Changed since the previous step ({0}):" -f $s[0])
    Show-Diff $prev $snap $noisy
    $prev = $snap
}

Write-Host "`nNow PUT THE SMB SERVER AND PATH BACK to their original values on the unit's web page (and leave the template on 1080p Low 5 Mbps if that is your standard)."
$client.Dispose()
Write-Host "Done. Copy everything above and send it to me (files are in $OutDir)."
