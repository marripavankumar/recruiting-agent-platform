<#
Start the whole platform on Podman: PostgreSQL, Qdrant, n8n, API, scheduler and web app.
  scripts\start.ps1           start (builds images only if they are missing)
  scripts\start.ps1 -Build    rebuild the images first, e.g. after code changes
If scripts are blocked: powershell -ExecutionPolicy Bypass -File scripts\start.ps1
#>
param([switch]$Build)
. (Join-Path $PSScriptRoot 'lib.ps1')

Initialize-Podman

$envFile = Join-Path $Root '.env'
if (-not (Test-Path $envFile)) {
    Write-Step 'No .env yet: creating it from .env.example (rule-based agents, no API key needed)'
    $lines = Get-Content (Join-Path $Root '.env.example') | ForEach-Object { $_ -replace '^RAP_LLM_PROVIDER=claude', 'RAP_LLM_PROVIDER=none' }
    [IO.File]::WriteAllText($envFile, (($lines -join "`n") + "`n"))  # UTF-8 without BOM
}

Write-Step 'Starting containers'
$upArgs = @('up', '-d')
if ($Build) { $upArgs += '--build' }
Invoke-Compose @upArgs *> $null
if ($LASTEXITCODE -ne 0) { Stop-WithError 'compose up failed; run it by hand to see why' }

function Wait-ForUrl([string]$Name, [string]$Url, [string]$Container) {
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($r.StatusCode -eq 200) { Write-Step "$Name is up: $Url"; return }
        } catch { }
        Start-Sleep -Seconds 2
    }
    Stop-WithError "$Name did not answer at $Url within 2 minutes; see: podman logs recruiting-agent-platform_${Container}_1"
}
Wait-ForUrl 'API' 'http://localhost:8000/health' 'api'
Wait-ForUrl 'Web' 'http://localhost:3000/signin' 'web'

# A fresh database has no users, and the web app's demo sign-in needs these five.
# Only on an empty user table, so real users are never mixed with demo ones.
$users = (Invoke-Compose exec -T postgres psql -U rap -d rap -tAc 'SELECT count(*) FROM app_user' 2> $null | Out-String).Trim()
if ($users -eq '0') {
    Write-Step 'Empty database: adding the five demo staff users'
    $sql = "INSERT INTO app_user (email, display_name, role) VALUES " +
        "('administrator@cbs.example', 'Demo Administrator', 'administrator'), " +
        "('bd.manager@cbs.example', 'Demo BD Manager', 'bd_manager'), " +
        "('recruiter@cbs.example', 'Demo Recruiter', 'recruiter'), " +
        "('account.manager@cbs.example', 'Demo Account Manager', 'account_manager'), " +
        "('sales.manager@cbs.example', 'Demo Sales Manager', 'sales_manager') " +
        "ON CONFLICT (email) DO NOTHING"
    Invoke-Compose exec -T postgres psql -U rap -d rap -qc $sql *> $null
}

Write-Host @'

  Web app   http://localhost:3000
  API docs  http://localhost:8000/docs
  n8n       http://localhost:5678

  Stop with: scripts\stop.ps1
'@
