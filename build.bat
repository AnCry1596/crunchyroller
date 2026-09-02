@echo off
title Build crunchyroller Executable
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 build_exe.py
) else (
    python build_exe.py
)

pause
