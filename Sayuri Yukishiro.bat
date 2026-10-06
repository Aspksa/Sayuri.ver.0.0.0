@echo off
rem Единственная точка входа для пользователя Windows.
rem Путь берётся от расположения этого файла, поэтому проект работает
rem с любого носителя: внутреннего диска, внешнего диска или флешки.
setlocal
chcp 65001 >nul 2>&1
title Sayuri Yukishiro

set "SAYURI_LAUNCHER=%~dp0scripts\launcher.ps1"

if not exist "%SAYURI_LAUNCHER%" (
    echo [ОШИБКА] Не найден файл запуска:
    echo          %SAYURI_LAUNCHER%
    echo          Похоже, копия проекта неполная.
    echo.
    pause
    exit /b 1
)

where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Windows PowerShell не найден.
    echo.
    pause
    exit /b 1
)

powershell.exe -NoProfile -NoLogo -ExecutionPolicy Bypass -File "%SAYURI_LAUNCHER%" %*
set "SAYURI_EXIT=%ERRORLEVEL%"

if not "%SAYURI_EXIT%"=="0" (
    echo.
    echo Запуск завершился с кодом %SAYURI_EXIT%.
    pause
)

endlocal & exit /b %SAYURI_EXIT%
