# Sparrow Node setup for Windows. It installs Sparrow Node on this computer,
# pairs it with the Sparrow server below and keeps it running in the
# background. Folders are chosen afterwards in Sparrow. Made for one pairing:
# the code expires ten minutes after Sparrow showed it.
& {
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
  $Server = '__SPARROW_SERVER__'
  $Code = '__SPARROW_CODE__'

  function Report([string]$Stage) {
    try {
      Invoke-RestMethod -Method Post -Uri "$Server/api/v1/node/setup/$Code/progress" `
        -ContentType 'application/json' -Body ('{"stage":"' + $Stage + '"}') -TimeoutSec 10 | Out-Null
    } catch {}
  }
  # Runs a program and returns its exit code and output, without stopping on stderr.
  function Invoke-Program([string]$Program, [string[]]$Arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
      $output = & $Program @Arguments 2>&1 | ForEach-Object { "$_" }
      $code = $LASTEXITCODE
    } finally {
      $ErrorActionPreference = $previous
    }
    return [pscustomobject]@{ Code = $code; Text = (($output | Where-Object { $_ }) -join "`n").Trim() }
  }

  $stage = 'started'
  $why = ''
  try {
    $dir = Join-Path $env:LOCALAPPDATA 'SparrowNode'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    Report 'started'
    Write-Host "Setting up Sparrow Node in $dir"
    Stop-ScheduledTask -TaskName 'Sparrow Node' -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
      Where-Object { $_.CommandLine -like "*$dir\app\sparrow-node.py*" } |
      ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

    $stage = 'download'
    $why = "Couldn't download Sparrow Node from $Server. If the command expired, get a fresh one in Sparrow."
    Report 'installing'
    Write-Host "Downloading Sparrow Node from $Server"
    $archive = Join-Path $dir 'package.zip'
    $fresh = Join-Path $dir 'app.new'
    $app = Join-Path $dir 'app'
    Invoke-WebRequest -UseBasicParsing -Uri "$Server/api/v1/node/setup/$Code/package.zip" -OutFile $archive
    if (Test-Path $fresh) { Remove-Item -Recurse -Force $fresh }
    Expand-Archive -Path $archive -DestinationPath $fresh -Force
    Remove-Item -Force $archive
    if (Test-Path $app) { Remove-Item -Recurse -Force $app }
    Move-Item $fresh $app

    $stage = 'python'
    $why = "Couldn't get Python 3.12. Check this computer's internet connection, then run the command again."
    Report 'python'
    Write-Host 'Getting Python 3.12'
    $uv = @((Join-Path $dir 'uv\uv.exe'), (Join-Path $dir 'uv\bin\uv.exe')) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $uv) {
      $env:UV_UNMANAGED_INSTALL = Join-Path $dir 'uv'
      Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression | Out-Null
      $uv = @((Join-Path $dir 'uv\uv.exe'), (Join-Path $dir 'uv\bin\uv.exe')) | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $uv) { throw $why }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $dir 'python'
    if ((Invoke-Program $uv @('python', 'install', '3.12')).Code) { throw $why }
    $found = Invoke-Program $uv @('python', 'find', '3.12')
    if ($found.Code) { throw $why }
    $python = $found.Text.Trim()
    $pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
    if (-not (Test-Path $pythonw)) { $pythonw = $python }

    $stage = 'pair'
    $why = "Pairing didn't work. Get a fresh command in Sparrow and run it again."
    Report 'pairing'
    $arguments = @((Join-Path $app 'sparrow-node.py'), '--config', (Join-Path $dir 'node.json'), 'configure', '--server', $Server, '--code', $Code)
    if ($Server.StartsWith('http://')) { $arguments += '--allow-private-http' }
    if ((Get-Command transmission-daemon -ErrorAction SilentlyContinue) -or (Test-Path (Join-Path $env:ProgramFiles 'Transmission\transmission-daemon.exe'))) {
      $arguments += '--managed-downloader'
    }
    Push-Location $app
    try { $paired = Invoke-Program $python $arguments } finally { Pop-Location }
    if ($paired.Code) {
      if ($paired.Text) { $why = $paired.Text }
      throw $why
    }

    $stage = 'service'
    $why = "Sparrow Node couldn't start in the background."
    Report 'starting'
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $action = New-ScheduledTaskAction -Execute $pythonw -WorkingDirectory $app `
      -Argument ('"' + (Join-Path $app 'sparrow-node.py') + '" --config "' + (Join-Path $dir 'node.json') + '" run')
    if ($admin) {
      # Runs from startup, whether or not anyone is signed in.
      $trigger = New-ScheduledTaskTrigger -AtStartup
      $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
    } else {
      $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
      $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    }
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
      -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName 'Sparrow Node' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName 'Sparrow Node'

    Report 'running'
    Write-Host ''
    Write-Host 'Sparrow Node is running. Choose its folders in Sparrow.' -ForegroundColor Green
    if (-not $admin) {
      Write-Host 'It starts when you sign in. To start it with Windows instead, run this command again in PowerShell as administrator.'
    }
    if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
      Write-Host 'For media checks, also run: winget install Gyan.FFmpeg'
    }
    if (-not ($arguments -contains '--managed-downloader')) {
      Write-Host 'To download here, also run: winget install Transmission.Transmission, then run this command again.'
    }
  } catch {
    Report "failed:$stage"
    Write-Host ''
    $message = if ($why) { $why } else { $_.Exception.Message }
    Write-Host $message -ForegroundColor Red
  }
}
