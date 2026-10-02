<#
.SYNOPSIS
  Redeploy the latest code from GitHub to the running VM and verify it over HTTPS.

.DESCRIPTION
  Idempotent; safe to run again:
  1. Makes sure the NSG allows HTTPS (443) and that no auto-shutdown schedule exists (24/7 hosting).
  2. Makes sure the VM's .env has HOST_NAME, SITE_ADDRESS, CANONICAL_URL and HSTS_MAX_AGE
     (existing values are kept; only missing keys are added). Nothing secret is written.
  3. git pull + docker compose up -d --build --remove-orphans (the database and certificate
     volumes are kept; --remove-orphans removes containers of services that no longer exist).
  4. With -EnableHsts: sets HSTS_MAX_AGE=31536000 (1 year). Use only after HTTPS works.
  5. Verifies HTTPS, the HTTP redirect, the login wall and /health from this laptop.
#>
param([switch]$EnableHsts)
. "$PSScriptRoot\common.ps1"

Assert-LoggedIn
if (-not (Test-ResourceGroup)) { throw "Resource group '$($Cfg.ResourceGroup)' not found. Run deploy.ps1 first." }
if ((Get-PowerState) -ne 'VM running') { throw 'The VM is not running. Run ./scripts/start.ps1 first.' }
$ip = Get-PublicIp

Write-Step 'Network: HTTPS rule and 24/7 hosting'
$https = Invoke-Az network nsg rule list -g $Cfg.ResourceGroup --nsg-name $Cfg.NsgName --query "[?name=='AllowHttps'] | length(@)" -o tsv
if ([int]$https -eq 0) {
    Invoke-Az network nsg rule create -g $Cfg.ResourceGroup --nsg-name $Cfg.NsgName -n AllowHttps `
        --priority 120 --direction Inbound --access Allow --protocol Tcp `
        --source-address-prefixes Internet --destination-port-ranges 443 -o none
    Write-Ok 'Added NSG rule AllowHttps (443 from Internet)'
} else { Write-Ok 'NSG already allows 443' }
$schedules = Invoke-Az resource list -g $Cfg.ResourceGroup --resource-type Microsoft.DevTestLab/schedules --query '[] | length(@)' -o tsv
if ([int]$schedules -gt 0) {
    Invoke-Az vm auto-shutdown -g $Cfg.ResourceGroup -n $Cfg.VmName --off -o none
    Write-Ok 'Removed the daily auto-shutdown schedule (the VM now runs 24/7)'
} else { Write-Ok 'No auto-shutdown schedule (24/7)' }

Write-Step "Updating the configuration and code on $ip"
$d = $Cfg.Domain
# No double quotes in these commands (see Invoke-Vm). Values contain no spaces or secrets.
$steps = @(
    'cd /opt/cloudtasks',
    '(grep -q ^HOST_NAME= .env || echo HOST_NAME=$(hostname) | sudo tee -a .env >/dev/null)',
    "(grep -q ^SITE_ADDRESS= .env || echo SITE_ADDRESS=$d | sudo tee -a .env >/dev/null)",
    "(grep -q ^CANONICAL_URL= .env || echo CANONICAL_URL=https://$d | sudo tee -a .env >/dev/null)",
    '(grep -q ^HSTS_MAX_AGE= .env || echo HSTS_MAX_AGE=0 | sudo tee -a .env >/dev/null)'
)
if ($EnableHsts) { $steps += 'sudo sed -i s/^HSTS_MAX_AGE=.*/HSTS_MAX_AGE=31536000/ .env' }
$steps += @(
    'sudo git pull --ff-only',
    'sudo git log -1 --oneline',
    'sudo docker compose up -d --build --remove-orphans 2>&1 | tail -n 6',
    'grep -E ^SITE_ADDRESS=\|^CANONICAL_URL=\|^HSTS_MAX_AGE=\|^HOST_NAME= .env'
)
Invoke-Vm $ip ($steps -join ' && ')

Write-Step 'Verifying from this laptop'
Wait-Http "https://$d/health" -TimeoutSec 300 | Out-Null
Test-Site $ip
if ($EnableHsts) {
    $hsts = (Invoke-WebRequest -Uri "https://$d/login" -UseBasicParsing -TimeoutSec 10).Headers['Strict-Transport-Security']
    Write-Ok "HSTS header: $hsts"
}
Write-Host "`nUpdated: https://$d/" -ForegroundColor Green
