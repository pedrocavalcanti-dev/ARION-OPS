@echo off
setlocal
title ARION OPS - Gerenciador
set "MANAGER=%~dp0manage-arion.ps1"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -ProjectRoot "%~dp0..\.."
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
    echo.
    echo A operacao terminou com erro. Consulte a mensagem e os logs em var\logs.
    echo Codigo de saida: %RESULT%
)
echo.
pause
exit /b %RESULT%
