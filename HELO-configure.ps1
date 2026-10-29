<#
  AJA HELO Configure - push settings to many HELOs at once.

  SEPARATE from the monitor (HELO-recMon.ps1), which stays read-only. This tool WRITES settings.

  Uses the same HELOs.txt as the monitor (next to this script). HELO-N in the schedule = the Nth HELO
  in HELOs.txt (same numbering as the monitor's "HELO N" labels). Ki Pro GO lines are ignored.

  What it can push (always to the FIRST recording profile, profile 0):
    - Filename prefix   : per unit, read from the launch recording schedule (.xlsx)
    - Recording template: same value for every checked unit   (needs its parameter ID, see $params)
    - SMB server address: same value for every checked unit   (needs its parameter ID, see $params)
    - SMB record path   : same value for every checked unit   (needs its parameter ID, see $params)

  Safety:
    - "Check units" reads current values and changes nothing.
    - Units that are recording or streaming are skipped, as are units not on profile 0.
    - Units the schedule marks "DON'T CHANGE" are unchecked by default and need an extra confirmation.
    - Every change is read back from the unit and reported (OK / reads back differently / failed).
    - Domain username and password are never touched.
#>
param(
    [string]$ConfigPath
)

#region Settings
[int]$timeoutSeconds = 4      # per-request timeout (writes can take a moment)
$baseDir = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
if (-not $ConfigPath) { $ConfigPath = Join-Path $baseDir 'HELOs.txt' }
$presetPath = Join-Path $baseDir 'HELO-presets.txt'

# HELO REST parameter IDs.
#   RecState / StrState : same ones the monitor reads
#   ProfileSel / Prefix : documented in AJA "HELO Commands" chapter
#   SmbServer / SmbPath / Template / TemplateApply : NOT documented there. Leave '' until confirmed
#   with Find-HeloParams.ps1; steps with an empty ID are disabled in the window.
$params = @{
    RecState      = 'eParamID_ReplicatorPrimaryRecState'
    StrState      = 'eParamID_ReplicatorStreamState'
    ProfileSel    = 'eParamID_RecordingProfileSel'
    Prefix        = 'eParamID_FilenamePrefix'
    SmbServer     = 'eParamID_RecordingShareNetworkSMB'
    SmbPath       = 'eParamID_RecordingPathFriendly'
    Template      = 'eParamID_RecordingTemplateSrc'
    TemplateApply = ''
}
$templateApplyValue = '1'
# Template name (as shown in the web page) -> value the unit expects. Fill in once known.
$templateValues = [ordered]@{ '1080p Low 5 Mbps' = '1'; '720p High 10 Mbps' = '2' }
$defaultTemplate = '1080p Low 5 Mbps'
# SMB server presets (more can be added with "Save preset"; they are kept in HELO-presets.txt).
$defaultPresets = [ordered]@{ 'Vulcan' = '\\192.168.185.100\Vulcan' }
#endregion Settings

#region Core logic (no GUI)

function ConvertTo-HeloIp($text) {
    $t = "$text".Trim()
    if ($t -match '^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$') {
        $octets = @()
        foreach ($i in 1..4) {
            $n = [int]$Matches[$i]
            if ($n -gt 255) { return $null }
            $octets += $n
        }
        return ($octets -join '.')
    }
    return $null
}

# Devices are hashtables: @{ IP; Name; Type = 'HELO' or 'GO'; Button }  (same file format as the monitor)
$script:devices = New-Object System.Collections.ArrayList

function Import-HeloConfig {
    $warnings = New-Object System.Collections.ArrayList
    $list     = New-Object System.Collections.ArrayList
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        [void]$warnings.Add("HELO list not found: $ConfigPath")
        return @{ Devices = $list; Warnings = $warnings }
    }
    try   { $lines = @(Get-Content -LiteralPath $ConfigPath -Encoding UTF8 -ErrorAction Stop) }
    catch { [void]$warnings.Add("Could not read ${ConfigPath}: $($_.Exception.Message)"); return @{ Devices = $list; Warnings = $warnings } }

    $seen = @{}
    $lineNo = 0
    foreach ($line in $lines) {
        $lineNo++
        $t = "$line".Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $parts = $t.Split(',')
        $ip = ConvertTo-HeloIp $parts[0]
        if (-not $ip)               { [void]$warnings.Add("Line ${lineNo}: not a valid IP, skipped ($t)"); continue }
        if ($seen.ContainsKey($ip)) { [void]$warnings.Add("Line ${lineNo}: duplicate of $ip, skipped"); continue }
        $seen[$ip] = $true
        $rest = @()
        if ($parts.Count -gt 1) { $rest = @($parts[1..($parts.Count - 1)]) }
        $type = 'HELO'
        if ($rest.Count -gt 0 -and $rest[$rest.Count - 1].Trim() -match '^(?i)(go|kiprogo|helo)$') {
            if ($Matches[1] -match '^(?i)helo$') { $type = 'HELO' } else { $type = 'GO' }
            if ($rest.Count -eq 1) { $rest = @() } else { $rest = @($rest[0..($rest.Count - 2)]) }
        }
        $name = ($rest -join ',').Trim()
        [void]$list.Add(@{ IP = $ip; Name = $name; Type = $type; Button = $null })
    }
    return @{ Devices = $list; Warnings = $warnings }
}

