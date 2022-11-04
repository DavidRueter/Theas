CALL \Projects\Theas\venv_py310_theas\scripts\activate.bat
del \Projects\Theas\dist\*.exe

cd \Projects\Theas
python makeversion.py

REM See https://pythonhosted.org/PyInstaller/usage.html for more options
pyinstaller TheasServerSvc.py --onefile --icon Theas.ico --version-file versioninfo.cfg

copy Theas.js .\dist\
copy TheasVue.js .\dist\
copy TheasMessages.dll .\dist\
copy SAMPLE_settings.cfg .\dist\

cd \Projects\Theas\dist

PAUSE
