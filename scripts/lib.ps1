# Shared by start.ps1 and stop.ps1. Not meant to be run on its own.
# Windows PowerShell 5.1 or PowerShell 7.

$Root = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $Root 'infra\docker-compose.yml'

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Stop-WithError([string]$Message) { Write-Host "error: $Message" -ForegroundColor Red; exit 1 }

function Test-Podman {
    & podman info *> $null
    return $LASTEXITCODE -eq 0
}

# Podman must be reachable. On Windows it runs in a WSL 2 VM ("podman machine").
function Initialize-Podman {
    if (-not (Get-Command podman -ErrorAction SilentlyContinue)) {
        Stop-WithError 'podman is not installed (see docs/05_Environment_Setup_Plan.md, section 6)'
    }
    if (Test-Podman) { return }
    Write-Step 'Starting the Podman machine'
    & podman machine start *> $null
    if (-not (Test-Podman)) { Stop-WithError 'could not start the Podman machine (create one with: podman machine init)' }
}

# `podman compose` when its provider runs; otherwise podman-compose as a Python module through
# uv (Windows Smart App Control can block the podman-compose.exe launcher).
$script:ComposeCmd = $null
function Invoke-Compose {
    if (-not $script:ComposeCmd) {
        & podman compose version *> $null
        if ($LASTEXITCODE -eq 0) {
            $script:ComposeCmd = @('podman', 'compose')
        } elseif (Get-Command uv -ErrorAction SilentlyContinue) {
            $script:ComposeCmd = @('uv', 'run', '--quiet', '--no-project', '--with', 'podman-compose', 'python', '-m', 'podman_compose')
        } else {
            Stop-WithError 'no compose provider: install podman-compose (uv tool install podman-compose)'
        }
    }
    $exe = $script:ComposeCmd[0]
    $rest = @($script:ComposeCmd | Select-Object -Skip 1) + @('-f', $ComposeFile, '--profile', 'app') + $args
    & $exe @rest
}
