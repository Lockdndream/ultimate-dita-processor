@echo off
setlocal
echo ============================================================
echo  DITA Converter -- Windows EXE Build
echo ============================================================
echo.

cd /d "%~dp0"

:: Use Python 3.11 explicitly — PyInstaller does not support Python 3.12+ for Streamlit
:: builds yet (socket C-extension bundling is broken on 3.13/3.14).
set PYTHON=py -3.11

%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.11 is not installed.
    echo        Download from https://python.org/downloads and install it.
    echo        PyInstaller does not support Python 3.13+ for Streamlit executables.
    pause
    exit /b 1
)

:: Show which Python will be used
for /f "tokens=*" %%v in ('%PYTHON% --version 2^>^&1') do echo Using: %%v
echo.

:: Pass through any arguments (e.g. --debug, --no-clean)
%PYTHON% build.py %*

if errorlevel 1 (
    echo.
    echo ============================================================
    echo  BUILD FAILED. See output above for details.
    echo  Tip: run  build.bat --debug  to keep the console visible.
    echo ============================================================
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Done! Distribute:  dist\DITA-Converter.exe
echo ============================================================
pause
endlocal