# Unnamed units are labelled by their position among units of the same type ("HELO 7").
function Get-DisplayName($dev) {
    if ($dev.Name) { return $dev.Name }
    $n = 0
    foreach ($x in $script:devices) {
        if ($x.Type -eq $dev.Type) { $n++ }
        if ([object]::ReferenceEquals($x, $dev)) { break }
    }
    if ($dev.Type -eq 'GO') { return "GO $n" }
    return "HELO $n"
}

# ---- SMB server presets ----
$script:presets = [ordered]@{}
function Import-Presets {
    $script:presets = [ordered]@{}
    foreach ($k in $defaultPresets.Keys) { $script:presets[$k] = $defaultPresets[$k] }
    if (Test-Path -LiteralPath $presetPath) {
        foreach ($line in @(Get-Content -LiteralPath $presetPath -Encoding UTF8)) {
            $i = "$line".IndexOf('=')
            if ($i -gt 0) { $script:presets[$line.Substring(0, $i).Trim()] = $line.Substring($i + 1).Trim() }
        }
    }
}
function Save-Presets {
    $out = @()
    foreach ($k in $script:presets.Keys) { $out += "$k=$($script:presets[$k])" }
    [System.IO.File]::WriteAllLines($presetPath, [string[]]$out, (New-Object System.Text.UTF8Encoding($false)))
}

# ---- HTTP (blocking; the window pumps messages between units) ----
Add-Type -AssemblyName System.Net.Http
$handler = New-Object System.Net.Http.HttpClientHandler
$handler.UseProxy = $false
$client = New-Object System.Net.Http.HttpClient($handler)
$client.Timeout = [TimeSpan]::FromSeconds($timeoutSeconds)
$client.DefaultRequestHeaders.ConnectionClose = $true

function Invoke-HeloGet($ip, $paramId) {
    try   { $raw = $client.GetStringAsync("http://$ip/config?action=get&paramid=$paramId").GetAwaiter().GetResult() }
    catch { return @{ Ok = $false; Raw = $null; Value = $null; ValueName = $null; Error = $_.Exception.GetBaseException().Message } }
    $val = $null; $vn = $null
    try { $j = $raw | ConvertFrom-Json; $val = "$($j.value)"; $vn = "$($j.value_name)" } catch { }
    return @{ Ok = $true; Raw = $raw; Value = $val; ValueName = $vn; Error = $null }
}

# Values are URL-encoded (spaces, backslashes, slashes, non-ASCII...) as the AJA docs require.
function Invoke-HeloSet($ip, $paramId, $value) {
    $enc = [System.Uri]::EscapeDataString("$value")
    try   { $raw = $client.GetStringAsync("http://$ip/config?action=set&paramid=$paramId&value=$enc").GetAwaiter().GetResult(); return @{ Ok = $true; Raw = $raw; Error = $null } }
    catch { return @{ Ok = $false; Raw = $null; Error = $_.Exception.GetBaseException().Message } }
}

