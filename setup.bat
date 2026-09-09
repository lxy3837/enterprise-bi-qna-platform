@echo off
rem ============================================================
rem  Platform Enterprise BI-QA Workbench - one-click launcher (bat v2.6.3, ASCII-safe)
rem  Usage:  double-click then choose [F]  (one-click product: portal + browser)
rem          or run:  setup.bat -Product
rem  Params are forwarded to setup.ps1 (see its header comments):
rem    -Product  -AutoInstall  -AutoMySQLZip  -MySQLRootPassword xxx
rem    -SkipDB  -SkipPortal  -Verify  -ResetProfile
rem  Note: this .bat is intentionally pure ASCII; all Chinese text is
rem        printed by setup.ps1.
rem  v2.6.2: read setup.ps1 as UTF-8 (BOM-independent) but ran it as a scriptblock,
rem          which broke $MyInvocation/$PSScriptRoot (script Path was null) -> crash.
rem  v2.6.3: step 1 repairs a stripped UTF-8 BOM on setup.ps1 (PowerShell 5.1 would
rem          otherwise decode it as GBK and garble the Chinese text); step 2 runs
rem          setup.ps1 with powershell -File so all script-context variables and
rem          advanced-parameter semantics (CmdletBinding/PSBoundParameters) work.
rem ============================================================
echo [launcher v2.6.3] Platform Enterprise BI-QA Workbench - starting setup.ps1 ...
cd /d "%~dp0"
if not exist "%~dp0setup.ps1" (
  echo [ERROR] setup.ps1 not found. Please extract the whole package first.
  pause
  exit /b 1
)
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
rem -- step 1: ensure setup.ps1 has a UTF-8 BOM (repair only when missing) --
"%PS%" -NoProfile -ExecutionPolicy Bypass -Command "$p='%~dp0setup.ps1';$b=[IO.File]::ReadAllBytes($p);if(-not ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)){[IO.File]::WriteAllText($p,[IO.File]::ReadAllText($p,[System.Text.Encoding]::UTF8),(New-Object System.Text.UTF8Encoding($true)))}"
if errorlevel 1 goto launcherror
rem -- step 2: run the real script (keeps $MyInvocation/$PSScriptRoot/advanced params) --
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "RC=%ERRORLEVEL%"
goto end
:launcherror
echo [ERROR] Failed to prepare setup.ps1 (UTF-8 BOM repair). Is the file locked or read-only?
set "RC=1"
:end
echo.
echo [done] exit code = %RC%  (window stays open; press any key to close)
pause
exit /b %RC%
