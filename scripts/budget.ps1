<#
.SYNOPSIS
  Create (or update) a monthly cost budget with e-mail alerts in Azure Cost Management.

.DESCRIPTION
  Budget "cloudtasks-budget" on the subscription: <Amount> USD per month, e-mails at 50% and 100%
  of actual cost and at 100% of forecast cost. The e-mail address is a parameter on purpose, so
  it is never stored in this public repository. Subscription IDs are filled in by `az rest` and
  never printed. Some offers (e.g. certain student/sponsorship subscriptions) don't support
  budgets; then the script prints the portal steps instead.

.EXAMPLE
  ./scripts/budget.ps1 -Email you@example.com            # 10 USD (default)
  ./scripts/budget.ps1 -Email you@example.com -Amount 15
#>
param(
    [Parameter(Mandatory = $true)][string]$Email,
    [int]$Amount = 10
)
. "$PSScriptRoot\common.ps1"

if ($Email -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { throw 'That does not look like an e-mail address.' }
Assert-LoggedIn

$start = (Get-Date -Day 1).ToString('yyyy-MM-01T00:00:00Z')
$end = (Get-Date -Day 1).AddYears(1).ToString('yyyy-MM-01T00:00:00Z')
function Alert([int]$Threshold, [string]$Type) {
    @{ enabled = $true; operator = 'GreaterThanOrEqualTo'; threshold = $Threshold; thresholdType = $Type; contactEmails = @($Email) }
}
$body = @{
    properties = @{
        category      = 'Cost'
        amount        = $Amount
        timeGrain     = 'Monthly'
        timePeriod    = @{ startDate = $start; endDate = $end }
        notifications = @{
            actual50    = Alert 50 'Actual'
            actual100   = Alert 100 'Actual'
            forecast100 = Alert 100 'Forecasted'
        }
    }
} | ConvertTo-Json -Depth 6

$bodyFile = Join-Path ([IO.Path]::GetTempPath()) 'cloudtasks-budget.json'
[IO.File]::WriteAllText($bodyFile, $body)
$url = 'https://management.azure.com/subscriptions/{subscriptionId}/providers/Microsoft.Consumption/budgets/cloudtasks-budget'

Write-Step "Creating budget: $Amount USD/month, alerts at 50% and 100% (actual) and 100% (forecast)"
$out = & az rest --method put --url $url --uri-parameters 'api-version=2023-11-01' --body "@$bodyFile" `
    --query '{name:name, amount:properties.amount, timeGrain:properties.timeGrain}' -o json 2>&1
$code = $LASTEXITCODE
Remove-Item $bodyFile -ErrorAction SilentlyContinue
# Never echo raw errors: they can contain subscription IDs.
$text = ($out | Out-String) -replace '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', '<id>'
if ($code -eq 0) {
    Write-Ok "Budget created: $(($text -replace '\s+', ' ').Trim())"
    Write-Info 'Azure checks costs a few times a day; alerts arrive by e-mail from Microsoft Azure.'
} else {
    Write-Host "Could not create the budget with the API:" -ForegroundColor Yellow
    Write-Host ($text.Trim() -split "`n" | Select-Object -First 4 | Out-String)
    Write-Host @"
Create it in the portal instead (2 minutes):
  1. Portal -> Cost Management -> Budgets -> + Add  (scope: your Azure for Students subscription)
  2. Name: cloudtasks-budget, Reset period: Monthly, Amount: $Amount
  3. Alert conditions: Actual 50%, Actual 100%, Forecasted 100%
  4. Alert recipients: your e-mail address -> Create
"@
    exit 1
}
