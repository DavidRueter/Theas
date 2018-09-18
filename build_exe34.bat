CALL \Projects\Theas\venv_py34_theas\scripts\activate.bat
del \Projects\Theas\dist\*.*
cd \Projects\Theas
python setup.py py2exe
cd \Projects\Theas\dist
PAUSE
