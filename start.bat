@echo off
rem Finanz-Dashboard lokal starten (Windows): Doppelklick genuegt.
rem Zum Beenden: Einstellungen -> "Dashboard beenden" oder dieses Fenster schliessen.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Finanz-Dashboard
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
if not defined PY goto nopython
%PY% "app\launcher.py" %*
if errorlevel 1 pause
exit /b

:nopython
echo.
echo   Python 3.10 oder neuer wurde nicht gefunden.
echo   Bitte von https://www.python.org/downloads/ installieren und dabei
echo   "Add python.exe to PATH" anhaken. Danach start.bat erneut ausfuehren.
echo.
echo   Python 3.10 or newer was not found. Please install it from
echo   https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run start.bat again.
echo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1
