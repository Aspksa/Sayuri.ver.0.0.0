<#
    Лаунчер Sayuri Yukishiro.

    Задачи:
      1. определить корень проекта без привязки к букве диска;
      2. найти Python (портативный, виртуальное окружение или системный);
      3. обеспечить записываемое хранилище даже на носителе только для чтения;
      4. показать понятные статусы готовности;
      5. поднять ядро и открыть сайт.

    Статусы рисует сам лаунчер по JSON от диагностики: родные цвета
    PowerShell надёжнее ANSI в старом conhost. Если консоль не умеет
    Unicode, значки деградируют до ASCII.
#>

[CmdletBinding()]
param(
    [switch]$PreflightOnly,
    [switch]$SkipPreflight,
    [switch]$NoBrowser,
    [switch]$Ascii,
    [switch]$NoTray,
    [switch]$Tray,
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"

# --- визуальный язык --------------------------------------------------

$script:UseUnicode = $false
if (-not $Ascii -and -not $env:SAYURI_ASCII) {
    try {
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
        $script:UseUnicode = ([Console]::OutputEncoding.CodePage -eq 65001)
    } catch {
        $script:UseUnicode = $false
    }
}

if ($script:UseUnicode) {
    $script:Glyph = @{ ok = "✓"; warn = "!"; bad = "✗"; info = "·"; step = "▸" }
    $script:Box = @{ tl = "╭"; tr = "╮"; bl = "╰"; br = "╯"; h = "─"; v = "│" }
} else {
    $script:Glyph = @{ ok = "+"; warn = "!"; bad = "x"; info = "."; step = ">" }
    $script:Box = @{ tl = "+"; tr = "+"; bl = "+"; br = "+"; h = "-"; v = "|" }
}

$script:Color = @{
    ok    = "Green"
    warn  = "Yellow"
    bad   = "Red"
    info  = "DarkGray"
    step  = "Cyan"
    title = "White"
}

function Get-StatusColor {
    param([string]$Kind)
    if ($script:Color.ContainsKey($Kind)) { return $script:Color[$Kind] }
    return $script:Color["info"]
}

function Write-Status {
    param(
        [ValidateSet("ok", "warn", "bad", "info", "step")]
        [string]$Kind,
        [string]$Name,
        [string]$Detail = "",
        [int]$Width = 13
    )

    $mark = "[" + $script:Glyph[$Kind] + "]"
    Write-Host "  " -NoNewline
    Write-Host $mark -ForegroundColor (Get-StatusColor $Kind) -NoNewline
    Write-Host (" " + $Name.PadRight($Width)) -NoNewline
    if ($Detail) {
        Write-Host (" " + $Detail) -ForegroundColor Gray
    } else {
        Write-Host ""
    }
}

function Write-Field {
    param([string]$Name, [string]$Value, [int]$Width = 10)

    Write-Host "  " -NoNewline
    Write-Host $script:Glyph["step"] -ForegroundColor $script:Color["step"] -NoNewline
    Write-Host (" " + $Name.PadRight($Width)) -NoNewline
    Write-Host (" " + $Value) -ForegroundColor Gray
}

function Write-Step {
    param([int]$Index, [int]$Total, [string]$Title)

    Write-Host ""
    Write-Host "  [$Index/$Total]" -ForegroundColor $script:Color["step"] -NoNewline
    Write-Host (" " + $Title) -ForegroundColor $script:Color["title"]
}

function Write-Rule {
    param([int]$Width = 62)
    Write-Host ("  " + ($script:Box["h"] * $Width)) -ForegroundColor $script:Color["info"]
}

function Write-Panel {
    param(
        [string[]]$Lines,
        [ValidateSet("ok", "warn", "bad", "info", "step")]
        [string]$Kind = "ok"
    )

    $width = 28
    foreach ($line in $Lines) {
        if ($line.Length -gt $width) { $width = $line.Length }
    }
    $colour = Get-StatusColor $Kind
    $border = $script:Box["h"] * ($width + 2)

    Write-Host ("  " + $script:Box["tl"] + $border + $script:Box["tr"]) -ForegroundColor $colour
    foreach ($line in $Lines) {
        Write-Host ("  " + $script:Box["v"] + " ") -ForegroundColor $colour -NoNewline
        Write-Host $line.PadRight($width) -NoNewline
        Write-Host (" " + $script:Box["v"]) -ForegroundColor $colour
    }
    Write-Host ("  " + $script:Box["bl"] + $border + $script:Box["br"]) -ForegroundColor $colour
}

function Get-Plural {
    param([int]$Count, [string]$One, [string]$Few, [string]$Many)

    $hundred = [Math]::Abs($Count) % 100
    $ten = [Math]::Abs($Count) % 10
    if ($hundred -ge 11 -and $hundred -le 14) { return "$Count $Many" }
    if ($ten -eq 1) { return "$Count $One" }
    if ($ten -ge 2 -and $ten -le 4) { return "$Count $Few" }
    return "$Count $Many"
}

# --- окружение --------------------------------------------------------

# $IsWindows есть только в PowerShell 7+; в 5.1 его нет вовсе.
$IsWindowsHost = if ($null -ne $PSVersionTable.Platform) {
    $PSVersionTable.Platform -eq "Win32NT"
} else {
    $true
}

$Root = Split-Path -Parent $PSScriptRoot
$SourceRoot = Join-Path $Root "src"
$env:PYTHONPATH = $SourceRoot
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
if ($Ascii) { $env:SAYURI_ASCII = "1" }

$VersionFile = Join-Path $Root "VERSION"
if (Test-Path -LiteralPath $VersionFile) {
    $ProjectVersion = (Get-Content -LiteralPath $VersionFile -Raw).Trim()
} else {
    $ProjectVersion = "unknown"
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
    # Reason различает три случая: путь задал пользователь, путь на
    # носителе, путь вынесен из-за защиты от записи. Иначе лаунчер
    # сообщал бы о недоступном носителе там, где его просто попросили
    # хранить данные в другом месте.
    if ($env:SAYURI_DATA_DIR) {
        return @{ Path = $env:SAYURI_DATA_DIR; Reason = "explicit" }
    }

    $dataDir = Join-Path $Root "data"
    try {
        if (-not (Test-Path -LiteralPath $dataDir)) {
            New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
        }
        $probe = Join-Path $dataDir ".launcher-write-test"
        Set-Content -LiteralPath $probe -Value "ok" -Encoding utf8 -NoNewline
        Remove-Item -LiteralPath $probe -Force
        return @{ Path = $dataDir; Reason = "media" }
    } catch {
        $fallbackBase = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $env:TEMP }
        $fallback = Join-Path $fallbackBase "SayuriYukishiro\data"
        New-Item -ItemType Directory -Path $fallback -Force | Out-Null
        $env:SAYURI_DATA_DIR = $fallback
        return @{ Path = $fallback; Reason = "fallback" }
    }
}

