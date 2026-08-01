param(
    [int]$Users = 25,
    [int]$TargetsPerUser = 2,
    [string]$TelegramChatId = "",
    [switch]$ResetData,
    [switch]$SkipCatalogCollection,
    [switch]$StartTelegramBot
)
. "$PSScriptRoot\Common.ps1"
Assert-LoadLabPrerequisites
Assert-LoadLabSecrets

if ($Users -lt 1) { throw "Users must be positive." }
if ($TargetsPerUser -lt 0) { throw "TargetsPerUser cannot be negative." }

$compose = Get-LoadComposeArgs -Mode integration

if ($ResetData) {
    Invoke-DockerChecked `
        -Arguments ($compose + @("down", "--remove-orphans")) `
        -Description "Stopping previous Integration Lab"

    foreach ($volume in @(
        "flashsale_integration_postgres_data",
        "flashsale_integration_prometheus_data",
        "flashsale_integration_loki_data",
        "flashsale_integration_grafana_data"
    )) {
        Remove-DockerVolumeIfExists -Name $volume
    }
}

$bootstrapServices = @(
    "postgres",
    "redis",
    "rabbitmq",
    "load_simulator",
    "ozon_browser_fetcher",
    "wb_browser_fetcher",
    "vpn_controller",
    "go_fetcher",
    "migrate",
    "backend",
    "postgres_exporter",
    "redis_exporter",
    "cadvisor",
    "blackbox_exporter",
    "loki",
    "promtail"
)
Invoke-DockerChecked `
    -Arguments ($compose + @("up", "-d", "--build") + $bootstrapServices) `
    -Description "Starting Integration Lab bootstrap services"

Wait-LoadLabHttp `
    -Url "http://127.0.0.1:8000/api/v1/prometheus/metrics" `
    -Name "backend"
Wait-LoadLabHttp -Url "http://127.0.0.1:8090/health" -Name "Go fetcher"

Write-Host "Waiting for VPN preflight to complete..." -ForegroundColor Cyan

$vpnReady = $false
$vpnWaitAttempts = 120
$vpnWaitDelaySeconds = 5

for ($attempt = 1; $attempt -le $vpnWaitAttempts; $attempt++) {
    $vpnLogs = & docker @(
        $compose + @(
            "logs",
            "--no-color",
            "--since",
            "15m",
            "vpn_controller"
        )
    ) 2>&1

    if ($LASTEXITCODE -ne 0) {
        throw "Could not read vpn_controller logs."
    }

    $completedLine = $vpnLogs |
        Select-String "VPN preflight cycle completed" |
        Select-Object -Last 1

    if ($completedLine) {
        if ($completedLine.Line -match "available_profiles=(\d+)") {
            $availableProfiles = [int]$Matches[1]

            if ($availableProfiles -lt 1) {
                throw "VPN preflight completed, but no available VPN profiles were found."
            }
        }

        $vpnReady = $true
        Write-Host "VPN preflight completed." -ForegroundColor Green
        break
    }

    Write-Host `
        "VPN preflight is still running ($attempt/$vpnWaitAttempts)..." `
        -ForegroundColor DarkGray

    Start-Sleep -Seconds $vpnWaitDelaySeconds
}

if (-not $vpnReady) {
    throw "VPN preflight did not complete within $($vpnWaitAttempts * $vpnWaitDelaySeconds) seconds."
}

if (-not $SkipCatalogCollection) {
    Invoke-DockerChecked `
        -Arguments ($compose + @(
            "exec", "-T", "go_fetcher", "/app/loadcatalog",
            "-output", "/load-data/integration-products.json",
            "-per-marketplace", "25"
        )) `
        -Description "Collecting and validating real marketplace products"
}

$seedArguments = @(
    "exec", "-T", "backend", "python", "manage.py", "prepare_integration_test",
    "--catalog", "/load-data/integration-products.json",
    "--users", "$Users",
    "--targets-per-user", "$TargetsPerUser",
    "--output", "/load-results/users.json",
    "--reset"
)
if ($TelegramChatId.Trim()) {
    $seedArguments += @("--telegram-chat-id", $TelegramChatId.Trim())
}
Invoke-DockerChecked `
    -Arguments ($compose + $seedArguments) `
    -Description "Preparing Integration Lab dataset"

$runtimeServices = @(
    "outbox_worker", "monitoring_scanner", "notification_consumer",
    "prometheus", "grafana"
)
if ($StartTelegramBot) {
    $runtimeServices += "telegram_bot"
}
Invoke-DockerChecked `
    -Arguments ($compose + @("up", "-d", "--build") + $runtimeServices) `
    -Description "Starting Integration Lab runtime services"

Wait-LoadLabHttp -Url "http://127.0.0.1:9090/-/ready" -Name "Prometheus"
Wait-LoadLabHttp -Url "http://127.0.0.1:3000/api/health" -Name "Grafana"

Write-Host ""
Write-Host "Integration Lab is ready." -ForegroundColor Green
Write-Host "Validated catalog: load_testing/data/integration-products.json"
Write-Host "Run test: .\load_testing\scripts\Run-Integration.ps1 -VUs 20 -Duration 10m"
if ($TelegramChatId.Trim()) {
    Write-Host "Send one real notification: .\load_testing\scripts\Trigger-IntegrationNotification.ps1"
}
if (-not $StartTelegramBot) {
    Write-Host "Incoming Telegram polling is intentionally disabled. Use -StartTelegramBot only with a dedicated test bot."
}
