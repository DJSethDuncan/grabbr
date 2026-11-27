# Build dist\grabbr.exe locally on Windows.
#   powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"

python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt

pyinstaller `
  --noconfirm --clean `
  --onefile --windowed `
  --name grabbr `
  --collect-data customtkinter `
  run_grabbr.py

Write-Host "`nBuilt dist\grabbr.exe"
