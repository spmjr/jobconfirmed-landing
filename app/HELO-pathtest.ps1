<#
  HELO-pathtest.ps1 - find out how to SET the SMB record path on a HELO.

  Safe: it only writes the value the unit ALREADY has (so nothing changes), and reads everything else.
  Use the spare unit (192.168.185.51). Make sure its path on the web page is what you want to keep first.

  Usage:  powershell -ExecutionPolicy Bypass -File .\HELO-pathtest.ps1 -Ip 192.168.185.51
#>
param([Parameter(Mandatory = $true)][string]$Ip)

Add-Type -AssemblyName System.Net.Http
$h = New-Object System.Net.Http.HttpClientHandler
$h.UseProxy = $false
$client = New-Object System.Net.Http.HttpClient($h)
$client.Timeout = [TimeSpan]::FromSeconds(5)
$client.DefaultRequestHeaders.ConnectionClose = $true

function Call($url) {
    try {
        $r = $client.GetAsync($url).GetAwaiter().GetResult()
        $body = $r.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        return "{0} {1}  {2}" -f [int]$r.StatusCode, $r.ReasonPhrase, $body.Trim()
    } catch { return "ERROR " + $_.Exception.GetBaseException().Message }
}
$base = "http://$Ip/config?action="

Write-Host "--- other possible names for the path (read only) ---"
foreach ($n in 'eParamID_RecordingPath', 'eParamID_RecordingPathSMB', 'eParamID_RecordingShareNetworkPath', 'eParamID_RecordingNetworkPath',
               'eParamID_RecordingSMBPath', 'eParamID_RecordingSharePath', 'eParamID_RecordingFolder', 'eParamID_RecordingPathFriendly',
               'eParamID_RecordingPathFriendly_1', 'eParamID_RecordingPath_1') {
    Write-Host ("{0,-40} {1}" -f $n, (Call "${base}get&paramid=$n"))
}

$cur = $null
try { $cur = ((Invoke-WebRequest -UseBasicParsing "${base}get&paramid=eParamID_RecordingPathFriendly").Content | ConvertFrom-Json).value } catch { }
Write-Host "`nCurrent path value on the unit: '$cur'"
if (-not $cur) { return }

Write-Host "`n--- writing the SAME value back, three ways ---"
$variants = [ordered]@{
    'as stored'          = $cur
    'one leading slash'  = '/' + $cur.TrimStart('/')
    'no leading slash'   = $cur.TrimStart('/')
}
foreach ($k in $variants.Keys) {
    $enc = [System.Uri]::EscapeDataString($variants[$k])
    Write-Host ("{0,-20} '{1}'  ->  {2}" -f $k, $variants[$k], (Call "${base}set&paramid=eParamID_RecordingPathFriendly&value=$enc"))
}
Write-Host "`nSend me everything above."
