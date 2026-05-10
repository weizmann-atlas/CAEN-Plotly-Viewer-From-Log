@echo off
echo === CAEN Log Viewer - Windows build ===

pip install -r requirements.txt
if errorlevel 1 goto :error

rmdir /s /q build 2>nul
rmdir /s /q dist  2>nul

pyinstaller caen_viewer.spec --noconfirm
if errorlevel 1 goto :error

echo.
echo Build complete.
echo Output: dist\CAEN_Log_Viewer.exe
echo.
echo The executable is self-contained — just distribute the single .exe file.
echo On first launch Windows extracts resources to a temp folder; this is normal.
echo.
echo NOTE: Windows Defender / SmartScreen may warn on first run.
echo This is a known false-positive for PyInstaller executables.
echo Users can click "More info" -> "Run anyway" to proceed.
pause
exit /b 0

:error
echo.
echo BUILD FAILED. Check output above for errors.
pause
exit /b 1
