@echo off
rem Puts a CORELINE icon on this user's desktop that starts the copy of the tool in this
rem folder (e.g. on the network drive), so everyone always runs the same, latest version.
rem Run it once per person; the icon lasts as long as the desktop does.
setlocal
set "CL_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dir = $env:CL_DIR;" ^
  "$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'CORELINE.lnk';" ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk);" ^
  "$s.TargetPath = Join-Path $dir 'run_coreline.bat';" ^
  "$s.WorkingDirectory = $dir;" ^
  "$s.IconLocation = (Join-Path $dir 'coreline\gui\assets\coreline.ico') + ',0';" ^
  "$s.WindowStyle = 7;" ^
  "$s.Description = 'CORELINE pipeline tool';" ^
  "$s.Save();" ^
  "Write-Host ('CORELINE icon added: ' + $lnk)"
if errorlevel 1 (
    echo Could not create the desktop icon.
)
pause