# Sets a parameter, reads it back, compares.  State: OK | Differs | Failed
function Set-AndVerify($ip, $paramId, $value, $ignoreLeadingSlashes = $false) {
    $s = Invoke-HeloSet $ip $paramId $value
    if (-not $s.Ok) { return @{ State = 'Failed'; Text = "set failed ($($s.Error))" } }
    Start-Sleep -Milliseconds 300
    $g = Invoke-HeloGet $ip $paramId
    if (-not $g.Ok) { return @{ State = 'Differs'; Text = 'sent, but could not read it back' } }
    $a = "$($g.Value)".Trim(); $b = "$value".Trim()
    if ($ignoreLeadingSlashes) { $a = $a.TrimStart('/','\'); $b = $b.TrimStart('/','\') }   # the unit stores //VIFG/x for /VIFG/x
    if ($a -ceq $b) { return @{ State = 'OK'; Text = "ok (unit stores '$($g.Value)')" } }
    return @{ State = 'Differs'; Text = "reads back as '$($g.Value)'" }
}

# Read-only look at one unit.
function Get-HeloSnapshot($ip) {
    $snap = @{ Ok = $false; State = 'NO CONNECTION'; Profile = ''; Prefix = '' }
    $rec = Invoke-HeloGet $ip $params.RecState
    if (-not $rec.Ok) { return $snap }
    $snap.Ok = $true
    $str = Invoke-HeloGet $ip $params.StrState
    if (-not $str.Ok) { $snap.State = 'UNKNOWN'; return $snap }
    $recIdle = $rec.Raw -match 'Idle';     $recOn = $rec.Raw -match 'Recording'
    $strIdle = $str.Raw -match 'eRSSIdle'; $strOn = $str.Raw -match 'eRSSStreaming'
    if     ($recIdle -and $strIdle) { $snap.State = 'Idle' }
    elseif ($recOn   -and $strIdle) { $snap.State = 'RECORDING' }
    elseif ($recIdle -and $strOn)   { $snap.State = 'Streaming' }
    elseif ($recOn   -and $strOn)   { $snap.State = 'REC+STRM' }
    else                            { $snap.State = 'UNKNOWN' }
    $p = Invoke-HeloGet $ip $params.ProfileSel
    if ($p.Ok) { $snap.Profile = $p.Value }
    $x = Invoke-HeloGet $ip $params.Prefix
    if ($x.Ok) { $snap.Prefix = "$($x.Value)" }
    return $snap
}

# Applies the plan to one unit. Returns @{ Status = OK|Warn|Skipped|NoConn|Failed ; Text }
#   $plan = @{ Prefix = <string or $null> ; Template = <name or $null> ; SmbServer = ; SmbPath = }
function Invoke-HeloPush($ip, $plan) {
    $rec = Invoke-HeloGet $ip $params.RecState
    if (-not $rec.Ok) { return @{ Status = 'NoConn'; Text = 'no answer' } }
    $str = Invoke-HeloGet $ip $params.StrState
    if (-not $str.Ok) { return @{ Status = 'NoConn'; Text = 'stream state unreadable' } }
    if (-not ($rec.Raw -match 'Idle' -and $str.Raw -match 'eRSSIdle')) {
        return @{ Status = 'Skipped'; Text = 'skipped: recording, streaming or unknown state' }
    }
    $prof = Invoke-HeloGet $ip $params.ProfileSel
    if (-not $prof.Ok) { return @{ Status = 'Failed'; Text = 'cannot read the recording profile' } }
    if ("$($prof.Value)" -ne '0') {
        return @{ Status = 'Skipped'; Text = "skipped: profile $($prof.Value) is selected (expected the first profile, 0)" }
    }

    $notes = @(); $worst = 'OK'
    $record = {
        param($label, $r)
        $script:stepNotes += ("{0} {1}" -f $label, $r.Text)
        if ($r.State -eq 'Failed') { $script:stepWorst = 'Failed' }
        elseif ($r.State -eq 'Differs' -and $script:stepWorst -ne 'Failed') { $script:stepWorst = 'Warn' }
    }
    $script:stepNotes = @(); $script:stepWorst = 'OK'

    if ($plan.Template -and $params.Template) {
        $enum = $templateValues[$plan.Template]
        $s = Invoke-HeloSet $ip $params.Template $enum
        if ($s.Ok -and $params.TemplateApply) { $s = Invoke-HeloSet $ip $params.TemplateApply $templateApplyValue; Start-Sleep -Milliseconds 1500 }
        if (-not $s.Ok) { & $record 'template:' @{ State = 'Failed'; Text = "set failed ($($s.Error))" } }
        else {
            $g = Invoke-HeloGet $ip $params.Template
            if ($g.Ok -and ("$($g.Value)".Trim() -ceq "$enum".Trim() -or "$($g.ValueName)".Trim() -ceq $plan.Template)) { & $record 'template:' @{ State = 'OK'; Text = 'ok' } }
            else { & $record 'template:' @{ State = 'Differs'; Text = "reads back as '$($g.Value)' $($g.ValueName)" } }
        }
    }
    if ($null -ne $plan.SmbServer -and $params.SmbServer) { & $record 'smb server:' (Set-AndVerify $ip $params.SmbServer $plan.SmbServer) }
    if ($null -ne $plan.SmbPath   -and $params.SmbPath)   { & $record 'smb path:'   (Set-AndVerify $ip $params.SmbPath   $plan.SmbPath $true) }
    if ($plan.Prefix) { & $record 'prefix:' (Set-AndVerify $ip $params.Prefix $plan.Prefix) }
    elseif ($plan.WantPrefix) { $script:stepNotes += 'prefix: none in schedule' }

    return @{ Status = $script:stepWorst; Text = ($script:stepNotes -join '; ') }
}

# ---- Reading the .xlsx without Excel (an .xlsx is a zip of XML files) ----
function ConvertTo-ColIndex($letters) {
    $n = 0
    foreach ($ch in "$letters".ToUpper().ToCharArray()) { $n = $n * 26 + ([int]$ch - 64) }
    return $n
}
function ConvertTo-ColLetters($n) {
    $s = ''
    while ($n -gt 0) {
        $m = ($n - 1) % 26
        $s = ([string][char](65 + $m)) + $s
        $n = [int][math]::Floor(($n - 1) / 26)
    }
    return $s
}

# Returns an array of @{ Name; Cells = @{ "row,col" = text } } for every worksheet.
function Read-Xlsx($path) {
    Add-Type -AssemblyName System.IO.Compression
    $fs = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        $zip = New-Object System.IO.Compression.ZipArchive($fs, [System.IO.Compression.ZipArchiveMode]::Read)
        $loadXml = {
            param($entryName)
            $e = $zip.GetEntry($entryName)
            if (-not $e) { return $null }
            $st = $e.Open()
            try { $doc = New-Object System.Xml.XmlDocument; $doc.Load($st); return $doc } finally { $st.Dispose() }
        }

        $strings = @()
        $sx = & $loadXml 'xl/sharedStrings.xml'
        if ($sx) {
            foreach ($si in $sx.SelectNodes("//*[local-name()='si']")) {
                $txt = ''
                foreach ($t in $si.SelectNodes(".//*[local-name()='t'][not(ancestor::*[local-name()='rPh'])]")) { $txt += $t.InnerText }
                $strings += $txt
            }
        }

        $targets = @{}
        $rx = & $loadXml 'xl/_rels/workbook.xml.rels'
        if ($rx) {
            foreach ($rel in $rx.SelectNodes("//*[local-name()='Relationship']")) {
                $tg = $rel.GetAttribute('Target')
                if ($tg.StartsWith('/')) { $tg = $tg.Substring(1) } else { $tg = 'xl/' + $tg }
                $targets[$rel.GetAttribute('Id')] = $tg
            }
        }

        $result = @()
        $wx = & $loadXml 'xl/workbook.xml'
        $relNs = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
        foreach ($sh in $wx.SelectNodes("//*[local-name()='sheet']")) {
            $rid = $sh.GetAttribute('id', $relNs)
            $entry = $targets[$rid]
            $doc = $null
            if ($entry) { $doc = & $loadXml $entry }
            $cells = @{}
            if ($doc) {
                foreach ($c in $doc.SelectNodes("//*[local-name()='c']")) {
                    $ref = $c.GetAttribute('r')
                    if ($ref -notmatch '^([A-Za-z]+)(\d+)$') { continue }
                    $col = ConvertTo-ColIndex $Matches[1]
                    $row = [int]$Matches[2]
                    $kind = $c.GetAttribute('t')
                    $v = $c.SelectSingleNode("*[local-name()='v']")
                    $val = $null
                    if ($kind -eq 's')              { if ($v) { $val = $strings[[int]$v.InnerText] } }
                    elseif ($kind -eq 'inlineStr')  { $val = (($c.SelectNodes(".//*[local-name()='t']") | ForEach-Object { $_.InnerText }) -join '') }
                    elseif ($v)                     { $val = $v.InnerText }
                    if ($null -ne $val -and "$val".Trim() -ne '') { $cells["$row,$col"] = "$val".Trim() }
                }
            }
            $result += @{ Name = $sh.GetAttribute('name'); Cells = $cells }
        }
        return $result
    }
    finally { $fs.Dispose() }
}

# ---- Understanding the schedule ----
$heloIdRegex = '^(?i)\s*helo[\s\-_]*0*(\d+)\s*$'

# Finds the column that holds the "HELO-N" labels (the column with the most of them).
function Find-ScheduleColumn($sheet) {
    $byCol = @{}
    foreach ($k in $sheet.Cells.Keys) {
        if ($sheet.Cells[$k] -match $heloIdRegex) {
            $col = [int]$k.Split(',')[1]
            if (-not $byCol.ContainsKey($col)) { $byCol[$col] = 0 }
            $byCol[$col]++
        }
    }
    $best = $null
    foreach ($col in ($byCol.Keys | Sort-Object)) {
        if ($null -eq $best -or $byCol[$col] -gt $byCol[$best]) { $best = $col }
    }
    if ($null -eq $best) { return $null }
    return @{ Col = [int]$best; Count = $byCol[$best] }
}

# HELO number -> @{ Num; Row; Prefix; Note; Locked }.   Prefix is read from $prefixCol on the same row.
function Get-ScheduleMap($sheet, $recCol, $prefixCol) {
    $map = @{}
    $lastNoteCol = [math]::Max($prefixCol, $recCol + 3) + 1
    foreach ($k in $sheet.Cells.Keys) {
        $parts = $k.Split(',')
        if ([int]$parts[1] -ne $recCol) { continue }
        if ($sheet.Cells[$k] -notmatch $heloIdRegex) { continue }
        $num = [int]$Matches[1]
        $row = [int]$parts[0]
        $prefix = ''
        if ($sheet.Cells.ContainsKey("$row,$prefixCol")) { $prefix = $sheet.Cells["$row,$prefixCol"] }
        $note = @(); $locked = $false
        for ($c = $recCol + 1; $c -le $lastNoteCol; $c++) {
            if ($c -eq $prefixCol -or -not $sheet.Cells.ContainsKey("$row,$c")) { continue }
            $txt = $sheet.Cells["$row,$c"]
            $note += $txt
            if ($txt -match "(?i)don'?t\s+change|do\s+not\s+change") { $locked = $true }
        }
        $map[$num] = @{ Num = $num; Row = $row; Prefix = $prefix; Note = ($note -join ' | '); Locked = $locked }
    }
    return $map
}

#endregion Core logic

#region GUI
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$cfg = Import-HeloConfig
$script:devices = $cfg.Devices
Import-Presets

$script:sheets  = @()
$script:map     = @{}
$script:rows    = New-Object System.Collections.ArrayList
$script:busy    = $false
$script:stop    = $false
$script:loading = $false

$form = New-Object System.Windows.Forms.Form
$form.Text = 'AJA HELO Configure - push settings to recorders'
$form.StartPosition = 'CenterScreen'
$form.Size = New-Object System.Drawing.Size(1280, 780)
$form.MinimumSize = New-Object System.Drawing.Size(1000, 680)

function New-Label($text, $x, $y, $w) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $text; $l.AutoSize = $false
    $l.Location = New-Object System.Drawing.Point($x, $y); $l.Size = New-Object System.Drawing.Size($w, 20)
    return $l
}
function New-Btn($text, $x, $y, $w) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $text
    $b.Location = New-Object System.Drawing.Point($x, $y); $b.Size = New-Object System.Drawing.Size($w, 28)
    return $b
}

