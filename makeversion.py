# Writes a versioninfo.cfg file that can be used by pyinstaller to add
# attributes to the .EXE file.

# Reads the previous version number from version.cfg, increments it,
# and then writes a log entry to version_history.txt

# The new version number (and other static information included in the string literal below)
# is then included in the .EXE


import win32timezone
import datetime
import getpass
import socket

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
                f2.write('\t'.join(list([datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S:%f'), 'ver. ' + theas_version, getpass.getuser(), socket.gethostname() + '\n'])))
                f2.close()
        except:
            print('WARNING:  Could not write new build version number out to version_history.txt')

    print('New version: {}'.format(theas_version))

    return theas_version


ver_string = inc_build()
ver_tuple = tuple(map(int, ver_string.strip(".").split(".")))

template_str = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={ver_tuple},
    prodvers={ver_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
StringFileInfo(
  [
  StringTable(
    u'040904B0',
    [StringStruct('CompanyName', u'David Rueter'),
    StringStruct('FileDescription', u'Theas Web Application Server'),
    StringStruct('FileVersion',  '{ver_string}'),
    StringStruct('InternalName', u'TheasServer'),
    StringStruct('LegalCopyright', u'David B. Rueter (drueter@assyst.com)'),
    StringStruct('OriginalFilename', u'TheasServerSvc.exe'),
    StringStruct('ProductName', u'Theas (https://git.io/fjtAw)'),
    StringStruct('ProductVersion', '{ver_string}')])
  ]),
VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)"""


out_file = open('versioninfo.cfg', 'w')
out_file.write(template_str)
out_file.close()

