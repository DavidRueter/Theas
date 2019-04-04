#!/usr/bin/env python
import servicemanager
import sys
import win32event
import win32service
import win32serviceutil
import win32evtlogutil
import os

# import winreg

import TheasServer

__author__ = 'DavidRueter'
'''
Theas web application server Windows service wrapper.

Author: David Rueter (drueter@assyst.com)
Date: 5/9/2016
Description : Wrapper to run TheasServer web server as a Windows service.
Home:  https://github.com/davidrueter/Theas

Usage:
    TheasServerSvc.exe debug
        Runs as a console app (and not as a service)

    note that for the following, you must run the .exe from an elevated (i.e. admin) command prompt:
    TheasServerSvc.exe install
    TheasServerSvc.exe install --username <username> --password <PASSWORD> --startup auto
    TheasServerSvc.exe remove
    TheasServerSvc.exe start
    TheasServerSvc.exe stop


Note:  You may rename TheasServerSvc.exe prior to installing the service--and the service will
reflect the new name.

The service expects to find a file named settings.cfg located in the same directory from which this application was
launched.

Note that when run as a service, Theas settings must come from the settings.cfg file.  (It is not possible to
pass in command-line parameters to Theas when running as a service.)

Note that as of 4/4/2019 I recommend using pyinstaller to create the .exe because this works with Python 3.5 and beyond:
    pyinstaller TheasServerSvc.py --onefile

For more info about pywin32:

    ../venv_py36_theas/Lib/site-packages/PyWin32.chm contains a Windows .chm help file
    http://timgolden.me.uk/pywin32-docs/index.html contains HTML version of the .chm (not necessarily current)
        in particular:  http://timgolden.me.uk/pywin32-docs/servicemanager.html
    https://github.com/mhammond/pywin32 is the official pywin32 project
'''


# The name of the service will be SERVICE_NAME_PREFIX + _ + program_filename
# For example, if the .exe is named MyApp.exe, the service would be Theas_MyApp
SERVICE_NAME_PREFIX = 'Theas'

# The .dll for Windows Event Manager messages
MESSAGE_FILE_DLL = 'TheasMessages.dll'
# see: https://www.eventsentry.com/blog/2010/11/creating-your-very-own-event-m.html


def get_program_directory():
    program_cmd = sys.argv[0]
    program_directory = ''
    program_filename = ''

    if program_cmd:
        program_directory, program_filename = os.path.split(program_cmd)

    if not program_directory:
        # no path is provided if running the python script as: python myscript.py
        # fall back to CWD
        program_directory = os.getcwd()

        if program_directory.endswith('system32'):
            # a service application may return C:\Windows\System32 as the CWD

            # Look to the executable path.
            program_directory = os.path.dirname(sys.executable)

            if program_directory.endswith('system32'):
                # However this too will be returned as C:\Windows\System32 when
                # running as a service on Windows Server 2012 R2.  In that case...
                # we are stuck.
                program_directory = ''


    program_directory = os.path.normpath(program_directory)

    if not program_directory.endswith(os.sep):
        program_directory += os.sep

    return program_directory, program_filename

# Declare some globals
G_program_directory, G_program_filename = get_program_directory()
G_program_name, G_extension = os.path.splitext(G_program_filename)
G_service_name = SERVICE_NAME_PREFIX + '_' + G_program_name


def write_winlog(*args):
    # for convenience, wrap LogInfoMsg
    if len(args) >= 2:
        servicemanager.LogInfoMsg(G_service_name + ': ' + args[1])
    else:
        servicemanager.LogInfoMsg(G_service_name + ': ' + args[0])

'''
def RegisterEventLogMessage(program_name, program_directory):

    key = None

    key_val = 'SYSTEM\\CurrentControlSet\\Services\\EventLog\\Application\\' + program_name
    orig_key_value = ''

    write_winlog('Trying to register {} as the event message file'.format(G_message_file))

    try:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_val, 0, winreg.KEY_ALL_ACCESS)
            orig_key_value, this_type = winreg.QueryValueEx(key, 'EventMessageFile')
            if orig_key_value != G_message_file:
                winreg.SetValueEx(key, 'EventMessageFile', None, winreg.REG_SZ, G_message_file)
                winreg.SetValueEx(key, 'TypesSupported', None, winreg.REG_DWORD, 7)
        except Exception as e2:
            print(e2)
            key = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, key_val)
            winreg.SetValueEx(key, 'EventMessageFile', None, winreg.REG_SZ, G_message_file)
            winreg.SetValueEx(key, 'TypesSupported', None, winreg.REG_DWORD, 7)
    finally:
        try:
            winreg.CloseKey(key)
        finally:
            pass
'''


