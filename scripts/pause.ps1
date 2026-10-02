<#
.SYNOPSIS
  Pause CloudTasks: deallocate the VM. Compute billing stops; the disk, the static IP and the data stay.
#>
. "$PSScriptRoot\common.ps1"

Assert-LoggedIn
if (-not (Test-ResourceGroup)) { throw "Resource group '$($Cfg.ResourceGroup)' not found. Nothing to pause." }

Write-Step "Deallocating $($Cfg.VmName) (takes about a minute)"
Invoke-Az vm deallocate -g $Cfg.ResourceGroup -n $Cfg.VmName -o none
Write-Ok "Power state: $(Get-PowerState)"

$c = Get-CostModel
Write-Host ''
Write-Host ('While paused you pay about {0:N3} USD/day ({1:N2} USD/month): OS disk {2:N3} + static public IP {3:N3} per day.' -f `
    $c.PausedDay, ($c.PausedDay * 30.4), $c.DiskDay, $c.IpDay)
Write-Host ('Running costs about {0:N3} USD/day. The IP address and your tasks are kept. Start again with ./scripts/start.ps1' -f $c.RunningDay)
