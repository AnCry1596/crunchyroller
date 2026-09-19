@echo off
setlocal enabledelayedexpansion

echo ===================================================
echo       Crunchyroller Windows Build Script
echo ===================================================
echo.

:: 1. Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

:: 2. Check/Install dependencies and PyInstaller
echo [1/4] Installing / updating build requirements...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

:: 3. Check for ffmpeg.exe
echo.
echo [2/4] Checking for ffmpeg.exe...
if not exist "ffmpeg.exe" (
    echo [WARNING] ffmpeg.exe not found in current folder.
    where ffmpeg.exe >nul 2>&1
    if not errorlevel 1 (
        echo [INFO] Found ffmpeg in system PATH. Copying to root for bundling...
        for /f "delims=" %%i in ('where ffmpeg.exe') do (
            copy "%%i" "ffmpeg.exe" >nul
            goto :ffmpeg_done
        )
    ) else (
        echo [NOTICE] If you want ffmpeg.exe bundled in the release, place ffmpeg.exe in this folder.
    )
) else (
    echo [OK] ffmpeg.exe found in project folder.
)
:ffmpeg_done

:: 4. Run PyInstaller
echo.
echo [3/4] Building Crunchyroller with PyInstaller...
pyinstaller --clean -y crunchyroller.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b 1
)

:: 5. Copy extra files to dist
echo.
echo [4/4] Finalizing build output in dist\crunchyroller...
if exist "README.md" copy "README.md" "dist\crunchyroller\" >nul
if exist "LICENSE" copy "LICENSE" "dist\crunchyroller\" >nul
if exist "ffmpeg.exe" copy "ffmpeg.exe" "dist\crunchyroller\" >nul

echo.
echo ===================================================
echo [SUCCESS] Build completed!
echo Executable is located at: dist\crunchyroller\crunchyroller.exe
echo ===================================================
echo.
pause
