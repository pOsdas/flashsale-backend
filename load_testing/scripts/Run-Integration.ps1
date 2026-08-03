param(
    [int]$VUs = 20,
    [string]$Duration = "10m"
)

. "$PSScriptRoot\Common.ps1"

Assert-LoadLabPrerequisites
Assert-LoadLabSecrets

$compose = Get-LoadComposeArgs -Mode integration

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$catalogPath = Join-Path `
    $projectRoot `
    "load_testing\data\integration-products.json"

$requiredServices = @(
    "postgres",
    "redis",
    "rabbitmq",
    "wb_browser_fetcher",
    "ozon_browser_fetcher",
    "vpn_controller",
    "go_fetcher",
    "backend",
    "monitoring_scanner",
    "outbox_worker",
    "notification_consumer"
)

function Get-RunningComposeServices {
    $output = & docker @(
        $compose + @(
            "ps",
            "--services",
            "--status",
            "running"
        )
    ) 2>&1

    if ($LASTEXITCODE -ne 0) {
        throw "Could not determine running Docker Compose services.`n$output"
    }

    return @(
        $output |
            ForEach-Object { $_.ToString().Trim() } |
            Where-Object { $_ }
    )
}

function Assert-RequiredServicesRunning {
    Write-Host `
        "Checking required integration services..." `
        -ForegroundColor Cyan

    $runningServices = Get-RunningComposeServices

    $missingServices = @(
        $requiredServices |
            Where-Object { $_ -notin $runningServices }
    )

    if ($missingServices.Count -gt 0) {
        $missingList = $missingServices -join ", "

        throw @"
Required integration services are not running: $missingList

Start the Integration Lab first:

.\load_testing\scripts\Start-IntegrationLab.ps1
"@
    }

    Write-Host `
        "All required integration services are running." `
        -ForegroundColor Green
}

function Assert-RequiredServicesHealthy {
    Write-Host `
        "Checking container health..." `
        -ForegroundColor Cyan

    $unhealthyServices = @()

    foreach ($service in $requiredServices) {
        $containerId = & docker @(
            $compose + @(
                "ps",
                "-q",
                $service
            )
        ) 2>$null

        if ($LASTEXITCODE -ne 0 -or -not $containerId) {
            $unhealthyServices += "$service (container not found)"
            continue
        }

        $containerId = $containerId.ToString().Trim()

        $healthStatus = & docker inspect `
            --format `
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}" `
            $containerId `
            2>$null

        if ($LASTEXITCODE -ne 0) {
            $unhealthyServices += "$service (inspect failed)"
            continue
        }

        $healthStatus = $healthStatus.ToString().Trim()

        if ($healthStatus -eq "unhealthy") {
            $unhealthyServices += "$service (unhealthy)"
        }
    }

    if ($unhealthyServices.Count -gt 0) {
        throw (
            "Unhealthy integration services: " +
            ($unhealthyServices -join ", ")
        )
    }

    Write-Host `
        "No unhealthy integration containers found." `
        -ForegroundColor Green
}

function Assert-IntegrationCatalog {
    Write-Host `
        "Checking integration product catalog..." `
        -ForegroundColor Cyan

    if (-not (Test-Path -LiteralPath $catalogPath -PathType Leaf)) {
        throw @"
Integration product catalog was not found:

$catalogPath

Run:

.\load_testing\scripts\Start-IntegrationLab.ps1
"@
    }

    try {
        $catalogRaw = Get-Content `
            -LiteralPath $catalogPath `
            -Raw `
            -Encoding UTF8

        $catalog = $catalogRaw | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw (
            "Integration catalog contains invalid JSON: " +
            $_.Exception.Message
        )
    }

    if (-not $catalogRaw.Trim()) {
        throw "Integration product catalog is empty."
    }

    # Не привязываемся к точной структуре JSON:
    # проверяем все строковые значения сериализованного каталога.
    $catalogJson = $catalog |
        ConvertTo-Json -Depth 100 -Compress

    $hasWildberries = $catalogJson -match `
        "wildberries\.ru|marketplace[`"']?\s*:\s*[`"']?wb"

    $hasOzon = $catalogJson -match `
        "ozon\.ru|marketplace[`"']?\s*:\s*[`"']?ozon"

    if (-not $hasWildberries -and -not $hasOzon) {
        throw @"
Integration catalog does not contain recognizable WB or Ozon products.

Catalog:
$catalogPath

Rebuild the Integration Lab:

.\load_testing\scripts\Start-IntegrationLab.ps1
"@
    }

    if ($hasWildberries) {
        Write-Host "WB test products found." -ForegroundColor Green
    }
    else {
        Write-Warning "No WB test products were found in the catalog."
    }

    if ($hasOzon) {
        Write-Host "Ozon test products found." -ForegroundColor Green
    }
    else {
        Write-Warning "No Ozon test products were found in the catalog."
    }
}

