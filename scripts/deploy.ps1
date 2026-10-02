<#
.SYNOPSIS
  Deploy CloudTasks to a new Ubuntu 24.04 VM on Azure (IaaS), behind nginx on port 80.

.DESCRIPTION
  1. Checks login, the student "allowed regions" policy, auto-shutdown support and VM size availability.
  2. Shows the planned resources and their cost per day, then waits for you to type "yes".
  3. Creates: resource group, static public IP, NSG (SSH only from your IP /32, HTTP open),
     VNet/subnet, the VM (SSH key only) with cloud-init (installs Docker, clones the repo,
     runs docker compose up -d), and auto-shutdown at 02:00 IST.
  4. Verifies http://<ip>/ and /health from this laptop and prints the URL.

.EXAMPLE
  ./scripts/deploy.ps1             # interactive: shows the plan, asks for "yes"
  ./scripts/deploy.ps1 -PlanOnly   # only show the plan and cost, create nothing
#>
param(
    [switch]$PlanOnly,
    [switch]$AssumeYes   # skip the prompt: only after you've reviewed the plan output
)
. "$PSScriptRoot\common.ps1"

# ---------------------------------------------------------------- 1. pre-flight checks
Write-Step 'Checking Azure login'
Assert-LoggedIn

if (Test-ResourceGroup) {
    if (-not $PlanOnly) {
        throw "Resource group '$($Cfg.ResourceGroup)' already exists. Use start.ps1 / pause.ps1, or destroy.ps1 first."
    }
    Write-Info "Note: '$($Cfg.ResourceGroup)' already exists (deployed). Showing the plan for reference only."
}

Write-Step 'Checking the subscription policy (allowed regions)'
$allowed = Invoke-Az policy assignment list `
    --query "[].parameters.listOfAllowedLocations.value" -o tsv
$allowed = @($allowed -split "`t|`r?`n" | Where-Object { $_ })
if ($allowed.Count -gt 0) {
    Write-Info "Allowed regions: $($allowed -join ', ')"
    if ($allowed -notcontains $Cfg.Region) { throw "Region $($Cfg.Region) is not allowed by your policy." }
    Write-Ok "$($Cfg.Region) is allowed"
} else {
    Write-Info 'No allowed-regions policy found (no restriction).'
}

Write-Step 'Checking that auto-shutdown works in this region'
$regionName = Invoke-Az account list-locations --query "[?name=='$($Cfg.Region)'].displayName | [0]" -o tsv
$shutdownRegions = Invoke-Az provider show -n Microsoft.DevTestLab `
    --query "resourceTypes[?resourceType=='schedules'].locations | [0]" -o tsv
if (@($shutdownRegions -split "`t|`r?`n") -notcontains $regionName) {
    throw "Auto-shutdown (Microsoft.DevTestLab/schedules) is not available in $regionName."
}
Write-Ok "Auto-shutdown is supported in $regionName"