# --- list of units ---
$lv = New-Object System.Windows.Forms.ListView
$lv.Dock = 'Fill'; $lv.View = 'Details'; $lv.CheckBoxes = $true
$lv.FullRowSelect = $true; $lv.GridLines = $true; $lv.HideSelection = $false
foreach ($col in @(@('Unit', 150), @('IP', 110), @('Schedule', 80), @('Prefix to set', 130), @('State', 90), @('Current prefix', 130), @('Flags / result', 520))) {
    [void]$lv.Columns.Add($col[0], $col[1])
}

# --- top: schedule picker ---
$pnlTop = New-Object System.Windows.Forms.Panel
$pnlTop.Dock = 'Top'; $pnlTop.Height = 84
$lblSched = New-Label 'Schedule (.xlsx):' 12 14 110
$txtSched = New-Object System.Windows.Forms.TextBox
$txtSched.Location = New-Object System.Drawing.Point(124, 11); $txtSched.Size = New-Object System.Drawing.Size(560, 22); $txtSched.ReadOnly = $true
$btnBrowse = New-Btn 'Browse...' 692 9 90
$lblSheet = New-Label 'Sheet:' 12 50 50
$cmbSheet = New-Object System.Windows.Forms.ComboBox
$cmbSheet.DropDownStyle = 'DropDownList'
$cmbSheet.Location = New-Object System.Drawing.Point(64, 47); $cmbSheet.Size = New-Object System.Drawing.Size(220, 24)
$lblCol = New-Label 'Prefix column:' 300 50 90
$cmbCol = New-Object System.Windows.Forms.ComboBox
$cmbCol.DropDownStyle = 'DropDownList'
$cmbCol.Location = New-Object System.Drawing.Point(392, 47); $cmbCol.Size = New-Object System.Drawing.Size(56, 24)
foreach ($n in 1..26) { [void]$cmbCol.Items.Add((ConvertTo-ColLetters $n)) }
$lblInfo = New-Label 'Load the launch recording schedule to fill in the filename prefixes.' 460 50 760
$btnAll = New-Btn 'Select all' 800 9 90
$btnNone = New-Btn 'Select none' 896 9 90
$btnSafe = New-Btn 'All except locked' 992 9 120
$pnlTop.Controls.AddRange(@($lblSched, $txtSched, $btnBrowse, $lblSheet, $cmbSheet, $lblCol, $cmbCol, $lblInfo, $btnAll, $btnNone, $btnSafe))