function Invoke-Sayuri {
    param([string[]]$Arguments)

    $callArgs = @($Python.Prefix) + @("-m", "sayuri_yukishiro.main") + $Arguments
    & $Python.Exe @callArgs
    return $LASTEXITCODE
}

function Invoke-SayuriJson {
    param([string[]]$Arguments)

    $callArgs = @($Python.Prefix) + @("-m", "sayuri_yukishiro.main") + $Arguments
    $raw = & $Python.Exe @callArgs 2>$null
    $code = $LASTEXITCODE
    $parsed = $null
    if ($raw) {
        try { $parsed = ($raw | Out-String | ConvertFrom-Json) } catch { $parsed = $null }
    }
    return @{ Data = $parsed; ExitCode = $code }
}

function Test-WindowsForms {
    try {
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Wait-ForEndpoint {
    <#
        Ядро само записывает фактический адрес в endpoint.json. Ждём именно
        запись от нашего процесса: чужой файл от прошлого запуска не подойдёт.
    #>
    param([int]$ExpectedPid, [int]$TimeoutSeconds = 60)

    $endpointPath = Join-Path $Root "data\runtime\endpoint.json"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $endpointPath) {
            try {
                $data = Get-Content -LiteralPath $endpointPath -Raw | ConvertFrom-Json
                if ($data.pid -eq $ExpectedPid) { return $data }
            } catch {
                # Файл пишется атомарно, но читатель может успеть между
                # созданием и заменой: просто пробуем снова.
            }
        }
        $process = Get-Process -Id $ExpectedPid -ErrorAction SilentlyContinue
        if (-not $process) { return $null }
        Start-Sleep -Milliseconds 300
    }
    return $null
}

