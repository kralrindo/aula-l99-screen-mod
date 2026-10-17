@echo off
cd /d "%~dp0"
python -c "import PIL, serial, icoextract, pycaw, psutil, winrt.windows.media.control" 2>nul || pip install pillow pyserial icoextract pycaw comtypes psutil winrt-runtime winrt-Windows.Foundation winrt-Windows.Media.Control
start "" pythonw tools\l99_deck_gui.py