# --- bottom: what to push ---
$grp = New-Object System.Windows.Forms.GroupBox
$grp.Dock = 'Bottom'; $grp.Height = 196
$grp.Text = 'Settings to push to the CHECKED units (always the first recording profile)'

$chkPrefix = New-Object System.Windows.Forms.CheckBox
$chkPrefix.Text = 'Filename prefix (from the schedule, one per unit)'
$chkPrefix.Location = New-Object System.Drawing.Point(14, 24); $chkPrefix.Size = New-Object System.Drawing.Size(420, 22); $chkPrefix.Checked = $true

$chkTemplate = New-Object System.Windows.Forms.CheckBox
$chkTemplate.Text = 'Recording template:'
$chkTemplate.Location = New-Object System.Drawing.Point(14, 54); $chkTemplate.Size = New-Object System.Drawing.Size(150, 22)
$cmbTemplate = New-Object System.Windows.Forms.ComboBox
$cmbTemplate.DropDownStyle = 'DropDownList'
$cmbTemplate.Location = New-Object System.Drawing.Point(170, 52); $cmbTemplate.Size = New-Object System.Drawing.Size(260, 24)
foreach ($k in $templateValues.Keys) { [void]$cmbTemplate.Items.Add($k) }
if ($cmbTemplate.Items.Count -gt 0) { $cmbTemplate.SelectedIndex = [math]::Max(0, $cmbTemplate.Items.IndexOf($defaultTemplate)) }

$chkServer = New-Object System.Windows.Forms.CheckBox
$chkServer.Text = 'SMB server address:'
$chkServer.Location = New-Object System.Drawing.Point(14, 84); $chkServer.Size = New-Object System.Drawing.Size(150, 22)
$cmbPreset = New-Object System.Windows.Forms.ComboBox
$cmbPreset.DropDownStyle = 'DropDownList'
$cmbPreset.Location = New-Object System.Drawing.Point(170, 82); $cmbPreset.Size = New-Object System.Drawing.Size(120, 24)
$txtServer = New-Object System.Windows.Forms.TextBox
$txtServer.Location = New-Object System.Drawing.Point(298, 82); $txtServer.Size = New-Object System.Drawing.Size(380, 22)
$btnSavePreset = New-Btn 'Save as preset...' 686 79 120

$chkPath = New-Object System.Windows.Forms.CheckBox
$chkPath.Text = 'SMB record path:'
$chkPath.Location = New-Object System.Drawing.Point(14, 114); $chkPath.Size = New-Object System.Drawing.Size(150, 22)
$txtPath = New-Object System.Windows.Forms.TextBox
$txtPath.Location = New-Object System.Drawing.Point(170, 112); $txtPath.Size = New-Object System.Drawing.Size(508, 22)

$btnCheck = New-Btn 'Check units (read only)' 14 150 170
$btnPush = New-Btn 'Push to checked units' 192 150 170
$btnPush.Font = New-Object System.Drawing.Font($btnPush.Font, [System.Drawing.FontStyle]::Bold)
$btnStop = New-Btn 'Stop' 370 150 80
$btnStop.Enabled = $false
$lblHint = New-Label 'Skips units that are recording/streaming or not on the first profile. Domain user/password are never changed.' 462 154 780

$grp.Controls.AddRange(@($chkPrefix, $chkTemplate, $cmbTemplate, $chkServer, $cmbPreset, $txtServer, $btnSavePreset, $chkPath, $txtPath, $btnCheck, $btnPush, $btnStop, $lblHint))

# Steps whose REST parameter is not known yet stay switched off.
foreach ($pair in @(@($chkTemplate, 'Template'), @($chkServer, 'SmbServer'), @($chkPath, 'SmbPath'))) {
    if (-not $params[$pair[1]]) {
        $pair[0].Enabled = $false
        $pair[0].Text += ' (ID not known yet)'
        $pair[0].Size = New-Object System.Drawing.Size(260, 22)
    }
}
if (-not $params.Template) { $cmbTemplate.Enabled = $false }
if (-not $params.SmbServer) { $cmbPreset.Enabled = $false; $txtServer.Enabled = $false; $btnSavePreset.Enabled = $false }
if (-not $params.SmbPath) { $txtPath.Enabled = $false }

