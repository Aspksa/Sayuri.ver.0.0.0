<#
    Лаунчер Sayuri Yukishiro.

    Задачи:
      1. определить корень проекта без привязки к букве диска;
      2. найти Python (портативный, виртуальное окружение или системный);
      3. обеспечить записываемое хранилище даже на носителе только для чтения;
      4. выполнить диагностику;
      5. поднять ядро и открыть сайт.
#>

[CmdletBinding()]
param(
    [switch]$PreflightOnly,
    [switch]$SkipPreflight,
    [switch]$NoBrowser,
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"

try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
} catch {
    # Старая консоль без поддержки UTF-8: продолжаем, текст может быть искажён.
}

$Root = Split-Path -Parent $PSScriptRoot
$SourceRoot = Join-Path $Root "src"
$env:PYTHONPATH = $SourceRoot
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$VersionFile = Join-Path $Root "VERSION"
if (Test-Path -LiteralPath $VersionFile) {
    $ProjectVersion = (Get-Content -LiteralPath $VersionFile -Raw).Trim()
} else {
    $ProjectVersion = "unknown"
}

function Write-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host $Text -ForegroundColor Cyan
}

function Resolve-SayuriPython {
    # Порядок намеренный: сначала то, что лежит на самом носителе.
    $portable = Join-Path $Root "runtime\python\python.exe"
    if (Test-Path -LiteralPath $portable) {
        return @{ Exe = $portable; Prefix = @(); Source = "портативный runtime" }
    }

    $venv = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venv) {
        return @{ Exe = $venv; Prefix = @(); Source = "виртуальное окружение" }
    }

    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($python) {
        return @{ Exe = $python.Source; Prefix = @(); Source = "системный Python" }
    }

    $py = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($py) {
        return @{ Exe = $py.Source; Prefix = @("-3"); Source = "лаунчер py" }
    }

    throw "Python 3.11+ не найден. Положите портативный Python в runtime\python или установите Python."
}

function Ensure-WritableData {
    <#
        Носитель может быть защищён от записи (CD, флешка с переключателем,
        сетевая папка без прав). Тогда данные уходят в профиль пользователя,
        а проект остаётся запускаемым.
    #>
    if ($env:SAYURI_DATA_DIR) {
        return
    }

    $dataDir = Join-Path $Root "data"
    try {
        if (-not (Test-Path -LiteralPath $dataDir)) {
            New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
        }
        $probe = Join-Path $dataDir ".launcher-write-test"
        Set-Content -LiteralPath $probe -Value "ok" -Encoding utf8 -NoNewline
        Remove-Item -LiteralPath $probe -Force
    } catch {
        $fallbackBase = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $env:TEMP }
        $fallback = Join-Path $fallbackBase "SayuriYukishiro\data"
        New-Item -ItemType Directory -Path $fallback -Force | Out-Null
        $env:SAYURI_DATA_DIR = $fallback
        Write-Host "[ИНФО] Носитель недоступен для записи. Данные: $fallback" -ForegroundColor Yellow
    }
}

function Invoke-Sayuri {
    param([string[]]$Arguments)

    $callArgs = @($Python.Prefix) + @("-m", "sayuri_yukishiro.main") + $Arguments
    & $Python.Exe @callArgs
    return $LASTEXITCODE
}

function Get-RunningEndpoint {
    $output = & $Python.Exe @($Python.Prefix + @("-m", "sayuri_yukishiro.main", "--endpoint")) 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    try {
        return ($output | Out-String | ConvertFrom-Json)
    } catch {
        return $null
    }
}

# --- запуск -----------------------------------------------------------

Write-Host "Sayuri Yukishiro v$ProjectVersion" -ForegroundColor White
Write-Host "Корень: $Root" -ForegroundColor DarkGray

$Python = Resolve-SayuriPython
Write-Host "Python: $($Python.Exe) ($($Python.Source))" -ForegroundColor DarkGray

Ensure-WritableData

$existing = Get-RunningEndpoint
if ($existing -and $existing.running) {
    Write-Host ""
    Write-Host "Система уже запущена: $($existing.url) (pid $($existing.pid))" -ForegroundColor Yellow
    if (-not $NoBrowser) {
        Start-Process $existing.url | Out-Null
        Write-Host "Сайт открыт в браузере." -ForegroundColor Green
    }
    exit 0
}

if (-not $SkipPreflight) {
    Write-Section "Проверка готовности"
    $preflightArgs = @("--preflight")
    if ($Port -gt 0) { $preflightArgs += @("--port", "$Port") }
    $code = Invoke-Sayuri -Arguments $preflightArgs
    if ($code -ne 0) {
        Write-Host ""
        Write-Host "Запуск невозможен: диагностика нашла критические проблемы." -ForegroundColor Red
        exit $code
    }
}

if ($PreflightOnly) {
    exit 0
}

Write-Section "Запуск ядра"
$serveArgs = @("--serve")
if ($Port -gt 0) { $serveArgs += @("--port", "$Port") }
if ($NoBrowser) { $serveArgs += "--no-browser" }

$code = Invoke-Sayuri -Arguments $serveArgs
exit $code
