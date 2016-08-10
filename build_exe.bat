CALL \Projects\TheasServer\venv_py34_theas\scripts\activate.bat
del \Projects\TheasServer\dist\*.*
cd \Projects\TheasServer
python setup.py py2exe
cd \Projects\TheasServer\dist
PAUSE