function Update-PresetList {
    $cmbPreset.Items.Clear()
    foreach ($k in $script:presets.Keys) { [void]$cmbPreset.Items.Add($k) }
}
Update-PresetList

$lblStatus = New-Object System.Windows.Forms.Label
$lblStatus.Dock = 'Bottom'; $lblStatus.Height = 24; $lblStatus.TextAlign = 'MiddleLeft'
$lblStatus.Text = 'Ready.'

# Fill control first so the edge panels dock before it.
$form.Controls.AddRange(@($lv, $grp, $lblStatus, $pnlTop))

function Show-InputBox($title, $prompt) {
    $dlg = New-Object System.Windows.Forms.Form
    $dlg.Text = $title; $dlg.ClientSize = New-Object System.Drawing.Size(340, 104)
    $dlg.FormBorderStyle = 'FixedDialog'; $dlg.StartPosition = 'CenterParent'; $dlg.MaximizeBox = $false; $dlg.MinimizeBox = $false
    $l = New-Label $prompt 12 10 316
    $tb = New-Object System.Windows.Forms.TextBox
    $tb.Location = New-Object System.Drawing.Point(12, 34); $tb.Size = New-Object System.Drawing.Size(316, 22)
    $okBtn = New-Btn 'OK' 172 66 75; $okBtn.DialogResult = 'OK'
    $noBtn = New-Btn 'Cancel' 253 66 75; $noBtn.DialogResult = 'Cancel'
    $dlg.AcceptButton = $okBtn; $dlg.CancelButton = $noBtn
    $dlg.Controls.AddRange(@($l, $tb, $okBtn, $noBtn))
    $answer = $null
    if ($dlg.ShowDialog($form) -eq 'OK') { $answer = $tb.Text.Trim() }
    $dlg.Dispose()
    return $answer
}

#region Rows
function Set-RowResult($row, $status, $text) {
    $flags = $row.Flags
    $line = $text
    if ($flags) { $line = "$text   [$flags]" }
    $row.Item.SubItems[6].Text = $line
    switch ($status) {
        'OK'      { $row.Item.BackColor = [System.Drawing.Color]::FromArgb(200, 240, 200) }
        'Warn'    { $row.Item.BackColor = [System.Drawing.Color]::FromArgb(255, 225, 150) }
        'Failed'  { $row.Item.BackColor = [System.Drawing.Color]::FromArgb(255, 190, 190) }
        'NoConn'  { $row.Item.BackColor = [System.Drawing.Color]::FromArgb(255, 245, 150) }
        'Skipped' { $row.Item.BackColor = [System.Drawing.Color]::FromArgb(225, 225, 225) }
        default   { }
    }
}

function Build-Rows {
    $lv.BeginUpdate()
    $lv.Items.Clear()
    $script:rows.Clear()
    $helos = @($script:devices | Where-Object { $_.Type -ne 'GO' })

    # prefixes used by more than one HELO
    $byPrefix = @{}
    foreach ($num in $script:map.Keys) {
        $pf = $script:map[$num].Prefix
        if ($pf -ne '' -and $pf -notmatch '[\\/:*?"<>|]') {
            $key = $pf.ToLower()
            if (-not $byPrefix.ContainsKey($key)) { $byPrefix[$key] = @() }
            $byPrefix[$key] += $num
        }
    }

    for ($i = 0; $i -lt $helos.Count; $i++) {
        $num = $i + 1
        $dev = $helos[$i]
        $s = $null
        if ($script:map.ContainsKey($num)) { $s = $script:map[$num] }
        $flags = @(); $prefix = ''; $locked = $false; $schedId = ''
        if ($s) {
            $schedId = "HELO-$num"
            $prefix = $s.Prefix
            if ($s.Locked) { $locked = $true; $flags += "LOCKED: schedule says don't change ($($s.Note))" }
            if ($prefix -eq '') { if (-not $locked) { $flags += 'no prefix in schedule' } }
            elseif ($prefix -match '[\\/:*?"<>|]') { $flags += 'prefix has illegal characters, will not be set'; $prefix = '' }
            else {
                $others = @($byPrefix[$prefix.ToLower()] | Where-Object { $_ -ne $num })
                if ($others.Count -gt 0) { $flags += ('same prefix as HELO-' + ($others -join ', HELO-')) }
            }
        }
        elseif ($script:sheets.Count -gt 0) { $flags += 'not in the schedule' }

        $item = New-Object System.Windows.Forms.ListViewItem((Get-DisplayName $dev))
        [void]$item.SubItems.Add($dev.IP)
        [void]$item.SubItems.Add($schedId)
        [void]$item.SubItems.Add($prefix)
        [void]$item.SubItems.Add('')
        [void]$item.SubItems.Add('')
        [void]$item.SubItems.Add(($flags -join '; '))
        $item.Checked = (-not $locked)
        if ($locked) { $item.ForeColor = [System.Drawing.Color]::Gray }
        $row = @{ Dev = $dev; Num = $num; Prefix = $prefix; Locked = $locked; Flags = ($flags -join '; '); Item = $item }
        $item.Tag = $row
        [void]$script:rows.Add($row)
        [void]$lv.Items.Add($item)
    }
    $lv.EndUpdate()
    $lblStatus.Text = ("{0} HELOs listed. Ki Pro GOs are not handled here." -f $helos.Count)
}

