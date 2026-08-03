param(
    [int]$VUs = 20,
    [string]$Duration = "10m"
)

. "$PSScriptRoot\Common.ps1"

Assert-LoadLabPrerequisites
Assert-LoadLabSecrets

$compose = Get-LoadComposeArgs -Mode integration

Write-Host "Waiting for VPN controller readiness..." -ForegroundColor Cyan

$readinessUrl = "http://127.0.0.1:8097/api/v1/readiness"
$vpnReady = $false
$vpnWaitAttempts = 120
$vpnWaitDelaySeconds = 5

for ($attempt = 1; $attempt -le $vpnWaitAttempts; $attempt++) {
    try {
        $readiness = Invoke-RestMethod `
            -Uri $readinessUrl `
            -Method Get `
            -TimeoutSec 10

        if ($readiness.ready -eq $true) {
            $vpnReady = $true

            Write-Host `
                "VPN controller is ready. Available profiles: $($readiness.available_profiles)" `
                -ForegroundColor Green

            break
        }

        Write-Host `
            "VPN controller is not ready: $($readiness.reason) ($attempt/$vpnWaitAttempts)..." `
            -ForegroundColor DarkGray
    }
    catch {
        $statusCode = $null

        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }

        if ($statusCode -eq 503) {
            Write-Host `
                "VPN controller is temporarily unavailable ($attempt/$vpnWaitAttempts)..." `
                -ForegroundColor DarkGray
        }
        else {
            Write-Host `
                "Could not reach VPN readiness endpoint ($attempt/$vpnWaitAttempts): $($_.Exception.Message)" `
                -ForegroundColor DarkGray
        }
    }

    Start-Sleep -Seconds $vpnWaitDelaySeconds
}

if (-not $vpnReady) {
    throw "VPN controller did not become ready within $($vpnWaitAttempts * $vpnWaitDelaySeconds) seconds."
}

Invoke-DockerChecked `
    -Arguments ($compose + @(
        "run", "--rm", "--service-ports",
        "-e", "INTEGRATION_VUS=$VUs",
        "-e", "INTEGRATION_DURATION=$Duration",
        "-e", "SUMMARY_FILE=/results/integration-summary.json",
        "-e", "K6_WEB_DASHBOARD_EXPORT=/results/integration-report.html",
        "k6", "run", "/scripts/integration.js"
    )) `
    -Description "k6 integration test"