function Get-PythonVersion {
    try {
        $raw = & $Python.Exe @($Python.Prefix + @("-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"))
        return ($raw | Out-String).Trim()
    } catch {
        return "неизвестно"
    }
}

# --- запуск -----------------------------------------------------------

Write-Host ""
Write-Panel -Kind "step" -Lines @("Sayuri Yukishiro  v$ProjectVersion")

$Python = Resolve-SayuriPython
$storage = Ensure-WritableData

Write-Host ""
Write-Field "Корень" $Root
Write-Field "Python" ("{0}  ·  {1}" -f (Get-PythonVersion), $Python.Source)
switch ($storage.Reason) {
    "explicit" { Write-Field "Данные" ("{0}  ·  задано вручную" -f $storage.Path) }
    "fallback" { Write-Field "Данные" ("{0}  ·  вынесено с носителя" -f $storage.Path) }
    default    { Write-Field "Данные" $storage.Path }
}
if ($storage.Reason -eq "fallback") {
    Write-Status -Kind "warn" -Name "носитель" -Detail "защищён от записи — данные вынесены в профиль"
}

$totalSteps = if ($PreflightOnly) { 1 } else { 2 }
$step = 0

if (-not $SkipPreflight) {
    $step++
    Write-Step $step $totalSteps "Проверка готовности"

    $preflightArgs = @("--preflight", "--format", "json")
    if ($Port -gt 0) { $preflightArgs += @("--port", "$Port") }
    $result = Invoke-SayuriJson -Arguments $preflightArgs

    if ($null -eq $result.Data) {
        Write-Status -Kind "bad" -Name "диагностика" -Detail "не удалось получить отчёт"
        Write-Host ""
        Write-Panel -Kind "bad" -Lines @("Запуск невозможен", "диагностика не отвечает")
        exit 1
    }

    $width = 13
    foreach ($check in $result.Data.checks) {
        if ($check.name.Length -ge $width) { $width = $check.name.Length + 1 }
    }
    foreach ($check in $result.Data.checks) {
        Write-Status -Kind $check.kind -Name $check.name -Detail $check.detail -Width $width
    }

    $summary = $result.Data.summary
    Write-Rule
    Write-Host "  " -NoNewline
    Write-Host (Get-Plural $summary.passed "проверка" "проверки" "проверок") -NoNewline
    Write-Host " пройдено" -NoNewline
    if ($summary.warnings -gt 0) {
        Write-Host " · " -ForegroundColor DarkGray -NoNewline
        Write-Host (Get-Plural $summary.warnings "замечание" "замечания" "замечаний") `
            -ForegroundColor $script:Color["warn"] -NoNewline
    }
    if ($summary.fatal -gt 0) {
        Write-Host " · " -ForegroundColor DarkGray -NoNewline
        Write-Host (Get-Plural $summary.fatal "критическая ошибка" "критические ошибки" "критических ошибок") `
            -ForegroundColor $script:Color["bad"] -NoNewline
    }
    Write-Host ""

    if ($result.Data.fatal) {
        Write-Host ""
        Write-Panel -Kind "bad" -Lines @(
            "Запуск невозможен",
            "исправьте строки со значком " + $script:Glyph["bad"]
        )
        exit 1
    }
}

if ($PreflightOnly) {
    Write-Host ""
    Write-Panel -Kind "ok" -Lines @("Система готова к запуску")
    exit 0
}

