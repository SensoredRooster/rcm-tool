$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m unittest discover -v
if (Test-Path dist) { Remove-Item dist -Recurse -Force }
if (Test-Path build) { Remove-Item build -Recurse -Force }
python -m PyInstaller --noconfirm --clean --windowed --name GamepadSignalLab --collect-all PySide6 gamepad_signal_lab.py
Write-Host "Portable build: dist\GamepadSignalLab\GamepadSignalLab.exe"
