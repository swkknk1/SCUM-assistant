@echo off
title SCUM Server Downloader
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0download_server.ps1"
pause
