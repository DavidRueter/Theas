from distutils.core import setup
import py2exe
import sys
import getpass
import socket

# If building a Windows service, uncomment the following line:
from win32 import servicemanager
from win32 import win32service
from win32 import win32api
from win32 import win32event
import datetime
import os

# See:  http://www.py2exe.org/index.cgi/ListOfOptions
# Also, see:  http://stackoverflow.com/questions/22390058/how-to-get-py2exe-to-build-in-copyright-information/37124090#37124090

if len(sys.argv) == 1:
    sys.argv.append('py2exe')
    sys.argv.append("-q")

def inc_build():
    theas_version = ''

    try:
        with open('version.cfg', 'r') as f:
            theas_version = f.read()
            f.close()
    except Exception as e:
        theas_version = '0.0.0.0'

    ver_parts = theas_version.split(sep='.', maxsplit=3)
    ver_parts[3] = str(int(ver_parts[3]) + 1)
    theas_version = '.'.join(ver_parts)

    try:
        with open('version.cfg', 'w') as f:
            f.write(theas_version)
            f.close()
    except Exception as e:
        print('WARNING:  Could not write new build version number out to version.cfg')

    try:
        with open('version_history.txt', 'a') as f:
            f.write('\t'.join(list([datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S:%f'), 'ver. ' + theas_version, getpass.getuser(), socket.gethostname() + '\n'])))
            f.close()
    except Exception as e:
        try:
            with open('version_history.txt', 'w') as f2:
                f2.write('\t'.join(list([datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S:%f'), 'ver. ' + theas_versionm, getpass.getuser(), socket.gethostname() + '\n'])))
                f2.close()
        except:
            print('WARNING:  Could not write new build version number out to version_history.txt')

    print('New version: {}'.format(theas_version))

    return theas_version

THEAS_VERSION = inc_build()

class Target:
    def __init__(self, **kw):
        global THEAS_VERSION

        self.__dict__.update(kw)
        # for the versioninfo resources
        self.version = THEAS_VERSION
        self.company_name = "David Rueter"
        self.copyright = "Copyright (c) 2016 by David Rueter"
        self.name = "Theas Web Application Server"
        self.product_name = 'https://github.com/davidrueter/theas'


# create an instance of class Target
# and give it additional needed info
target = Target(
    description="Theas Web Application Server",
    # this is your code file
    script='TheasServer.py',
    #cmdline_style='pywin32',
    # this will form TestProgram.exe
    dest_base="TheasServer",
    icon_resources=[(1, "Theas.ico")],
    dll_excludes=['MSVCR100D.dll'],
)

target_svc = Target(
    description="Theas Web Application Server (service)",
    # this is your code file
    modules='TheasServerSvc',
    #cmdline_style='pywin32',
    # this will form TestProgram.exe
    dest_base="TheasServerSvc",
    icon_resources=[(1, "Theas.ico")],
    dll_excludes=['MSVCR100D.dll'],
)


setup(

    # Options for py2exe
    #    bundle_files:
    #      1=Bundle everything in the .EXE
    #      2=Bundle everything but the Python interpreter in the .EXE
    #      3=Don't bundle anything into the .EXE
    options={'py2exe':
                 {
                     'bundle_files': 1,
                     'compressed': True,
                     'packages': ['win32',],

                 }
             },

    #Name of zipfile to generate. If set to None, files will be bundled with the .EXE
    zipfile=None,

    # List of scripts to convert into console .EXEs
    #console=['TheasServer.py'],
    console=[target],



    # List of module names containing win32 service classes to convert into Windows services.
    #    Corresponds to the physical .py file that defines the service.
    #    For a service, the module name (not the filename) must be specified!

    #UNCOMMENT the following line to package as a Windows service
    #service = ['TheasServerSvc'],
    service=[target_svc],

    # List of additional files to be included.
    #    This is a list of tuples, where the first element is the folder (and empty string means the folder
    #    where the .EXE is located), and the second element is a list of filenames.

    data_files=[('', ['SAMPLE_settings.cfg', 'Theas.js',
                      'libeay32.dll', 'ssleay32.dll',
                      'sybdb.dll',
                      'TheasMessages.dll'
                      ])],

    # If using OpenSSL:
    # 'libeay32.dll', 'ssleay32.dll',

    # If you want to cheat and include MS VC++ redistributable
    # 'msvcp100.dll', 'msvcr100.dll',

    # (better to download from:
    # (x64)  https://www.microsoft.com/en-us/download/details.aspx?id=13523
    # (x86)  https://www.microsoft.com/en-us/download/details.aspx?id=8328


    #Python file that will be run when setting up the runtime environment
    #custom-boot-script='',

    # List of scripts to convert into GUI .EXEs
    #    If the script does not provide a GUI, this option will cause the application to run in the background
    #    with no UI and no console.
    #     Uncomment the following line to have the application run in the background

    #windows=[{
    #    'script': 'TheasServer.py'

    #}],
)
