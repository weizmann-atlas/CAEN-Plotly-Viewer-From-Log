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
echo Output folder: dist\CAEN_Log_Viewer\
echo Launch:        dist\CAEN_Log_Viewer\CAEN_Log_Viewer.exe
echo.
echo IMPORTANT: distribute the entire dist\CAEN_Log_Viewer\ folder (e.g. zipped).
echo The exe requires QtWebEngineProcess.exe and resource files next to it —
echo these cannot be packed into a single file due to Qt WebEngine constraints.
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