Write-Step "Checking that $($Cfg.VmSize) is available to this subscription in $($Cfg.Region)"
# az rest fills in {subscriptionId} itself, so the ID is never printed.
# (Query string passed via --uri-parameters: a literal "&" would be eaten by cmd.exe, which runs az.cmd.)
$skuUrl = 'https://management.azure.com/subscriptions/{subscriptionId}/providers/Microsoft.Compute/skus'
$skuParams = @('api-version=2021-07-01', "`$filter=location eq '$($Cfg.Region)'")
$skuInfo = Invoke-Az rest --method get --url $skuUrl --uri-parameters @skuParams `
    --query "value[?name=='$($Cfg.VmSize)' && resourceType=='virtualMachines'] | [0].{name:name, restrictions:join(',', restrictions[].reasonCode)}" -o json |
    Out-String | ConvertFrom-Json
if (-not $skuInfo) { throw "$($Cfg.VmSize) does not exist in $($Cfg.Region)." }
if ($skuInfo.restrictions) { throw "$($Cfg.VmSize) is restricted in $($Cfg.Region): $($skuInfo.restrictions)" }
Write-Ok "$($Cfg.VmSize) is available (no restrictions)"

Write-Step 'Finding your current public IP (for the SSH rule)'
$myIp = (Invoke-RestMethod -Uri 'https://api.ipify.org' -TimeoutSec 10).ToString().Trim()
if ($myIp -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { throw "Could not detect your public IPv4 address (got '$myIp')." }
Write-Ok "SSH (22) will be allowed only from $myIp/32"

# ---------------------------------------------------------------- 2. plan + cost
Write-Step 'Fetching live prices (Azure Retail Prices API)'
$cost = Get-CostModel

Write-Host "`nPLANNED RESOURCES  (region: $($Cfg.Region) / $regionName)" -ForegroundColor Yellow
$plan = @(
    [pscustomobject]@{ Resource = 'Resource group'; Name = $Cfg.ResourceGroup; Details = 'container for everything below';                             'USD/day' = '0' }
    [pscustomobject]@{ Resource = 'Virtual machine'; Name = $Cfg.VmName;      Details = "$($Cfg.VmSize), Ubuntu 24.04 Arm64, SSH key only";          'USD/day' = '{0:N3}' -f $cost.VmDay }
    [pscustomobject]@{ Resource = 'OS disk';        Name = "$($Cfg.VmName)-osdisk"; Details = "$($Cfg.OsDiskSku) $($Cfg.OsDiskGb) GB (E4)";          'USD/day' = '{0:N3}' -f $cost.DiskDay }
    [pscustomobject]@{ Resource = 'Public IP';      Name = $Cfg.PublicIpName; Details = 'Standard SKU, static IPv4';                                'USD/day' = '{0:N3}' -f $cost.IpDay }
    [pscustomobject]@{ Resource = 'NSG';            Name = $Cfg.NsgName;      Details = "22 from $myIp/32, 80 from anywhere, rest denied";            'USD/day' = '0' }
    [pscustomobject]@{ Resource = 'VNet + subnet';  Name = $Cfg.VnetName;     Details = '10.20.0.0/24, subnet 10.20.0.0/26';                          'USD/day' = '0' }
    [pscustomobject]@{ Resource = 'NIC';            Name = "$($Cfg.VmName)VMNic"; Details = 'attached to VM, NSG and public IP';                     'USD/day' = '0' }
    [pscustomobject]@{ Resource = 'Auto-shutdown';  Name = "shutdown-computevm-$($Cfg.VmName)"; Details = '02:00 IST (20:30 UTC) every day';       'USD/day' = '0' }
)
$plan | Format-Table -AutoSize | Out-String | Write-Host
Show-CostTable $cost
Write-Host ("`nPrices: VM {0} USD/h, public IP {1} USD/h, disk {2} USD/month. Billed to your Azure for Students credit (no card)." -f $cost.VmHour, $cost.IpHour, $cost.DiskMonth)
Write-Host 'Outbound data: first 100 GB/month free. Auto-shutdown deallocates the VM at 02:00 IST, so the VM part stops being billed overnight.'

if ($PlanOnly) { Write-Host "`nPlan only: nothing was created." -ForegroundColor Yellow; return }

if (-not $AssumeYes) {
    $answer = Read-Host "`nCreate these resources? Type 'yes' to continue"
    if ($answer -ne 'yes') { Write-Host 'Cancelled. Nothing was created.'; return }
}

# ---------------------------------------------------------------- 3. create
Write-Step 'SSH key'
if (-not (Test-Path $Cfg.SshKey)) {
    New-Item -ItemType Directory -Force (Split-Path $Cfg.SshKey) | Out-Null
    & ssh-keygen -t ed25519 -f $Cfg.SshKey -N '""' -C 'cloudtasks-azure' -q
    if ($LASTEXITCODE -ne 0) { throw 'ssh-keygen failed' }
    Write-Ok "Created new key $($Cfg.SshKey) (private key stays on this laptop)"
} else {
    Write-Ok "Using existing key $($Cfg.SshKey)"
}
$pubKey = "$($Cfg.SshKey).pub"

$tags = 'project=cloudtasks', 'purpose=pbl'
Write-Step "Creating resource group $($Cfg.ResourceGroup)"
Invoke-Az group create -n $Cfg.ResourceGroup -l $Cfg.Region --tags @tags -o none
Write-Ok 'Resource group created'

