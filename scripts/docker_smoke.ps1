$ErrorActionPreference = "Stop"

function Wait-ForHealthy {
    $deadline = (Get-Date).AddMinutes(5)
    do {
        try {
            $health = Invoke-RestMethod -Uri $script:HealthUrl -TimeoutSec 5
            if ($health.status -eq "healthy") { return }
        } catch {}
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    throw "Stack did not become healthy"
}

$webPort = if ($env:WEB_PORT) { $env:WEB_PORT } else { "3000" }
$script:HealthUrl = "http://127.0.0.1:$webPort/api/health"
docker compose build --no-cache
docker compose up -d
Wait-ForHealthy

$sentinel = Join-Path $PSScriptRoot "..\data\smoke-persistence-$PID.txt"
$writeTest = Join-Path $PSScriptRoot "..\downloads\.smoke-write-$PID"
try {
    Set-Content -LiteralPath $sentinel -Value "persistent"
    docker compose restart
    Wait-ForHealthy
    if (-not (Test-Path -LiteralPath $sentinel)) {
        throw "Bind-mounted data did not survive restart"
    }

    Set-Content -LiteralPath $writeTest -Value "writable"
    docker compose ps
    Write-Host "PASS: stack healthy after restart, data persistent, downloads writable"
} finally {
    Remove-Item -LiteralPath $sentinel -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $writeTest -Force -ErrorAction SilentlyContinue
}