function Assert-BackendAvailable {
    Write-Host `
        "Checking backend availability..." `
        -ForegroundColor Cyan

    $backendUrls = @(
        "http://127.0.0.1:8000/api/v1/health/",
        "http://127.0.0.1:8000/health/",
        "http://127.0.0.1:8000/"
    )

    foreach ($url in $backendUrls) {
        try {
            $response = Invoke-WebRequest `
                -Uri $url `
                -Method Get `
                -TimeoutSec 10 `
                -UseBasicParsing

            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                Write-Host `
                    "Backend is reachable at $url" `
                    -ForegroundColor Green

                return
            }
        }
        catch {
            $statusCode = $null

            if ($_.Exception.Response) {
                $statusCode = [int]$_.Exception.Response.StatusCode
            }

            # Даже 401, 403 или 404 подтверждают, что backend отвечает.
            if (
                $statusCode -in @(
                    400,
                    401,
                    403,
                    404,
                    405
                )
            ) {
                Write-Host `
                    "Backend is reachable at $url (HTTP $statusCode)." `
                    -ForegroundColor Green

                return
            }
        }
    }

    throw "Backend is not reachable at http://127.0.0.1:8000."
}

function Wait-VpnControllerReadiness {
    Write-Host `
        "Waiting for VPN controller readiness..." `
        -ForegroundColor Cyan

    $readinessUrl = "http://127.0.0.1:8097/api/v1/readiness"
    $vpnWaitAttempts = 120
    $vpnWaitDelaySeconds = 5

    for ($attempt = 1; $attempt -le $vpnWaitAttempts; $attempt++) {
        try {
            $readiness = Invoke-RestMethod `
                -Uri $readinessUrl `
                -Method Get `
                -TimeoutSec 10

            $availableProfiles = 0

            if ($null -ne $readiness.available_profiles) {
                $availableProfiles = [int]$readiness.available_profiles
            }

            if (
                $readiness.ready -eq $true -and
                $availableProfiles -gt 0
            ) {
                Write-Host `
                    "VPN controller is ready. Available profiles: $availableProfiles" `
                    -ForegroundColor Green

                return
            }

            Write-Host `
                (
                    "VPN controller is not ready: " +
                    "reason=$($readiness.reason), " +
                    "profiles=$availableProfiles " +
                    "($attempt/$vpnWaitAttempts)"
                ) `
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
                    (
                        "Could not reach VPN readiness endpoint " +
                        "($attempt/$vpnWaitAttempts): " +
                        $_.Exception.Message
                    ) `
                    -ForegroundColor DarkGray
            }
        }

        Start-Sleep -Seconds $vpnWaitDelaySeconds
    }

    throw (
        "VPN controller did not become ready within " +
        "$($vpnWaitAttempts * $vpnWaitDelaySeconds) seconds."
    )
}

Assert-RequiredServicesRunning
Assert-RequiredServicesHealthy
Assert-IntegrationCatalog
Assert-BackendAvailable
Wait-VpnControllerReadiness

Write-Host `
    "Integration environment is ready. Starting k6..." `
    -ForegroundColor Green

Invoke-DockerChecked `
    -Arguments ($compose + @(
        "run",
        "--rm",
        "--service-ports",
        "-e",
        "INTEGRATION_VUS=$VUs",
        "-e",
        "INTEGRATION_DURATION=$Duration",
        "-e",
        "SUMMARY_FILE=/results/integration-summary.json",
        "-e",
        "K6_WEB_DASHBOARD_EXPORT=/results/integration-report.html",
        "k6",
        "run",
        "/scripts/integration.js"
    )) `
    -Description "k6 integration test"