CALL \Projects\Theas\venv_py311_theas\scripts\activate.bat
del \Projects\Theas\dist\*.exe

cd \Projects\Theas
python makeversion.py

REM See https://pythonhosted.org/PyInstaller for more options
pyinstaller TheasServerSvc.py --clean --onefile --icon Theas.ico --version-file versioninfo.cfg

REM Try this if PythonService.exe doesn't get included by PyInstaller
REM pyinstaller TheasServerSvc.py --onefile --icon Theas.ico --version-file versioninfo.cfg --hidden-import=win32serviceutil --hidden-import=win32event --hidden-import=win32service
REM--hidden-import=win32serviceutil --hidden-import=win32event --hidden-import=win32service
REM pyinstaller TheasServerSvc.py --onefile --icon Theas.ico --version-file versioninfo.cfg --add-binary "C:\Projects\Theas\venv_py311_theas\Lib\site-packages\win32\PythonService.exe;."

REM Try this if you need more control...but take care to update the hardcoded paths in the .spec file
REM pyinstaller TheasServerSvc.spec

copy Theas.js .\dist\
copy TheasVue.js .\dist\
copy TheasMessages.dll .\dist\
copy SAMPLE_settings.cfg .\dist\

cd \Projects\Theas\dist

PAUSE
