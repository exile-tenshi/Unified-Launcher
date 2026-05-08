@echo off
setlocal enabledelayedexpansion
title Tenshi Build System

echo.
echo  ████████╗███████╗███╗   ██╗███████╗██╗  ██╗██╗
echo  ╚══██╔══╝██╔════╝████╗  ██║██╔════╝██║  ██║██║
echo     ██║   █████╗  ██╔██╗ ██║███████╗███████║██║
echo     ██║   ██╔══╝  ██║╚██╗██║╚════██║██╔══██║██║
echo     ██║   ███████╗██║ ╚████║███████║██║  ██║██║
echo     ╚═╝   ╚══════╝╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝╚═╝
echo.
echo  TenshiVoice.exe Builder  v3.0
echo  ─────────────────────────────
echo.

REM Step 1: Install dependencies
echo [1/5] Installing Python dependencies...
pip install -r requirements_desktop.txt --quiet
if errorlevel 1 (
    echo ERROR: pip install failed. Make sure Python 3.9+ is installed.
    pause
    exit /b 1
)
echo       Done.

REM Step 2: Convert logo PNG → ICO (requires Pillow)
echo [2/5] Converting logo to .ico format...
python -c "from PIL import Image; img = Image.open('../TenshiWeb/tenshi_logo.png').convert('RGBA'); img.save('tenshi_logo.ico', format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])" 2>nul
if errorlevel 1 (
    echo       WARNING: Could not convert icon. Build will continue without custom icon.
) else (
    echo       Done.
)

REM Step 3: Clean previous build
echo [3/5] Cleaning previous build artifacts...
if exist "dist\TenshiVoice.exe" del /f "dist\TenshiVoice.exe"
if exist "build" rmdir /s /q "build" 2>nul
echo       Done.

REM Step 4: Run PyInstaller
echo [4/5] Building TenshiVoice.exe (this may take 2-5 minutes)...
pyinstaller tenshi.spec --noconfirm --clean
if errorlevel 1 (
    echo ERROR: PyInstaller build failed. Check output above for details.
    pause
    exit /b 1
)
echo       Done.

REM Step 5: Verify output
echo [5/5] Verifying output...
if exist "dist\TenshiVoice.exe" (
    for %%A in ("dist\TenshiVoice.exe") do set SIZE=%%~zA
    set /a SIZE_MB=!SIZE! / 1048576
    echo.
    echo  ✓ Build successful!
    echo  ✓ Output: dist\TenshiVoice.exe  (!SIZE_MB! MB)
    echo.
    echo  Upload dist\TenshiVoice.exe to files.tenshi.lol/TenshiVoice.exe
    echo  to make it available for download on the website.
    echo.
) else (
    echo ERROR: TenshiVoice.exe not found in dist\. Build may have failed.
)

pause
