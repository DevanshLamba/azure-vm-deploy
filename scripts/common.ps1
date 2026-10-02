# Shared settings and helpers for deploy / pause / start / destroy. Dot-sourced, not run directly.
# Never prints subscription or tenant IDs: every az call below uses --query to pick only the fields it needs.

$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ge 7) { $PSNativeCommandArgumentPassing = 'Legacy' }

$Cfg = [ordered]@{
    ResourceGroup = 'rg-cloudtasks'
    Region        = 'eastasia'                 # see "Region choice" in docs/report.md
    VmName        = 'vm-cloudtasks'
    VmSize        = 'Standard_B2pts_v2'        # 2 vCPU (Arm64) / 1 GiB, cheapest size allowed for this subscription
    Image         = 'Canonical:ubuntu-24_04-lts:server-arm64:latest'
    AdminUser     = 'azureuser'
    OsDiskSku     = 'StandardSSD_LRS'          # billed as E4 (32 GiB)
    OsDiskGb      = 30
    VnetName      = 'vnet-cloudtasks'
    SubnetName    = 'snet-cloudtasks'
    NsgName       = 'nsg-cloudtasks'
    PublicIpName  = 'pip-cloudtasks'
    ShutdownUtc   = '2030'                     # 20:30 UTC = 02:00 IST
    RepoUrl       = 'https://github.com/devanshlamba/azure-vm-deploy.git'
    SshKey        = Join-Path $HOME '.ssh\cloudtasks_azure_ed25519'
}

function Write-Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text)   { Write-Host "    OK  $Text" -ForegroundColor Green }
function Write-Info([string]$Text) { Write-Host "    $Text" }

# Run az, return its stdout, throw with a readable message if it fails.
function Invoke-Az {
    $out = & az @args
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI failed: az $($args[0]) $($args[1]) (exit $LASTEXITCODE)" }
    return $out
}

function Assert-LoggedIn {
    $state = & az account show --query state -o tsv 2>$null
    if ($LASTEXITCODE -ne 0 -or $state -ne 'Enabled') { throw 'Not logged in to Azure. Run: az login' }
    $name = Invoke-Az account show --query name -o tsv
    Write-Ok "Logged in, subscription: $name"
}

function Test-ResourceGroup { (Invoke-Az group exists -n $Cfg.ResourceGroup) -eq 'true' }

function Get-PublicIp {
    Invoke-Az network public-ip show -g $Cfg.ResourceGroup -n $Cfg.PublicIpName --query ipAddress -o tsv
}

function Get-PowerState {
    Invoke-Az vm get-instance-view -g $Cfg.ResourceGroup -n $Cfg.VmName `
        --query "instanceView.statuses[?starts_with(code,'PowerState/')].displayStatus | [0]" -o tsv
}

# Live pay-as-you-go prices from the public Azure Retail Prices API (no login needed).
function Get-RetailPrice([string]$Filter) {
    $uri = 'https://prices.azure.com/api/retail/prices?$filter=' + [uri]::EscapeDataString($Filter)
    $items = (Invoke-RestMethod -Uri $uri -TimeoutSec 20).Items | Where-Object { $_.type -eq 'Consumption' }
    if (-not $items) { throw "No price found for: $Filter" }
    return [double]($items | Select-Object -First 1).retailPrice
}

function Get-CostModel {
    $r = $Cfg.Region
    # Linux pay-as-you-go row: not Windows, not Spot / Low Priority, not Cloud Services.
    $uri = 'https://prices.azure.com/api/retail/prices?$filter=' + [uri]::EscapeDataString(
        "armRegionName eq '$r' and armSkuName eq '$($Cfg.VmSize)' and serviceName eq 'Virtual Machines'")
    $vm = (Invoke-RestMethod -Uri $uri -TimeoutSec 20).Items | Where-Object {
        $_.type -eq 'Consumption' -and $_.productName -like 'Virtual Machines*' -and $_.productName -notlike '*Windows*' -and
        $_.skuName -notlike '*Spot*' -and $_.skuName -notlike '*Low Priority*' } | Select-Object -First 1
    if (-not $vm) { throw "No Linux price found for $($Cfg.VmSize) in $r" }
    $vmHour = [double]$vm.retailPrice
    $ipHour = Get-RetailPrice "armRegionName eq '$r' and productName eq 'IP Addresses' and meterName eq 'Standard IPv4 Static Public IP'"
    $diskMonth = Get-RetailPrice "armRegionName eq '$r' and productName eq 'Standard SSD Managed Disks' and meterName eq 'E4 LRS Disk'"
    $diskDay = $diskMonth / 730 * 24
    [pscustomobject]@{
        VmHour     = $vmHour
        IpHour     = $ipHour
        DiskMonth  = $diskMonth
        RunningDay = $vmHour * 24 + $ipHour * 24 + $diskDay
        PausedDay  = $ipHour * 24 + $diskDay
        VmDay      = $vmHour * 24
        IpDay      = $ipHour * 24
        DiskDay    = $diskDay
    }
}

function Show-CostTable($c) {
    "{0,-34} {1,10} {2,12}" -f 'State', 'USD / day', 'USD / month' | Write-Host
    "{0,-34} {1,10:N3} {2,12:N2}" -f 'Running (VM + disk + IP)', $c.RunningDay, ($c.RunningDay * 30.4) | Write-Host
    "{0,-34} {1,10:N3} {2,12:N2}" -f 'Paused / deallocated (disk + IP)', $c.PausedDay, ($c.PausedDay * 30.4) | Write-Host
    "{0,-34} {1,10:N3} {2,12:N2}" -f 'Destroyed (resource group deleted)', 0, 0 | Write-Host
}

# Poll an URL from this laptop until it answers 200 (or time runs out).
function Wait-Http([string]$Url, [int]$TimeoutSec = 900) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { Write-Host ''; return $r }
        } catch { }
        Write-Host '.' -NoNewline
        Start-Sleep -Seconds 10
    }
    Write-Host ''
    throw "Timed out waiting for $Url"
}

function Test-Site([string]$Ip) {
    $home_ = Invoke-WebRequest -Uri "http://$Ip/" -UseBasicParsing -TimeoutSec 10
    if ($home_.StatusCode -ne 200 -or $home_.Content -notmatch 'CloudTasks') { throw "Home page check failed ($($home_.StatusCode))" }
    Write-Ok "http://$Ip/ returns 200 and the CloudTasks page"
    $health = Invoke-RestMethod -Uri "http://$Ip/health" -TimeoutSec 10
    if ($health.status -ne 'ok') { throw "/health says: $($health.status)" }
    Write-Ok "http://$Ip/health -> status=$($health.status), database=$($health.database)"
}
