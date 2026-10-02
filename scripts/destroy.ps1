<#
.SYNOPSIS
  Delete everything: the whole resource group (VM, disk, IP, NSG, VNet). Cost afterwards: 0.
  Asks you to type the resource group name to confirm. Your tasks are deleted with the disk.
#>
param([switch]$AssumeYes)   # skip the prompt: only when you're sure
. "$PSScriptRoot\common.ps1"

Assert-LoggedIn
if (-not (Test-ResourceGroup)) { Write-Ok "Resource group '$($Cfg.ResourceGroup)' does not exist. Nothing to delete."; return }

Write-Step "Resources in $($Cfg.ResourceGroup) that will be deleted"
Invoke-Az resource list -g $Cfg.ResourceGroup --query "[].{Type:type, Name:name}" -o table | Write-Host

if (-not $AssumeYes) {
    $answer = Read-Host "`nThis cannot be undone. Type the resource group name ($($Cfg.ResourceGroup)) to delete it"
    if ($answer -ne $Cfg.ResourceGroup) { Write-Host 'Cancelled. Nothing was deleted.'; return }
}

Write-Step 'Deleting the resource group (takes a few minutes)'
Invoke-Az group delete -n $Cfg.ResourceGroup --yes -o none
if (Test-ResourceGroup) { throw 'The resource group still exists. Check the portal.' }
Write-Ok "Resource group '$($Cfg.ResourceGroup)' deleted. Ongoing cost: 0 USD/day."
Write-Info "Your SSH key is still on this laptop ($($Cfg.SshKey)); delete it too if you no longer need it."