class TheasServerSvc(win32serviceutil.ServiceFramework):
    # Windows will call the methods of this class to control the service

    # Make sure that globals G_service_name is populated before this declaration

    _svc_name_ = G_service_name
    _svc_display_name_ = G_service_name
    _svc_description_ = SERVICE_NAME_PREFIX + ' web application server. See: https://github.com/davidrueter/Theas'

    # Optionally, you can set additional arguments that will be passed into the service when the service starts
    # However, if parameters are specified here, one of these must be "service" to indicate that the service
    # is to be run (see where run_service is set below.)  Otherwise, the default behavior is that if there
    # are parameters, only HandleCommandLine is called (i.e. for installing, removing service, etc.)...
    # and the service is not run.

    # If you want commandline parameters that are specified when the service is installed to be included here,
    # you must process the parameters yourself and set _exe_args_ before HandleCommandLine is called.
    # (HandleCommandLine will ignore or raise an error on additional parameters.)
    _exe_args_ = None   # 'service param1 param2 param3'


    # note:  we save the service name in a global to facilitate using this name when logging

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self.timeout = 2000


    def onServicePoll(self):
        # Wait for service stop signal, if I timeout, loop again
        rc = win32event.WaitForSingleObject(self.hWaitStop, self.timeout)

        # Check to see if self.hWaitStop happened
        if rc == win32event.WAIT_OBJECT_0:
            # Stop signal encountered
            write_winlog('Stopping')
        else:
            write_winlog('Alive and well')

    def SvcStop(self):

        write_winlog('Service stop request received')

        # Tell the TheasServer event loop to stop
        TheasServer.StopServer()

        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)

        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STOPPED,
                              (self._svc_name_, ''))

    def SvcDoRun(self):

        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED,
                              (self._svc_name_, ''))

        #for arg in sys.argv:
        #    write_winlog('Argument found: ' + arg)

        write_winlog('Calling TheasServer.run')

        # Start the TheasServer server.  It will run an event loop until the service is stopped and
        # TheasServer.StopServer() is called.
        TheasServer.run(run_as_svc=True)



if __name__ == '__main__':
    # Note:  we assume that the class declaration of TheasServerSvc
    # will populate the global variables (G_program_filename, etc.)
    # Those must be populated before anything else happens (i.e. we
    # can't change these values in initialize, etc.)

    run_service = False

    # peek to see if there is a parameter /service to explicitly tell us to run as a service
    for arg in sys.argv:
        if arg.lower() == 'service':
            run_service = True

    # if the .exe is run without arguments, default to run as a service
    if len(sys.argv) == 1:
        run_service = True

    if run_service:
        # if the .exe is run without arguments, run the service
        try:
            import win32traceutil
            # to help with error handling.
            # See: http://python.6.x6.nabble.com/Running-a-Windows-Python-service-without-pythonservice-exe-tp1956976p1956982.html

            servicemanager.Initialize(G_service_name, G_program_directory + MESSAGE_FILE_DLL)
                # note:  explicitly provide G_service_name so that the service is named according to the
                # current .exe filename (even if it is renamed) instead of the class name.

            servicemanager.PrepareToHostSingle(TheasServerSvc)
                # note:  "single" means that this .exe will host a single service.
                # The TheasServerSvc class we declared above will be what the Windows Service Manager
                # uses to control the service.

            servicemanager.StartServiceCtrlDispatcher()

        except (SystemExit, KeyboardInterrupt):
            raise
        except:
            msg = 'Error while trying to start service {}'.format(G_service_name)
            servicemanager.LogErrorMsg(G_service_name + ': ' + msg)
            print(msg)

            import traceback
            traceback.print_exc()

    else:
        try:
            # process the service-related command line parameters
            win32serviceutil.HandleCommandLine(TheasServerSvc)
        except Exception as e:
            msg = 'Error while calling HandleCommandLine: {}'.format(e)
            servicemanager.LogErrorMsg(G_program_filename + ': ' + msg)
            print(msg)

            import traceback
            traceback.print_exc()

        if len(sys.argv) > 1:
            msg = 'Service ' + sys.argv[1] + ' performed.'
            servicemanager.LogInfoMsg(G_program_filename + ': ' + msg)

        # do additional work needed after installation (i.e. register event message file)
        if len(sys.argv) > 1 and sys.argv[1] in ("install", "update"):

            try:
                servicemanager.SetEventSourceName(G_service_name, True)
            except Exception as e:
                msg = 'Failed to SetEventSourceName: {}'.format(e)
                servicemanager.LogErrorMsg(G_program_filename + ': ' + msg)
                print(msg)


            try:
                win32evtlogutil.AddSourceToRegistry(SERVICE_NAME_PREFIX + G_program_name,
                                                    msgDLL=G_program_directory + MESSAGE_FILE_DLL,
                                                    eventLogType="Application",
                                                    eventLogFlags=None)

                # note:  At one time I was having problems with win32evtlogutil.AddSourceToRegistry and so
                # I wrote my own function RegisterEventLogMessage which is preserved above in a doc string
                # for reference.  It is no longer needed at present.
                # RegisterEventLogMessage(SERVICE_NAME_PREFIX + G_program_name, G_program_directory)



            except Exception as e:
                msg = 'Failed to RegisterEventLogMessage: {}'.format(e)
                servicemanager.LogErrorMsg(G_program_filename + ': ' + msg)
                print(msg)
                pass

