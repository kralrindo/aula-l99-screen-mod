@echo off
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3 is needed: https://www.python.org/downloads/  - tick "Add python.exe to PATH" & pause & exit /b 1)
python -c "import PIL" 2>nul || pip install pillow
python install.py %1
pause
