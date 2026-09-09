@echo off
rem ============================================================
rem  Platform Enterprise BI-QA Workbench - one-click launcher (bat v2.6.2, ASCII-safe)
rem  Usage:  double-click then choose [F]  (one-click product: portal + browser)
rem          or run:  setup.bat -Product
rem  Params are forwarded to setup.ps1 (see its header comments):
rem    -Product  -AutoInstall  -AutoMySQLZip  -MySQLRootPassword xxx
rem    -SkipDB  -SkipPortal  -Verify  -ResetProfile
rem  Note: this .bat is intentionally pure ASCII; all Chinese text is
rem        printed by setup.ps1. v2.6.2: launcher reads setup.ps1 as UTF-8
rem        explicitly (BOM-independent), so an editor that strips the BOM
rem        can no longer break parsing on PowerShell 5.1 (GBK console).
rem ============================================================
echo [launcher v2.6.2] Platform Enterprise BI-QA Workbench - starting setup.ps1 ...
cd /d "%~dp0"
if not exist "%~dp0setup.ps1" (
  echo [ERROR] setup.ps1 not found. Please extract the whole package first.
  pause
  exit /b 1
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -Command "$p='%~dp0setup.ps1';$t=[IO.File]::ReadAllText($p,[System.Text.Encoding]::UTF8);$t=$t.TrimStart([char]0xFEFF);$s=[scriptblock]::Create($t);& $s %*"
echo.
echo [done] exit code = %ERRORLEVEL%  (window stays open; press any key to close)
pause
