<#
Test data in the running stack's database (fictional companies, jobs, candidates; nothing sent).
  scripts\testdata.ps1 load          add the test pipeline (needs an empty database)
  scripts\testdata.ps1 clean         remove ALL business data; keeps staff users and settings
  scripts\testdata.ps1 clean -Yes    same, without asking
  scripts\testdata.ps1 status        row counts
Runs inside the API container, with the API's settings. Start the stack first (start.ps1).
#>
param(
    [Parameter(Position = 0)][ValidateSet('load', 'clean', 'status')][string]$Action,
    [switch]$Yes
)
. (Join-Path $PSScriptRoot 'lib.ps1')

if (-not $Action) { Get-Help $PSCommandPath; exit 2 }
if ($Action -eq 'clean' -and -not $Yes) {
    $answer = Read-Host 'Delete ALL companies, jobs, candidates, approvals and messages? Type yes'
    if ($answer -ne 'yes') { Write-Host 'Nothing deleted.'; exit 1 }
}

Initialize-Podman
$cmd = @('exec', '-T', 'api', 'python', '-m', 'recruiting.testdata', $Action)
if ($Action -eq 'clean') { $cmd += '--yes' }
Invoke-Compose @cmd
exit $LASTEXITCODE
