@echo off
setlocal enableextensions

REM ============================================================
REM  Run QC.bat  --  Double-click to run a QC against the
REM                  newest CSV in the QC_Inbox folder.
REM
REM  Setup:
REM    1. Keep this .bat next to "QC Wizard.exe"
REM    2. The first run creates a QC_Inbox subfolder next to it
REM    3. Drop today's QC CSV into QC_Inbox, then double-click
REM    4. Pick the instrument in the popup -> done
REM ============================================================

REM ---- Locations (edit DROPBOX if you want a different folder) ----
set "EXE=%~dp0QC Wizard.exe"
set "DROPBOX=%~dp0QC_Inbox"

if not exist "%EXE%" (
    powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('QC Wizard.exe not found next to this .bat file.','Run QC','OK','Error') | Out-Null"
    exit /b 1
)

if not exist "%DROPBOX%" mkdir "%DROPBOX%" >nul 2>&1

REM ---- Instrument picker (PowerShell WinForms dialog) ----
set "PICK=%TEMP%\qc_instrument_%RANDOM%.txt"
if exist "%PICK%" del "%PICK%" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Add-Type -AssemblyName System.Windows.Forms;" ^
  "Add-Type -AssemblyName System.Drawing;" ^
  "$f = New-Object Windows.Forms.Form;" ^
  "$f.Text = 'Run QC';" ^
  "$f.Size = New-Object Drawing.Size(340,200);" ^
  "$f.StartPosition = 'CenterScreen';" ^
  "$f.FormBorderStyle = 'FixedDialog';" ^
  "$f.MaximizeBox = $false;" ^
  "$f.MinimizeBox = $false;" ^
  "$lbl = New-Object Windows.Forms.Label;" ^
  "$lbl.Text = 'Select instrument:';" ^
  "$lbl.Location = New-Object Drawing.Point(25,20);" ^
  "$lbl.Size = New-Object Drawing.Size(280,20);" ^
  "$cb = New-Object Windows.Forms.ComboBox;" ^
  "$cb.Location = New-Object Drawing.Point(25,45);" ^
  "$cb.Size = New-Object Drawing.Size(280,28);" ^
  "$cb.DropDownStyle = 'DropDownList';" ^
  "'Colorado','Ganges','HuangHe','Huron','Nile' | ForEach-Object { [void]$cb.Items.Add($_) };" ^
  "$cb.SelectedIndex = 0;" ^
  "$ok = New-Object Windows.Forms.Button;" ^
  "$ok.Text = 'Run QC';" ^
  "$ok.DialogResult = 'OK';" ^
  "$ok.Location = New-Object Drawing.Point(115,95);" ^
  "$ok.Size = New-Object Drawing.Size(110,34);" ^
  "$f.Controls.AddRange(@($lbl,$cb,$ok));" ^
  "$f.AcceptButton = $ok;" ^
  "if ($f.ShowDialog() -eq 'OK') { $cb.SelectedItem | Out-File -FilePath '%PICK%' -Encoding ASCII -NoNewline }"

if not exist "%PICK%" exit /b 0

set /p INSTRUMENT=<"%PICK%"
del "%PICK%" >nul 2>&1

if "%INSTRUMENT%"=="" exit /b 0

REM ---- Find newest .csv in dropbox ----
set "QCFILE="
for /f "delims=" %%f in ('dir /b /a-d /o-d "%DROPBOX%\*.csv" 2^>nul') do (
    set "QCFILE=%DROPBOX%\%%f"
    goto :got_file
)

:got_file
if "%QCFILE%"=="" (
    powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('No .csv file found in:`n%DROPBOX%`n`nDrop today''s QC CSV in there and try again.','Run QC','OK','Warning') | Out-Null"
    REM Open the dropbox folder so the user can drop a file in
    start "" "%DROPBOX%"
    exit /b 1
)

REM ---- Launch headless ----
start "" "%EXE%" --auto --instrument "%INSTRUMENT%" --qc-file "%QCFILE%"
endlocal
