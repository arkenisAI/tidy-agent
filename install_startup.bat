@echo off
rem Adds Tidy Agent (background mode) to Windows startup.
set "TARGET=%~dp0run_background.bat"
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Tidy Agent.lnk"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LINK%'); $s.TargetPath='%TARGET%'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
echo Tidy Agent will now start with Windows. Delete "%LINK%" to undo.
pause
