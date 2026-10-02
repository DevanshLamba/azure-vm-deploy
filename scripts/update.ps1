<#
.SYNOPSIS
  Redeploy the latest code from GitHub to the running VM: git pull + docker compose up -d --build.
  The database volume and the .env with the AZURE_* values are kept. Then verifies the site.
#>
. "$PSScriptRoot\common.ps1"

Assert-LoggedIn
if (-not (Test-ResourceGroup)) { throw "Resource group '$($Cfg.ResourceGroup)' not found. Run deploy.ps1 first." }
if ((Get-PowerState) -ne 'VM running') { throw 'The VM is not running. Run ./scripts/start.ps1 first.' }
$ip = Get-PublicIp

Write-Step "Pulling the latest code and rebuilding on $ip"
& ssh -i $Cfg.SshKey -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts_cloudtasks" `
    "$($Cfg.AdminUser)@$ip" 'cd /opt/cloudtasks && sudo git pull --ff-only && sudo git log -1 --format="deployed commit: %h %s" && sudo docker compose up -d --build 2>&1 | tail -n 4'
if ($LASTEXITCODE -ne 0) { throw 'Update over SSH failed.' }

Write-Step 'Verifying'
Wait-Http "http://$ip/health" -TimeoutSec 300 | Out-Null
Test-Site $ip
Write-Host "`nUpdated: http://$ip/" -ForegroundColor Green
