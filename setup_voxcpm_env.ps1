$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $Root ".venv_voxcpm"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' was not found. Install Python 3.12 or add it to PATH."
}

if (-not (Test-Path $PythonExe)) {
    py -3.12 -m venv $VenvDir
}

& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install voxcpm soundfile
& $PythonExe -c "import voxcpm, soundfile; print('VoxCPM environment is ready')"

Write-Host ""
Write-Host "VoxCPM Python path:"
Write-Host $PythonExe
