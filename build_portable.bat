@echo off
cd /d "%~dp0"
title Clubapp VPN - Portable build
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
  where py >nul 2>nul
  if errorlevel 1 (
    echo Python is not installed. Install Python 3.12 or older ^(64-bit^) from python.org
    echo and tick "Add python.exe to PATH", then run this file again.
    echo.
    pause
    exit /b 1
  )
  set "PY=py"
)
echo Using: %PY%
%PY% -m pip install --upgrade pyinstaller pillow
%PY% build_portable.py
echo.
echo ------------------------------------------------------------
echo Finished. Portable zip: Output\ClubappVPN-Portable-v*.zip
echo ------------------------------------------------------------
pause
