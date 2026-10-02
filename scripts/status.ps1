<#
.SYNOPSIS
  One-screen health report: VM power state, DNS, HTTPS + certificate expiry, redirect, login wall,
  container health, auto-shutdown (should be off) and the running cost. Read-only: changes nothing.
#>
. "$PSScriptRoot\common.ps1"

function Show([string]$Label, [string]$Value, [bool]$Good = $true) {
    $color = if ($Good) { 'Green' } else { 'Yellow' }
    Write-Host ("  {0,-24} " -f $Label) -NoNewline
    Write-Host $Value -ForegroundColor $color
}

Assert-LoggedIn
if (-not (Test-ResourceGroup)) { Write-Host "Resource group '$($Cfg.ResourceGroup)' does not exist (nothing deployed, cost 0)."; return }
$ip = Get-PublicIp
$d = $Cfg.Domain

Write-Step 'Azure'
$power = Get-PowerState
Show 'VM power state' $power ($power -eq 'VM running')
Show 'Public IP' $ip
$schedules = Invoke-Az resource list -g $Cfg.ResourceGroup --resource-type Microsoft.DevTestLab/schedules --query '[] | length(@)' -o tsv
Show 'Auto-shutdown' ($(if ([int]$schedules -gt 0) { 'ON (VM stops at 02:00 IST)' } else { 'off (24/7)' })) ([int]$schedules -eq 0)

Write-Step 'DNS and HTTPS'
$resolved = Resolve-Domain
Show "DNS $d" ($(if ($resolved) { $resolved -join ', ' } else { 'does not resolve' })) ($resolved -contains $ip)
if ($power -eq 'VM running' -and $resolved -contains $ip) {
    try {
        $cert = Get-CertInfo $d
        Show 'Certificate' ("{0}, issued by {1}" -f $(if ($cert.Valid) { 'valid' } else { 'NOT valid' }), $cert.Issuer) $cert.Valid
        Show 'Certificate expires' ("{0:d MMM yyyy} ({1} days; Caddy renews ~30 days before)" -f $cert.NotAfter, $cert.DaysLeft) ($cert.DaysLeft -gt 14)
    } catch { Show 'Certificate' "error: $($_.Exception.Message)" $false }
    try {
        $r = Get-HttpStatus "http://$d/"
        Show 'HTTP -> HTTPS redirect' "$($r.Code) $($r.Location)" ($r.Code -in 301, 308)
        $a = Get-HttpStatus "https://$d/api/tasks"
        Show '/api without login' "$($a.Code)" ($a.Code -eq 401)
        $h = Invoke-WebRequest -Uri "https://$d/health" -UseBasicParsing -TimeoutSec 10
        Show '/health' "$($h.StatusCode) $($h.Content)" ($h.StatusCode -eq 200)
        $hsts = $h.Headers['Strict-Transport-Security']
        Show 'HSTS' ($(if ($hsts) { $hsts } else { 'not sent' })) ($hsts -match 'max-age=[1-9]')
    } catch { Show 'HTTPS checks' "error: $($_.Exception.Message)" $false }
}

if ($power -eq 'VM running') {
    Write-Step 'Containers (over SSH)'
    try {
        Invoke-Vm $ip 'sudo docker compose -f /opt/cloudtasks/docker-compose.yml ps --format table'
        Write-Host ''
        Invoke-Vm $ip 'sudo docker compose -f /opt/cloudtasks/docker-compose.yml logs caddy --since 1440h 2>&1 | grep -iE certificate.obtained\|renew | tail -n 3 || true'
    } catch { Show 'SSH' "not reachable from this IP ($($_.Exception.Message)); start.ps1 re-pins the SSH rule" $false }
}

Write-Step 'Cost'
$c = Get-CostModel
Show-CostTable $c
