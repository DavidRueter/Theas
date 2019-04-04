CALL \Projects\Theas\venv_py36_theas\scripts\activate.bat
del \Projects\Theas\dist\*.exe
cd \Projects\Theas
pyinstaller TheasServerSvc.py --onefile
cd \Projects\Theas\dist
PAUSE