# Уже запущенный экземпляр: открываем его сайт, второй не поднимаем.
$existing = Invoke-SayuriJson -Arguments @("--endpoint")
if ($existing.ExitCode -eq 0 -and $existing.Data -and $existing.Data.running) {
    Write-Host ""
    Write-Status -Kind "ok" -Name "экземпляр" -Detail "уже запущен, pid $($existing.Data.pid)"
    if (-not $NoBrowser) {
        Start-Process $existing.Data.url | Out-Null
        Write-Status -Kind "ok" -Name "сайт" -Detail "открыт в браузере"
    }
    Write-Host ""
    Write-Panel -Kind "ok" -Lines @("Система активна", $existing.Data.url)
    exit 0
}

$step++
Write-Step $step $totalSteps "Запуск ядра"

# Режим трея — по умолчанию на Windows с доступными Windows Forms.
# -NoTray возвращает поведение «ядро в этом окне».
$trayScript = Join-Path $PSScriptRoot "tray.ps1"
$useTray = $false
if (-not $NoTray -and (Test-Path -LiteralPath $trayScript)) {
    if ($Tray) {
        $useTray = $true
    } elseif ($IsWindowsHost) {
        $useTray = $true
    }
}
if ($useTray -and -not (Test-WindowsForms)) {
    Write-Status -Kind "warn" -Name "трей" -Detail "Windows Forms недоступны — ядро останется в этом окне"
    $useTray = $false
}

$serveArgs = @("--serve")
if ($Port -gt 0) { $serveArgs += @("--port", "$Port") }
if ($NoBrowser) { $serveArgs += "--no-browser" }

if (-not $useTray) {
    $code = Invoke-Sayuri -Arguments $serveArgs

    Write-Host ""
    if ($code -eq 0) {
        Write-Status -Kind "info" -Name "ядро" -Detail "остановлено"
    } else {
        Write-Status -Kind "bad" -Name "ядро" -Detail "завершилось с кодом $code"
    }
    exit $code
}

# Ядро уходит в отдельный скрытый процесс: окно консоли можно свернуть,
# а ядро продолжит работать и переживёт сворачивание.
$coreArgs = @($Python.Prefix) + @("-m", "sayuri_yukishiro.main") + $serveArgs
$core = Start-Process -FilePath $Python.Exe -ArgumentList $coreArgs `
    -WorkingDirectory $Root -WindowStyle Hidden -PassThru

$endpoint = Wait-ForEndpoint -ExpectedPid $core.Id -TimeoutSeconds 60
if (-not $endpoint) {
    Write-Status -Kind "bad" -Name "ядро" -Detail "не поднялось за 60 секунд"
    if (-not $core.HasExited) { $core.Kill() }
    Write-Host ""
    Write-Panel -Kind "bad" -Lines @("Запуск не удался", "ядро не сообщило адрес")
    exit 1
}

Write-Status -Kind "ok" -Name "ядро" -Detail "запущено, pid $($core.Id)"
Write-Status -Kind "ok" -Name "сайт" -Detail $endpoint.url
if (-not $NoBrowser) {
    Start-Process $endpoint.url | Out-Null
    Write-Status -Kind "ok" -Name "браузер" -Detail "сайт открыт"
}

Write-Host ""
Write-Panel -Kind "ok" -Lines @(
    "Система активна",
    $endpoint.url,
    "Значок у часов: меню и выход"
)
Write-Host ""
Write-Status -Kind "info" -Name "окно" -Detail "сворачивается в область уведомлений"

$trayParams = @{
    Root         = $Root
    PythonExe    = $Python.Exe
    PythonPrefix = $Python.Prefix
    CorePid      = $core.Id
    PollSeconds  = 5
}
& $trayScript @trayParams
$trayCode = $LASTEXITCODE

if ($trayCode -eq 2) {
    # Трей не поднялся: не оставляем ядро без присмотра в скрытом процессе.
    Write-Status -Kind "warn" -Name "трей" -Detail "не запустился — ядро остановлено"
    $existing = Invoke-SayuriJson -Arguments @("--endpoint")
    if ($existing.Data -and $existing.Data.running -and -not $core.HasExited) {
        $core.Kill()
    }
    exit 2
}

Write-Host ""
Write-Status -Kind "info" -Name "ядро" -Detail "остановлено"
exit 0
