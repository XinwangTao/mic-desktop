param(
    [string]$Venv = 'F:\cache\venvs\micbuild'
)

# Package MicDesktop as a onefile exe:
#   - embed appicon.ico (shown by Explorer / taskbar)
#   - embed version_info.txt (file properties)
#   - bundle micapp/assets so the window icon works after packaging
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 as ANSI
# when there is no BOM, so UTF-8 Chinese comments here get mangled.
Set-Location 'F:\projects\mic-desktop'
# NOTE: --specpath build means relative paths resolve against build\, so every
# asset path below has to be absolute or PyInstaller looks in build\micapp\assets.
& "$Venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --noconsole `
  --name MicDesktop --distpath dist --workpath build --specpath build `
  --icon 'F:\projects\mic-desktop\micapp\assets\appicon.ico' `
  --version-file 'F:\projects\mic-desktop\packaging\version_info.txt' `
  --add-data 'F:\projects\mic-desktop\micapp\assets;micapp\assets' `
  --collect-all bleak --collect-submodules winrt `
  --collect-data certifi --hidden-import win32com.client --hidden-import pythoncom `
  --exclude-module tkinter --exclude-module numpy --exclude-module matplotlib main.py *> 'F:\cache\pyinstaller.log'
exit $LASTEXITCODE