function Update-Mapping {
    if ($script:loading) { return }
    $script:map = @{}
    $idx = $cmbSheet.SelectedIndex
    if ($idx -ge 0 -and $idx -lt $script:sheets.Count) {
        $sheet = $script:sheets[$idx]
        $info = Find-ScheduleColumn $sheet
        if ($info -and $cmbCol.SelectedIndex -ge 0) {
            $prefixCol = $cmbCol.SelectedIndex + 1
            $script:map = Get-ScheduleMap $sheet $info.Col $prefixCol
            $lblInfo.Text = ("Found {0} HELO rows (labels in column {1}); prefixes read from column {2}. Change the prefix column if the numbers look wrong." -f $info.Count, (ConvertTo-ColLetters $info.Col), (ConvertTo-ColLetters $prefixCol))
        }
        else { $lblInfo.Text = 'No "HELO-N" rows found on this sheet.' }
    }
    Build-Rows
}

function Import-ScheduleFile($path) {
    try   { $loaded = @(Read-Xlsx $path) }
    catch { [void][System.Windows.Forms.MessageBox]::Show($form, "Could not read the file:`r`n$($_.Exception.Message)", 'Schedule', 'OK', 'Warning'); return }
    $script:loading = $true
    $script:sheets = $loaded
    $txtSched.Text = $path
    $cmbSheet.Items.Clear()
    $bestIdx = 0; $bestCnt = -1
    for ($i = 0; $i -lt $loaded.Count; $i++) {
        $info = Find-ScheduleColumn $loaded[$i]
        $cnt = 0
        if ($info) { $cnt = $info.Count }
        [void]$cmbSheet.Items.Add(("{0}  ({1} HELO rows)" -f $loaded[$i].Name, $cnt))
        if ($cnt -gt $bestCnt) { $bestCnt = $cnt; $bestIdx = $i }
    }
    if ($loaded.Count -gt 0) {
        $cmbSheet.SelectedIndex = $bestIdx
        $info = Find-ScheduleColumn $loaded[$bestIdx]
        if ($info) { $cmbCol.SelectedIndex = [math]::Min(25, $info.Col + 1) }   # prefix is usually two columns right of the labels
    }
    $script:loading = $false
    if ($bestCnt -le 0) { [void][System.Windows.Forms.MessageBox]::Show($form, 'No "HELO-N" rows were found in that workbook.', 'Schedule', 'OK', 'Warning') }
    Update-Mapping
}
#endregion Rows

#region Running
function Set-Busy($on) {
    $script:busy = $on
    $btnCheck.Enabled = -not $on; $btnPush.Enabled = -not $on; $btnBrowse.Enabled = -not $on
    $btnStop.Enabled = $on
    if ($on) { $script:stop = $false }
}

function Invoke-CheckUnits {
    $targets = @($lv.CheckedItems | ForEach-Object { $_.Tag })
    if ($targets.Count -eq 0) { return }
    Set-Busy $true
    $n = 0
    foreach ($row in $targets) {
        if ($script:stop) { break }
        $n++
        $lblStatus.Text = ("Checking {0} of {1}: {2}" -f $n, $targets.Count, $row.Dev.IP)
        [System.Windows.Forms.Application]::DoEvents()
        $snap = Get-HeloSnapshot $row.Dev.IP
        $row.Item.SubItems[4].Text = $snap.State
        $row.Item.SubItems[5].Text = $snap.Prefix
        $text = 'checked'
        $status = 'OK'
        if (-not $snap.Ok) { $text = 'no answer'; $status = 'NoConn' }
        elseif ($snap.State -ne 'Idle') { $text = 'will be skipped (not idle)'; $status = 'Skipped' }
        elseif ("$($snap.Profile)" -ne '0') { $text = "will be skipped (profile $($snap.Profile) selected)"; $status = 'Skipped' }
        Set-RowResult $row $status $text
        [System.Windows.Forms.Application]::DoEvents()
    }
    $lblStatus.Text = 'Check finished. Nothing was changed.'
    Set-Busy $false
}

