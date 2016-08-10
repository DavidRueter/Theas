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

'''

SERVICE_NAME_PREFIX = 'Theas'

import win32serviceutil
import win32service

import os
import sys
import time

from win32 import servicemanager


import win32api
import win32event

import TheasServer
import winreg


def write_winlog(*args):
    if len(args) >= 2:
        servicemanager.LogInfoMsg(args[1])
    else:
        servicemanager.LogInfoMsg(args[0])



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



def RegisterEventLogMessage(program_name, program_directory):
    key = None

    key_val = 'SYSTEM\\CurrentControlSet\\Services\\EventLog\\Application\\Theas_' + program_name
    orig_key_value = ''

    try:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_val, 0, winreg.KEY_ALL_ACCESS)
            orig_key_value, this_type = winreg.QueryValueEx(key, 'EventMessageFile')
            if orig_key_value != 'TheasMessages.dll':
                winreg.SetValueEx(key, 'EventMessageFile', None, winreg.REG_SZ, program_directory + 'TheasMessages.dll')
                winreg.SetValueEx(key, 'TypesSupported', None, winreg.REG_DWORD, 7)
        except Exception as e:
            print(e)
            key = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, key_val)
            winreg.SetValueEx(key, 'EventMessageFile', None, winreg.REG_SZ, program_directory + 'TheasMessages.dll')
            winreg.SetValueEx(key, 'TypesSupported', None, winreg.REG_DWORD, 7)
    finally:
        try:
            winreg.CloseKey(key)
        finally:
            pass



class TheasServerSvc(win32serviceutil.ServiceFramework):
    #_svc_name_ = 'DefaultServiceName'
    #_svc_display_name_ = 'Default Service Display Name'
    #_svc_description_ = 'Default Service Description'

    _exe_name = sys.argv[0]
    p = _exe_name.rfind('\\')
    if p > 0:
        _exe_name = _exe_name[p + 1:]

    p = _exe_name.find('.')
    if p > 0:
        _exe_name = _exe_name[0 : p]

    _svc_name_ = SERVICE_NAME_PREFIX + '_' + _exe_name
    _svc_display_name_ = SERVICE_NAME_PREFIX + '_' + _exe_name
    _svc_description_ = SERVICE_NAME_PREFIX + ' web application server. See: https://github.com/davidrueter/Theas'


    def RegisterEventLogMessage(self, message_dll='TheasMessages.dll', prefix='Theas_'):
        program_directory, program_filename = get_program_directory()
        program_name, extension = os.path.splitext(program_filename)
        key = None

        key_val = 'SYSTEM\\CurrentControlSet\\Services\\EventLog\\Application\\' + prefix + program_name
        orig_key_value = ''

        try:
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_val, 0, winreg.KEY_ALL_ACCESS)
                orig_key_value, this_type = winreg.QueryValueEx(key, 'EventMessageFile')
                if orig_key_value != message_dll:
                    winreg.SetValueEx(key, 'EventMessageFile', None, winreg.REG_SZ, program_directory + message_dll)
                    winreg.SetValueEx(key, 'TypesSupported', None, winreg.REG_DWORD, 7)
            except:
                try:
                    key = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, key_val)
                    winreg.SetValueEx(key, 'EventMessageFile', None, winreg.REG_SZ, program_directory + message_dll)
                    winreg.SetValueEx(key, 'TypesSupported', None, winreg.REG_DWORD, 7)
                except:
                    pass
        finally:
            try:
                winreg.CloseKey(key)
            finally:
                pass


    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)


    def onServicePoll(self):
        # Wait for service stop signal, if I timeout, loop again
        rc = win32event.WaitForSingleObject(self.hWaitStop, self.timeout)

        # Check to see if self.hWaitStop happened
        if rc == win32event.WAIT_OBJECT_0:
            # Stop signal encountered
            servicemanager.LogInfoMsg(self._svc_name_ + ' - STOPPING')
        else:
            servicemanager.LogInfoMsg(self._svc_name_ + ' - is alive and well')

    def SvcStop(self):
        #import servicemanager
        #ask TheasServer server to stop
        TheasServer.StopServer()

        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)

        #servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE, servicemanager.PYS_SERVICE_STOPPED, (self._svc_name_, ''))

    def SvcDoRun(self):

        try:
            msg = 'Trying to register event log messages'
            write_winlog(msg)
            self.RegisterEventLogMessage()
        except Exception as e:
            msg = 'Theas app: Failed to RegisterEventLogMessage: {}'.format(e)
            write_winlog(msg)
            pass

        #import servicemanager
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE, servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ''))

        self.timeout = 3000

        #start TheasServer server
        TheasServer.run()


def ctrlHandler(ctrlType):
    return True


if __name__ == '__main__':
    #RegisterEventLogMessage()
    win32api.SetConsoleCtrlHandler(ctrlHandler, True)
    win32serviceutil.HandleCommandLine(TheasServerSvc)