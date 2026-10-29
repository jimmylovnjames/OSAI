# Install OSAI on Windows 10/11 and start it at sign-in.
# Run in PowerShell:  powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
$ErrorActionPreference = "Stop"
$Src = Split-Path -Parent $PSScriptRoot
$Prefix = Join-Path $env:LOCALAPPDATA "OSAI"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Host "Installing Python..."
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
}
New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
python -m venv "$Prefix\venv"
& "$Prefix\venv\Scripts\python.exe" -m pip install -q --upgrade pip
& "$Prefix\venv\Scripts\pip.exe" install -q "$Src[browser,desktop]"
& "$Prefix\venv\Scripts\playwright.exe" install chromium

$Startup = [Environment]::GetFolderPath("Startup")
$Shell = New-Object -ComObject WScript.Shell
$Lnk = $Shell.CreateShortcut((Join-Path $Startup "OSAI.lnk"))
$Lnk.TargetPath = "$Prefix\venv\Scripts\pythonw.exe"
$Lnk.Arguments = "-m osai --mini"
$Lnk.WorkingDirectory = $Prefix
$Lnk.Save()
Start-Process "$Prefix\venv\Scripts\pythonw.exe" -ArgumentList "-m osai --mini"
Write-Host "OSAI installed and running. It starts automatically when you sign in."
