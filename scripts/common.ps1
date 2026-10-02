# Shared settings and helpers for deploy / start / pause / update / status / destroy. Dot-sourced, not run directly.
# Never prints subscription or tenant IDs: every az call below uses --query to pick only the fields it needs.

$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ge 7) { $PSNativeCommandArgumentPassing = 'Legacy' }
# Windows PowerShell 5.1 does not enable TLS 1.2 by default.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

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
    Domain        = 'tasks.devanshlamba.in'    # Cloudflare A record -> the static IP, "DNS only"
    CreditUsd     = 100                        # Azure for Students credit, for the run-out estimate
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
    $days = [math]::Floor($Cfg.CreditUsd / $c.RunningDay)
    Write-Host ("Running 24/7, {0} USD of credit lasts about {1} days (until about {2:d MMM yyyy})." -f $Cfg.CreditUsd, $days, (Get-Date).AddDays($days))
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

function Get-SshArgs {
    @('-i', $Cfg.SshKey, '-o', 'StrictHostKeyChecking=accept-new', '-o', "UserKnownHostsFile=$HOME/.ssh/known_hosts_cloudtasks")
}

# Run a command on the VM. Avoid double quotes inside $Command: Windows PowerShell 5.1 strips them.
function Invoke-Vm([string]$Ip, [string]$Command) {
    $sshArgs = Get-SshArgs
    & ssh @sshArgs "$($Cfg.AdminUser)@$Ip" $Command
    if ($LASTEXITCODE -ne 0) { throw "SSH command failed (exit $LASTEXITCODE)" }
}

function Resolve-Domain {
    try { @([Net.Dns]::GetHostAddresses($Cfg.Domain) | Where-Object AddressFamily -eq 'InterNetwork' | ForEach-Object { $_.ToString() }) }
    catch { @() }
}

# Status code and Location of one request, without following redirects.
function Get-HttpStatus([string]$Url) {
    $req = [Net.HttpWebRequest]::Create($Url)
    $req.AllowAutoRedirect = $false
    $req.Timeout = 10000
    try { $res = $req.GetResponse() }
    catch [Net.WebException] { $res = $_.Exception.Response; if (-not $res) { throw } }
    try { [pscustomobject]@{ Code = [int]$res.StatusCode; Location = $res.Headers['Location'] } }
    finally { $res.Close() }
}

# The certificate the server presents, and whether Windows considers it valid for the name.
function Get-CertInfo([string]$HostName) {
    $script:certPolicyErrors = 'unknown'
    $tcp = New-Object Net.Sockets.TcpClient
    $tcp.Connect($HostName, 443)
    try {
        $cb = [Net.Security.RemoteCertificateValidationCallback] { param($s, $c, $ch, $e) $script:certPolicyErrors = "$e"; $true }
        $ssl = New-Object Net.Security.SslStream($tcp.GetStream(), $false, $cb)
        $ssl.AuthenticateAsClient($HostName)
        $cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
        [pscustomobject]@{
            Name     = $cert.GetNameInfo('DnsName', $false)
            Issuer   = $cert.GetNameInfo('SimpleName', $true)
            NotAfter = $cert.NotAfter
            DaysLeft = [int][math]::Floor(($cert.NotAfter - (Get-Date)).TotalDays)
            Valid    = ($script:certPolicyErrors -eq 'None')
        }
    } finally { $tcp.Close() }
}

# Verify the live site from this laptop: valid HTTPS, redirect, login wall, health.
function Test-Site([string]$Ip) {
    $d = $Cfg.Domain
    $resolved = Resolve-Domain
    if ($resolved -notcontains $Ip) {
        throw "$d does not resolve to $Ip yet (got: $($resolved -join ', ')). Add the Cloudflare A record (tasks -> $Ip, DNS only) and wait a minute."
    }
    Write-Ok "$d resolves to $Ip"
    $cert = Get-CertInfo $d
    if (-not $cert.Valid) { throw "The certificate for $d is not valid yet (issuer: $($cert.Issuer)). Caddy may still be getting it; try again in a minute." }
    Write-Ok ("Valid certificate for {0} from {1}, expires {2:d MMM yyyy} ({3} days left)" -f $cert.Name, $cert.Issuer, $cert.NotAfter, $cert.DaysLeft)
    $redir = Get-HttpStatus "http://$d/health"
    if ($redir.Code -notin 301, 308 -or $redir.Location -notlike "https://$d/*") { throw "HTTP does not redirect to HTTPS (got $($redir.Code) $($redir.Location))" }
    Write-Ok "http://$d redirects to HTTPS ($($redir.Code))"
    $page = Invoke-WebRequest -Uri "https://$d/login" -UseBasicParsing -TimeoutSec 10
    if ($page.StatusCode -ne 200 -or $page.Content -notmatch 'CloudTasks') { throw "Login page check failed ($($page.StatusCode))" }
    Write-Ok "https://$d/login returns the sign-in page"
    $api = Get-HttpStatus "https://$d/api/tasks"
    if ($api.Code -ne 401) { throw "/api/tasks without a session returned $($api.Code), expected 401" }
    Write-Ok "https://$d/api/tasks without a session -> 401"
    $health = Invoke-RestMethod -Uri "https://$d/health" -TimeoutSec 10
    if ($health.status -ne 'ok') { throw "/health says: $($health.status)" }
    Write-Ok "https://$d/health -> status=$($health.status)"
}