function Invoke-PushUnits {
    $targets = @($lv.CheckedItems | ForEach-Object { $_.Tag })
    if ($targets.Count -eq 0) { [void][System.Windows.Forms.MessageBox]::Show($form, 'No units are checked.', 'Push'); return }

    $plan = @{ Prefix = $null; WantPrefix = $false; Template = $null; SmbServer = $null; SmbPath = $null }
    $lines = @()
    if ($chkPrefix.Checked) { $plan.WantPrefix = $true; $lines += 'Filename prefix from the schedule (per unit)' }
    if ($chkTemplate.Checked -and $chkTemplate.Enabled) {
        if (-not $templateValues[$cmbTemplate.Text]) { [void][System.Windows.Forms.MessageBox]::Show($form, 'The template value is not known yet.', 'Push'); return }
        $plan.Template = $cmbTemplate.Text; $lines += "Recording template: $($cmbTemplate.Text)"
    }
    if ($chkServer.Checked -and $chkServer.Enabled) {
        if ($txtServer.Text.Trim() -eq '') { [void][System.Windows.Forms.MessageBox]::Show($form, 'Enter the SMB server address.', 'Push'); return }
        $plan.SmbServer = $txtServer.Text.Trim(); $lines += "SMB server address: $($plan.SmbServer)"
    }
    if ($chkPath.Checked -and $chkPath.Enabled) {
        if ($txtPath.Text.Trim() -eq '') { [void][System.Windows.Forms.MessageBox]::Show($form, 'Enter the SMB record path.', 'Push'); return }
        $plan.SmbPath = $txtPath.Text.Trim(); $lines += "SMB record path: $($plan.SmbPath)"
    }
    if ($lines.Count -eq 0) { [void][System.Windows.Forms.MessageBox]::Show($form, 'Nothing selected to push.', 'Push'); return }

    $lockedPicked = @($targets | Where-Object { $_.Locked })
    $msg = "Push to $($targets.Count) unit(s):`r`n`r`n  - " + ($lines -join "`r`n  - ") + "`r`n`r`nUnits that are recording/streaming, or not on the first profile, are skipped."
    if ($lockedPicked.Count -gt 0) {
        $msg += "`r`n`r`nWARNING: $($lockedPicked.Count) checked unit(s) are marked DON'T CHANGE in the schedule:`r`n  " + (($lockedPicked | ForEach-Object { Get-DisplayName $_.Dev }) -join ', ')
    }
    $msg += "`r`n`r`nContinue?"
    $icon = 'Question'; if ($lockedPicked.Count -gt 0) { $icon = 'Warning' }
    if ([System.Windows.Forms.MessageBox]::Show($form, $msg, 'Push settings', 'YesNo', $icon) -ne 'Yes') { return }

    Set-Busy $true
    $counts = @{ OK = 0; Warn = 0; Skipped = 0; NoConn = 0; Failed = 0 }
    $n = 0
    foreach ($row in $targets) {
        if ($script:stop) { break }
        $n++
        $lblStatus.Text = ("Pushing {0} of {1}: {2} ({3})" -f $n, $targets.Count, (Get-DisplayName $row.Dev), $row.Dev.IP)
        [System.Windows.Forms.Application]::DoEvents()
        $unitPlan = @{ Prefix = $null; WantPrefix = $plan.WantPrefix; Template = $plan.Template; SmbServer = $plan.SmbServer; SmbPath = $plan.SmbPath }
        if ($plan.WantPrefix -and $row.Prefix -ne '') { $unitPlan.Prefix = $row.Prefix }
        $res = Invoke-HeloPush $row.Dev.IP $unitPlan
        $counts[$res.Status]++
        Set-RowResult $row $res.Status $res.Text
        [System.Windows.Forms.Application]::DoEvents()
    }
    $summary = "Done. OK: {0}   differs: {1}   skipped: {2}   no answer: {3}   failed: {4}" -f $counts.OK, $counts.Warn, $counts.Skipped, $counts.NoConn, $counts.Failed
    if ($script:stop) { $summary = "Stopped. " + $summary }
    $lblStatus.Text = $summary
    Set-Busy $false
    [void][System.Windows.Forms.MessageBox]::Show($form, $summary + "`r`n`r`nDetails are in the Flags / result column.", 'Push finished')
}
#endregion Running

#region Events
$btnBrowse.Add_Click({
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Filter = 'Excel workbook (*.xlsx)|*.xlsx'
    $dlg.Title = 'Launch recording schedule'
    if ($dlg.ShowDialog($form) -eq 'OK') { Import-ScheduleFile $dlg.FileName }
    $dlg.Dispose()
})
$cmbSheet.Add_SelectedIndexChanged({
    if ($script:loading) { return }
    $idx = $cmbSheet.SelectedIndex
    if ($idx -ge 0 -and $idx -lt $script:sheets.Count) {
        $info = Find-ScheduleColumn $script:sheets[$idx]
        if ($info) { $script:loading = $true; $cmbCol.SelectedIndex = [math]::Min(25, $info.Col + 1); $script:loading = $false }
    }
    Update-Mapping
})
$cmbCol.Add_SelectedIndexChanged({ Update-Mapping })
$cmbPreset.Add_SelectedIndexChanged({
    if ($cmbPreset.SelectedItem) { $txtServer.Text = $script:presets[[string]$cmbPreset.SelectedItem] }
})
$btnSavePreset.Add_Click({
    $val = $txtServer.Text.Trim()
    if ($val -eq '') { return }
    $name = Show-InputBox 'Save preset' 'Name for this SMB server address (e.g. Atlas):'
    if (-not $name) { return }
    $script:presets[$name] = $val
    try { Save-Presets } catch { [void][System.Windows.Forms.MessageBox]::Show($form, "Could not save presets: $($_.Exception.Message)", 'Presets') }
    Update-PresetList
})
$btnAll.Add_Click({ foreach ($it in $lv.Items) { $it.Checked = $true } })
$btnNone.Add_Click({ foreach ($it in $lv.Items) { $it.Checked = $false } })
$btnSafe.Add_Click({ foreach ($it in $lv.Items) { $it.Checked = (-not $it.Tag.Locked) } })
$btnCheck.Add_Click({ Invoke-CheckUnits })
$btnPush.Add_Click({ Invoke-PushUnits })
$btnStop.Add_Click({ $script:stop = $true; $lblStatus.Text = 'Stopping after the current unit...' })
$form.Add_Shown({
    if ($cfg.Warnings.Count -gt 0) { [void][System.Windows.Forms.MessageBox]::Show($form, ($cfg.Warnings -join "`r`n"), 'HELO list', 'OK', 'Warning') }
})
$form.Add_FormClosing({ $client.Dispose() })
#endregion Events

Build-Rows
[void]$form.ShowDialog()
$form.Dispose()
#endregion GUI