Write-Step 'Creating static public IP'
Invoke-Az network public-ip create -g $Cfg.ResourceGroup -n $Cfg.PublicIpName --sku Standard `
    --allocation-method Static --version IPv4 --tags @tags -o none
$ip = Get-PublicIp
Write-Ok "Public IP: $ip"

Write-Step 'Creating network security group'
Invoke-Az network nsg create -g $Cfg.ResourceGroup -n $Cfg.NsgName --tags @tags -o none
Invoke-Az network nsg rule create -g $Cfg.ResourceGroup --nsg-name $Cfg.NsgName -n AllowSshFromMyIp `
    --priority 100 --direction Inbound --access Allow --protocol Tcp `
    --source-address-prefixes "$myIp/32" --destination-port-ranges 22 -o none
Invoke-Az network nsg rule create -g $Cfg.ResourceGroup --nsg-name $Cfg.NsgName -n AllowHttp `
    --priority 110 --direction Inbound --access Allow --protocol Tcp `
    --source-address-prefixes Internet --destination-port-ranges 80 -o none
Write-Ok "NSG: 22 from $myIp/32, 80 from Internet (everything else denied by default)"

Write-Step 'Creating virtual network'
Invoke-Az network vnet create -g $Cfg.ResourceGroup -n $Cfg.VnetName --address-prefix 10.20.0.0/24 `
    --subnet-name $Cfg.SubnetName --subnet-prefix 10.20.0.0/26 --tags @tags -o none
Write-Ok 'VNet created'

Write-Step 'Preparing cloud-init (Docker install + git clone + docker compose up)'
$cloudInit = (Get-Content "$PSScriptRoot\cloud-init.yaml" -Raw).
    Replace('__REGION__', $Cfg.Region).
    Replace('__VM_SIZE__', $Cfg.VmSize).
    Replace('__VM_NAME__', $Cfg.VmName).
    Replace('__PUBLIC_IP__', $ip).
    Replace('__ADMIN_USER__', $Cfg.AdminUser).
    Replace('__REPO_URL__', $Cfg.RepoUrl)
$cloudInitFile = Join-Path ([IO.Path]::GetTempPath()) 'cloudtasks-cloud-init.yaml'
[IO.File]::WriteAllText($cloudInitFile, ($cloudInit -replace "`r`n", "`n"))
Write-Ok 'cloud-init rendered'

Write-Step "Creating VM $($Cfg.VmName) ($($Cfg.VmSize)), takes 1-3 minutes"
Invoke-Az vm create -g $Cfg.ResourceGroup -n $Cfg.VmName -l $Cfg.Region `
    --image $Cfg.Image --size $Cfg.VmSize `
    --admin-username $Cfg.AdminUser --authentication-type ssh --ssh-key-values $pubKey `
    --vnet-name $Cfg.VnetName --subnet $Cfg.SubnetName `
    --public-ip-address $Cfg.PublicIpName --nsg $Cfg.NsgName `
    --storage-sku $Cfg.OsDiskSku --os-disk-size-gb $Cfg.OsDiskGb --os-disk-name "$($Cfg.VmName)-osdisk" `
    --os-disk-delete-option Delete --nic-delete-option Delete `
    --custom-data $cloudInitFile --tags @tags -o none
Remove-Item $cloudInitFile -ErrorAction SilentlyContinue
Write-Ok 'VM created'

Write-Step 'Enabling auto-shutdown at 02:00 IST (20:30 UTC)'
Invoke-Az vm auto-shutdown -g $Cfg.ResourceGroup -n $Cfg.VmName --time $Cfg.ShutdownUtc -o none
Write-Ok 'Auto-shutdown scheduled daily'

# ---------------------------------------------------------------- 4. verify
Write-Step "Waiting for cloud-init to install Docker and start the app (usually 3-6 minutes)"
try {
    Wait-Http "http://$ip/health" -TimeoutSec 900 | Out-Null
} catch {
    Write-Host 'The app did not come up in time. Last lines of the cloud-init log:' -ForegroundColor Red
    & ssh -i $Cfg.SshKey -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts_cloudtasks" `
        "$($Cfg.AdminUser)@$ip" 'sudo tail -n 40 /var/log/cloud-init-output.log'
    throw
}
Test-Site $ip

Write-Step 'Checking the containers over SSH'
& ssh -i $Cfg.SshKey -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts_cloudtasks" `
    "$($Cfg.AdminUser)@$ip" "cloud-init status; sudo docker compose -f /opt/cloudtasks/docker-compose.yml ps --format 'table {{.Name}}\t{{.Status}}'"

Write-Host "`nCloudTasks is live:  http://$ip/" -ForegroundColor Green
Write-Host "SSH:                 ssh -i $($Cfg.SshKey) $($Cfg.AdminUser)@$ip"
Write-Host 'Pause (stop paying for the VM):  ./scripts/pause.ps1'
