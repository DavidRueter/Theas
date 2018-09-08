#!/usr/bin/env python
__author__ = 'DavidRueter'
'''
 Theas web application server Windows service wrapper.

 Author: David Rueter (drueter@assyst.com)
 Date: 5/9/2016
 Description : Wrapper to run TheasServer web server as a Windows service.
 Home:  https://github.com/davidrueter/Theas

 Usage : TheasServerSvc.exe /install
 Usage : TheasServerSvc.exe /remove

 C:\>python TheasServerSvc.py  --username <username> --password <PASSWORD> --startup auto install

 Note:  You may rename TheasServerSvc prior to installing the service--and the service will
 reflect the new name.

 The service expects to find a file named settings.cfg located in the same directory from which this application was
 launched.

 Note that when run as a service, Theas settings must come from the settings.cfg file.  (It is not possible to
 pass in command-line parameters to Theas when running as a service.)

 See:  http://timgolden.me.uk/pywin32-docs/servicemanager.html

'''

SERVICE_NAME_PREFIX = 'Theas'
MESSAGE_FILE_DLL = 'TheasMessages.dll'
# see: https://www.eventsentry.com/blog/2010/11/creating-your-very-own-event-m.html

from win32 import servicemanager
import sys
import win32event
import win32service
import win32serviceutil

import TheasServer
#import winreg
import win32evtlogutil
import os

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
    # Make sure that globals G_service_name is populated before
    # this declaration

    _svc_name_ = G_service_name
    _svc_display_name_ = G_service_name
    _svc_description_ = SERVICE_NAME_PREFIX + ' web application server. See: https://github.com/davidrueter/Theas'

    # save the service name in a global, to facilitate logging


    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)


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

        TheasServer.StopServer()

        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)

        # Clean up
        #TheasServer.cleanup()

        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STOPPED,
                              (self._svc_name_, ''))

    def SvcDoRun(self):

        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED,
                              (self._svc_name_, ''))

        self.timeout = 3000

        # start TheasServer server
        TheasServer.run(run_as_svc=True)


if __name__ == '__main__':
    # Note:  we assume that the class declaration of TheasServerSvc
    # will populate the global variables (G_program_filename, etc.)
    # Those must be populated before anything else happens (i.e.
    # can't change the values in initialize, etc.)


    # if called without argvs, let's run !
    if len(sys.argv) == 1:
        try:
            servicemanager.Initialize('TheasServerSvc', G_program_directory + MESSAGE_FILE_DLL)
            servicemanager.PrepareToHostSingle(TheasServerSvc)
            servicemanager.StartServiceCtrlDispatcher()

        #except win32service.error:
        except Exception as e:
            msg = 'Error: {}'.format(e)
            servicemanager.LogErrorMsg(G_service_name + ': ' + msg)
            print(msg)
            win32serviceutil.usage()

    else:
        win32serviceutil.HandleCommandLine(TheasServerSvc)

        if sys.argv[1] in ("install", "update"):

            msg = 'Performing installation tasks'
            servicemanager.LogInfoMsg(G_program_filename + ': ' + msg)

            try:
                msg = 'About to SetEventSourceName'
                servicemanager.LogInfoMsg(G_program_filename + ': ' + msg)

                servicemanager.SetEventSourceName(G_service_name, True)
            except Exception as e:
                msg = 'Failed to SetEventSourceName: {}'.format(e)
                servicemanager.LogErrorMsg(G_program_filename + ': ' + msg)
                print(msg)
                pass

            try:
                msg = 'About to register event log messages'
                servicemanager.LogInfoMsg(G_program_filename + ': ' + msg)


                win32evtlogutil.AddSourceToRegistry(SERVICE_NAME_PREFIX + G_program_name,
                                                    msgDLL=G_program_directory + MESSAGE_FILE_DLL,
                                                    eventLogType="Application",
                                                    eventLogFlags=None)

                #RegisterEventLogMessage(SERVICE_NAME_PREFIX + G_program_name, G_program_directory)

                msg = 'Event log message registration appears to have succeeded'
                servicemanager.LogInfoMsg(G_program_filename + ': ' + msg)
            except Exception as e:
                msg = 'Failed to RegisterEventLogMessage: {}'.format(e)
                servicemanager.LogErrorMsg(G_program_filename + ': ' + msg)
                print(msg)
                pass

