$ErrorActionPreference = 'Stop'
$fixtureRoot = Join-Path $env:RUNNER_TEMP 'sparrow-native-check'
New-Item -ItemType Directory -Force $fixtureRoot | Out-Null
$env:SPARROW_DATA_DIR = Join-Path $fixtureRoot 'server'
$env:SPARROW_WINDOWS_FIXTURE = $fixtureRoot
$program = Join-Path $env:ProgramFiles 'Sparrow Node'
$installer = Start-Process -FilePath 'packaging/output/SparrowNode-Setup-x64.exe' -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -Wait -PassThru
if ($installer.ExitCode -ne 0) { throw 'Installer failed.' }
$server = Start-Process -FilePath 'python' -ArgumentList '-m uvicorn backend.main:app --host 127.0.0.1 --port 8893' -PassThru -RedirectStandardOutput "$fixtureRoot/server.log" -RedirectStandardError "$fixtureRoot/server-error.log"
try {
  $env:SPARROW_FFMPEG = "$program/_internal/bin/ffmpeg.exe"
  $env:SPARROW_FFPROBE = "$program/_internal/bin/ffprobe.exe"
  python tests/windows_installed_node.py "$program"
  if ($LASTEXITCODE -ne 0) { throw 'Installed node acceptance failed.' }
  $service = Get-CimInstance Win32_Service -Filter "Name='SparrowNode'"
  if ($service.StartName -ne 'NT AUTHORITY\LocalService') { throw 'Node service has an unexpected account.' }
  if ($service.StartMode -ne 'Auto') { throw 'Node will not restart with Windows.' }
} finally {
  & "$program/SparrowService.exe" stop
  Stop-Process -Id $server.Id -ErrorAction SilentlyContinue
  Start-Process -FilePath "$program/unins000.exe" -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -Wait
}
if (-not (Test-Path "$fixtureRoot/library/Installed Fixture.mp4")) { throw 'Uninstall removed media.' }
