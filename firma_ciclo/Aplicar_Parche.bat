@echo off
chcp 65001 >nul
title Aplicar parche - Check list nuevo
cd /d "%~dp0"

set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" aplicar_parche.py %*
echo.
pause
