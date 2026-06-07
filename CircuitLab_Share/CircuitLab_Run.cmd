@echo off
cd /d "%~dp0"

if exist "%USERPROFILE%\anaconda3\python.exe" (
  "%USERPROFILE%\anaconda3\python.exe" main.py
  pause
  exit /b
)

if exist "%USERPROFILE%\miniconda3\python.exe" (
  "%USERPROFILE%\miniconda3\python.exe" main.py
  pause
  exit /b
)

if exist "%LOCALAPPDATA%\anaconda3\python.exe" (
  "%LOCALAPPDATA%\anaconda3\python.exe" main.py
  pause
  exit /b
)

if exist "%LOCALAPPDATA%\miniconda3\python.exe" (
  "%LOCALAPPDATA%\miniconda3\python.exe" main.py
  pause
  exit /b
)

python.exe --version > "%TEMP%\circuitlab_python_check.txt" 2>&1
findstr /R /C:"^Python 3" "%TEMP%\circuitlab_python_check.txt" >NUL 2>&1
if not errorlevel 1 (
  python.exe main.py
  pause
  exit /b
)

py.exe -3 --version > "%TEMP%\circuitlab_python_check.txt" 2>&1
findstr /R /C:"^Python 3" "%TEMP%\circuitlab_python_check.txt" >NUL 2>&1
if not errorlevel 1 (
  py.exe -3 main.py
  pause
  exit /b
)

echo Python was not found on this PC.
echo Install Python 3 from https://www.python.org/downloads/
echo IMPORTANT: Check "Add python.exe to PATH" during install.
echo Then double-click CircuitLab_Run.cmd again.
echo.
echo If Microsoft Store opens or only "Python" appears, turn off
echo Windows Settings ^> Apps ^> Advanced app settings ^> App execution aliases
echo for python.exe and python3.exe, or install Python from python.org.
pause
