<#
.SYNOPSIS
  Start CloudTasks again after pause.ps1: start the VM, wait, verify over HTTPS, print the URL.
  Also updates the SSH rule if your own public IP has changed since the last run.
#>
. "$PSScriptRoot\common.ps1"

Assert-LoggedIn
if (-not (Test-ResourceGroup)) { throw "Resource group '$($Cfg.ResourceGroup)' not found. Run deploy.ps1 first." }

Write-Step "Starting $($Cfg.VmName)"
Invoke-Az vm start -g $Cfg.ResourceGroup -n $Cfg.VmName -o none
Write-Ok "Power state: $(Get-PowerState)"

Write-Step 'Keeping the SSH rule pinned to your current IP'
$myIp = (Invoke-RestMethod -Uri 'https://api.ipify.org' -TimeoutSec 10).ToString().Trim()
$ruleIp = Invoke-Az network nsg rule show -g $Cfg.ResourceGroup --nsg-name $Cfg.NsgName -n AllowSshFromMyIp `
    --query sourceAddressPrefix -o tsv
if ($ruleIp -ne "$myIp/32") {
    Invoke-Az network nsg rule update -g $Cfg.ResourceGroup --nsg-name $Cfg.NsgName -n AllowSshFromMyIp `
        --source-address-prefixes "$myIp/32" -o none
    Write-Ok "SSH rule updated to $myIp/32"
} else {
    Write-Ok "SSH rule already $myIp/32"
}

$ip = Get-PublicIp
Write-Step 'Waiting for the site (Docker restarts the containers on boot)'
Wait-Http "https://$($Cfg.Domain)/health" -TimeoutSec 300 | Out-Null
Test-Site $ip

$c = Get-CostModel
Write-Host "`nCloudTasks is live:  https://$($Cfg.Domain)/" -ForegroundColor Green
Write-Host ('Running 24/7 costs about {0:N3} USD/day. Pause with ./scripts/pause.ps1.' -f $c.RunningDay)
