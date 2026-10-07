<#
    Иконка Sayuri Yukishiro в области уведомлений Windows (у часов).

    Хост берёт на себя:
      1. значок с меню и открытием сайта по двойному клику;
      2. сворачивание окна консоли и возврат его из меню;
      3. опрос состояния системы с обновлением подсказки;
      4. мягкую остановку ядра по токену, Kill — только как fallback;
      5. перезапуск ядра.

    Запускается лаунчером; ядро к этому моменту уже работает отдельным
    процессом, а его адрес и токен лежат в data/runtime/endpoint.json.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [string[]]$PythonPrefix = @(),
    [int]$CorePid = 0,
    [int]$PollSeconds = 5,
    [switch]$KeepConsole
)

$ErrorActionPreference = "Stop"

try {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
} catch {
    Write-Warning "Windows Forms недоступны — режим трея невозможен: $($_.Exception.Message)"
    exit 2
}

Add-Type -Namespace Sayuri -Name Win32 -MemberDefinition @'
    [DllImport("kernel32.dll")]
    public static extern System.IntPtr GetConsoleWindow();

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(System.IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(System.IntPtr hWnd);
'@

$SW_HIDE = 0
$SW_SHOW = 5

$script:ConsoleHandle = [Sayuri.Win32]::GetConsoleWindow()
$script:ConsoleVisible = $true
$script:LastStatus = ""
$script:Endpoint = $null
$script:CoreProcessId = $CorePid

$EndpointPath = Join-Path $Root "data\runtime\endpoint.json"
$IconPath = Join-Path $Root "assets\sayuri.ico"
$VersionFile = Join-Path $Root "VERSION"
$ProjectVersion = if (Test-Path -LiteralPath $VersionFile) {
    (Get-Content -LiteralPath $VersionFile -Raw).Trim()
} else { "unknown" }

# --- вспомогательное ---------------------------------------------------

function Get-Endpoint {
    if (-not (Test-Path -LiteralPath $EndpointPath)) { return $null }
    try {
        return (Get-Content -LiteralPath $EndpointPath -Raw | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Invoke-CoreApi {
    param(
        [string]$Path,
        [string]$Method = "GET",
        [int]$TimeoutSec = 5
    )

    $endpoint = $script:Endpoint
    if (-not $endpoint) { return $null }
    $uri = "$($endpoint.url)api/$Path"
    try {
        if ($Method -eq "POST") {
            return Invoke-RestMethod -Method Post -Uri $uri -TimeoutSec $TimeoutSec `
                -Headers @{ "X-Sayuri-Token" = $endpoint.token }
        }
        return Invoke-RestMethod -Uri $uri -TimeoutSec $TimeoutSec
    } catch {
        return $null
    }
}

function Show-Console {
    if ($script:ConsoleHandle -eq [System.IntPtr]::Zero) { return }
    [Sayuri.Win32]::ShowWindow($script:ConsoleHandle, $SW_SHOW) | Out-Null
    [Sayuri.Win32]::SetForegroundWindow($script:ConsoleHandle) | Out-Null
    $script:ConsoleVisible = $true
}

function Hide-Console {
    if ($script:ConsoleHandle -eq [System.IntPtr]::Zero) { return }
    [Sayuri.Win32]::ShowWindow($script:ConsoleHandle, $SW_HIDE) | Out-Null
    $script:ConsoleVisible = $false
}

function Open-Site {
    $endpoint = $script:Endpoint
    if (-not $endpoint) {
        Show-Balloon "Sayuri Yukishiro" "Адрес ещё не известен" "Warning"
        return
    }
    Start-Process $endpoint.url | Out-Null
}

function Set-TrayTooltip {
    param([string]$Text)

    # NotifyIcon.Text в .NET Framework ограничен 63 символами и бросает
    # ArgumentException при превышении: обрезаем, а не падаем в трее.
    if ($Text.Length -gt 63) {
        $Text = $Text.Substring(0, 60) + "..."
    }
    $script:Tray.Text = $Text
}

function Show-Balloon {
    param([string]$Title, [string]$Text, [string]$Icon = "Info")

    $script:Tray.BalloonTipTitle = $Title
    $script:Tray.BalloonTipText = $Text
    $script:Tray.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::$Icon
    $script:Tray.ShowBalloonTip(4000)
}

function Get-CoreProcess {
    if ($script:CoreProcessId -le 0) { return $null }
    return Get-Process -Id $script:CoreProcessId -ErrorAction SilentlyContinue
}

function Stop-Core {
    <#
        Сначала штатное завершение: ядро само закроет базу, снимет
        endpoint.json и остановит службы. Kill — только если не ответило.
    #>
    $script:Endpoint = Get-Endpoint
    $answer = Invoke-CoreApi -Path "shutdown" -Method "POST" -TimeoutSec 10
    $process = Get-CoreProcess

    if ($answer -and $process) {
        if (-not $process.WaitForExit(15000)) {
            $process.Kill()
        }
        return "soft"
    }

    if (-not $answer -and $process) {
        $process.Kill()
        return "kill"
    }
    if ($answer) { return "soft" }
    return "none"
}

function Start-Core {
    $arguments = @($PythonPrefix) + @("-m", "sayuri_yukishiro.main", "--serve", "--no-browser")
    $process = Start-Process -FilePath $PythonExe -ArgumentList $arguments `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru
    $script:CoreProcessId = $process.Id

    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        $endpoint = Get-Endpoint
        if ($endpoint -and $endpoint.pid -eq $process.Id) {
            $script:Endpoint = $endpoint
            return $true
        }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

# --- значок ------------------------------------------------------------

$script:Tray = New-Object System.Windows.Forms.NotifyIcon
if (Test-Path -LiteralPath $IconPath) {
    $script:Tray.Icon = New-Object System.Drawing.Icon $IconPath
} else {
    $script:Tray.Icon = [System.Drawing.SystemIcons]::Application
}
$script:Tray.Visible = $true
Set-TrayTooltip "Sayuri Yukishiro v$ProjectVersion"

$menu = New-Object System.Windows.Forms.ContextMenuStrip

function Add-MenuItem {
    param([string]$Text, [scriptblock]$Action, [switch]$Bold)

    $item = New-Object System.Windows.Forms.ToolStripMenuItem
    $item.Text = $Text
    if ($Bold) {
        $item.Font = New-Object System.Drawing.Font($item.Font, [System.Drawing.FontStyle]::Bold)
    }
    $item.add_Click($Action)
    $menu.Items.Add($item) | Out-Null
    return $item
}

function Add-MenuSeparator {
    $menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
}

$script:OpenItem = Add-MenuItem -Text "Открыть Sayuri" -Bold -Action { Open-Site }
$script:StatusItem = Add-MenuItem -Text "Состояние: проверяется…" -Action {
    $system = Invoke-CoreApi -Path "system"
    if ($system) {
        Show-Balloon "Состояние системы" (
            "Версия v$($system.version), ядро v$($system.core_version)`n" +
            "Службы $($system.healthy_services)/$($system.service_count) · база $($system.database_check)`n" +
            "Носитель: $($system.media)"
        )
    } else {
        Show-Balloon "Состояние системы" "Ядро не отвечает" "Error"
    }
}

Add-MenuSeparator

$script:WindowItem = Add-MenuItem -Text "Показать окно" -Action {
    if ($script:ConsoleVisible) { Hide-Console } else { Show-Console }
}

Add-MenuItem -Text "Перезапустить ядро" -Action {
    Show-Balloon "Sayuri Yukishiro" "Перезапуск ядра…"
    Stop-Core | Out-Null
    Start-Sleep -Milliseconds 500
    if (Start-Core) {
        Show-Balloon "Sayuri Yukishiro" "Ядро перезапущено: $($script:Endpoint.url)"
    } else {
        Show-Balloon "Sayuri Yukishiro" "Ядро не поднялось — откройте окно" "Error"
        Show-Console
    }
} | Out-Null

Add-MenuSeparator

Add-MenuItem -Text "Выход" -Action {
    $script:Timer.Stop()
    $result = Stop-Core
    $script:Tray.Visible = $false
    $script:Tray.Dispose()
    Show-Console
    if ($result -eq "kill") {
        Write-Warning "Ядро не ответило на мягкую остановку и было завершено принудительно."
    }
    [System.Windows.Forms.Application]::Exit()
} | Out-Null

$script:Tray.ContextMenuStrip = $menu
$script:Tray.add_DoubleClick({ Open-Site })

# --- опрос состояния ---------------------------------------------------

function Update-TrayState {
    $script:Endpoint = Get-Endpoint
    $health = Invoke-CoreApi -Path "health" -TimeoutSec 3

    if (-not $health) {
        $state = "stopped"
        Set-TrayTooltip "Sayuri Yukishiro v$ProjectVersion`nЯдро не отвечает"
        $script:StatusItem.Text = "Состояние: ядро не отвечает"
    } elseif ($health.status -eq "ok") {
        $state = "ok"
        # Подсказка в трее ограничена 63 символами — держим её короткой.
        Set-TrayTooltip "Sayuri v$($health.version)`n$($script:Endpoint.url)"
        $script:StatusItem.Text = "Состояние: активна"
    } else {
        $state = "degraded"
        Set-TrayTooltip "Sayuri v$($health.version)`nЕсть замечания"
        $script:StatusItem.Text = "Состояние: есть замечания"
    }

    if ($script:LastStatus -and $state -ne $script:LastStatus) {
        switch ($state) {
            "ok"       { Show-Balloon "Sayuri Yukishiro" "Система снова активна" "Info" }
            "degraded" { Show-Balloon "Sayuri Yukishiro" "Система работает с замечаниями" "Warning" }
            "stopped"  { Show-Balloon "Sayuri Yukishiro" "Ядро перестало отвечать" "Error" }
        }
    }
    $script:LastStatus = $state

    $script:WindowItem.Text = if ($script:ConsoleVisible) { "Скрыть окно" } else { "Показать окно" }
}

$script:Timer = New-Object System.Windows.Forms.Timer
$script:Timer.Interval = [Math]::Max(1, $PollSeconds) * 1000
$script:Timer.add_Tick({ Update-TrayState })

$script:Endpoint = Get-Endpoint
Update-TrayState
$script:Timer.Start()

if (-not $KeepConsole) {
    Hide-Console
    $script:WindowItem.Text = "Показать окно"
}

Show-Balloon "Sayuri Yukishiro" "Свёрнуто в область уведомлений. Двойной клик — открыть сайт."

try {
    [System.Windows.Forms.Application]::Run()
} finally {
    $script:Timer.Stop()
    $script:Timer.Dispose()
    if ($script:Tray) {
        $script:Tray.Visible = $false
        $script:Tray.Dispose()
    }
}
