<#
Stop the platform. Data stays in the Podman volumes for the next start.
  scripts\stop.ps1            stop and remove the containers
  scripts\stop.ps1 -Machine   also stop the Podman machine (frees its memory)
If scripts are blocked: powershell -ExecutionPolicy Bypass -File scripts\stop.ps1
#>
param([switch]$Machine)
. (Join-Path $PSScriptRoot 'lib.ps1')

if (Test-Podman) {
    Write-Step 'Stopping containers'
    Invoke-Compose down *> $null
} else {
    Write-Step 'Podman is not running: nothing to stop'
}

if ($Machine) {
    & podman machine inspect *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Step 'Stopping the Podman machine'
        & podman machine stop *> $null
    }
}
Write-Step 'Stopped'
