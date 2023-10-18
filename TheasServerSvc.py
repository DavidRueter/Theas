#!/usr/bin/env python
import asyncio
import sys

import win32event
import win32service
import win32serviceutil

# Needed for some services that require overlapped io.  see:
# https://learning.oreilly.com/library/view/python-programming-on/1565926218/ch18s06.html
#import pywintypes

import win32evtlogutil
import os

# Needed if RegisterEventLogMessage is used
#import winreg

import thbase
import TheasServer


__author__ = 'DavidRueter'
'''
Theas web application server Windows service wrapper.

Author: David Rueter (drueter@assyst.com)
Date: 5/9/2016, last modified 8/30/2022
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
    
    Also chapter 18 of Python Programming On Win32 by Andy Robinson, Mark Hammond is helpful
    https://learning.oreilly.com/library/view/python-programming-on/1565926218/ch18s06.html
    
'''


# The name of the service will be SERVICE_NAME_PREFIX + _ + program_filename
# For example, if the .exe is named MyApp.exe, the service would be Theas_MyApp
SERVICE_NAME_PREFIX = 'Theas'

# The .dll for Windows Event Manager messages
MESSAGE_FILE_DLL = 'TheasMessages.dll'
# see: https://www.eventsentry.com/blog/2010/11/creating-your-very-own-event-m.html


# Declare some globals
G_program_directory, G_program_filename = thbase.get_program_directory()
G_program_name, G_extension = os.path.splitext(G_program_filename)
G_service_name = SERVICE_NAME_PREFIX + '_' + G_program_name

G_message_file = G_program_directory + '\\\\' + 'TheasMessages.dll'

G_current_service = None # set by TheasServerSvc.__init__


def write_winlog(*args, is_error=False):
    # for convenience, wrap LogInfoMsg for logging outside the TheasServerSvc class

    import servicemanager # See note above

    fnc = None
    if is_error:
        fnc = servicemanager.LogErrorMsg
    else:
        fnc = servicemanager.LogInfoMsg

    if len(args) >= 2:
        fnc(G_service_name + ': ' + args[1])
    else:
        fnc(G_service_name + ': ' + args[0])

    #servicemanager.LogMsg(
    #    servicemanager.EVENTLOG_INFORMATION_TYPE,
    #    servicemanager.PYS_SERVICE_STARTED,
    #    (G_service_name,
    #     '')
    #)

def _main():
    # Declaring _main() as a module-level function provides a number of benefits that
    # we would not have if this same code were implemented within the TheasServerSvc class.

    # 1) allows runtime generation of the service name based on the EXE name, etc.
    # 2) allows this module to be run as the __main__ module which simplifies launching from
    #    the pycharm debugger

    # Note:  we assume that the class declaration of TheasServerSvc
    # will populate the global variables (G_program_filename, etc.)
    # Those must be populated before anything else happens (i.e. we
    # can't change these values in initialize, etc.)


    import servicemanager

    write_winlog('Starting service _main()')

    run_service = False
    debug_service = False

    TSS = TheasServerSvc

    # if the .exe is run without arguments, default to run as a service
    if len(sys.argv) == 1:
        run_service = True
        # if the .exe is run without arguments, run the service
    else:
        # peek to see if there is a parameter /service to explicitly tell us to run as a service
        for arg in sys.argv:
            if arg.lower() == 'service':
                run_service = True
            if arg.lower() == 'debug':
                debug_service = True

    if run_service:
        write_winlog('Running as service in _main()')

        try:

            import win32traceutil
            # to help with error handling.
            # See: http://python.6.x6.nabble.com/Running-a-Windows-Python-service-without-pythonservice-exe-tp1956976p1956982.html

            servicemanager.Initialize(G_service_name, G_program_directory + MESSAGE_FILE_DLL)
                # note:  explicitly provide G_service_name so that the service is named according to the
                # current .exe filename (even if it is renamed) instead of the class name.

            servicemanager.PrepareToHostSingle(TSS)
                # note:  "single" means that this .exe will host a single service.
                # The TheasServerSvc class we declared above will be what the Windows Service Manager
                # uses to control the service.

            servicemanager.StartServiceCtrlDispatcher()

        except (SystemExit, KeyboardInterrupt) as e:
            write_winlog('KeyboardInterrupt received when running as service in _main()', is_error=True)
            raise
        except Exception as e:
            msg = 'Error while trying to start service {} {}'.format(G_service_name, e)
            write_winlog(msg, is_error=True)

    elif debug_service:
        # handled explicitly here rather than relying on HandleCommandLine (called below)
        # to facilitate running within the pycharm debugger
        write_winlog('Running as debug service in _main()')
        win32serviceutil.DebugService(TSS, (G_program_filename, 'debug'))

    else:
        try:
            write_winlog('Processing command line in _main()')

            # process the service-related command line parameters
            win32serviceutil.HandleCommandLine(TSS)
            pass
        except Exception as e:
            msg = 'Error while calling HandleCommandLine: {}'.format(e)
            write_winlog('Error processing command line in _main() {}'.format(e), is_error=True)


        if len(sys.argv) > 1:
            msg = 'Service ' + sys.argv[1] + ' performed.'
            write_winlog(msg)

        # do additional work needed after installation (i.e. register event message file)
        if len(sys.argv) > 1 and sys.argv[1] in ("install", "update"):

            try:
                servicemanager.SetEventSourceName(G_service_name, True)
            except Exception as e:
                msg = 'Failed to SetEventSourceName: {}'.format(e)
                write_winlog('Error Processing install or update from command line in _main() {}'.format(e), is_error=True)
                sys.exit(1)

            try:
                win32evtlogutil.AddSourceToRegistry(SERVICE_NAME_PREFIX + G_program_name,
                                                    msgDLL=G_program_directory + MESSAGE_FILE_DLL,
                                                    eventLogType="Application",
                                                    eventLogFlags=None)

                # note:  At one time I was having problems with win32evtlogutil.AddSourceToRegistry and so
                # I wrote my own function RegisterEventLogMessage which is preserved below in a doc string
                # for reference.  It is no longer needed at present, but be could be called instead:
                # RegisterEventLogMessage(SERVICE_NAME_PREFIX + G_program_name, G_program_directory)


            except Exception as e:
                msg = 'Failed to RegisterEventLogMessage: {}'.format(e)
                write_winlog('Error calling AddSourceToRegistry to add event source in _main() {} but continuing to run'.format(e), is_error=True)
                #sys.exit(1)

'''
# Works, but no longer needed now that I am able to make use of win32evtlogutil.AddSourceToRegistry
def RegisterEventLogMessage(program_name='', program_directory='', message_file=''):

    global G_program_name
    global G_program_directory
    global G_message_file

    if not program_name:
        program_name = G_program_name

    if not program_directory:
        program_directory = G_program_directory

    if not message_file:
        message_file = G_message_file

    key = None

    key_val = 'SYSTEM\\CurrentControlSet\\Services\\EventLog\\Application\\' + program_name

    write_winlog('Trying to register {} as the event message file'.format(message_file))

    try:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_val, 0, winreg.KEY_ALL_ACCESS)
            orig_key_value, this_type = winreg.QueryValueEx(key, 'EventMessageFile')
            if orig_key_value != message_file:
                winreg.SetValueEx(key, 'EventMessageFile', None, winreg.REG_SZ, message_file)
                winreg.SetValueEx(key, 'TypesSupported', None, winreg.REG_DWORD, 7)
        except Exception as e2:
            print(e2)
            key = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, key_val)
            winreg.SetValueEx(key, 'EventMessageFile', None, winreg.REG_SZ, message_file)
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
        self.timeout = 1

        win32serviceutil.ServiceFramework.__init__(self, args)
        # Create an event which we will use to wait on.
        # The "service stop" request will set this event.
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)


        # Save this service object to G_current_service for convenience
        global G_current_service#.set_service(self)        global G_current_service
        G_current_service = self

    def GetAcceptedControls(self):
        result = win32serviceutil.ServiceFramework.GetAcceptedControls(self)
        result |= win32service.SERVICE_ACCEPT_PRESHUTDOWN
        return result

    def SvcDoRun(self):
        # ---------------------------------------------------------------------
        # Make entry in the event log that this service started
        # ---------------------------------------------------------------------

        write_winlog('Service SvcDoRun() was called')

        # Start the TheasServer server.  It will run an event loop until the service is stopped and
        # TheasServer.StopServer() is called.

        # In this way, the asyncio loop does the looping for the life of the service (instead of the
        # "while true" loop shown in most examples.
        TheasServer.run(run_as_svc=True)


    def SvcOtherEx(self, control, event_type, data):

        # See the MSDN documentation for "HandlerEx callback" for a list
        # of control codes that a service can respond to.
        #
        # We respond to `SERVICE_CONTROL_PRESHUTDOWN` instead of
        # `SERVICE_CONTROL_SHUTDOWN` since it seems that we can't log
        # info messages when handling the latter.

        write_winlog('Starting SvcOtherEx()')

        if control == win32service.SERVICE_CONTROL_PRESHUTDOWN:
            write_winlog('Service received a pre-shutdown notification in SvcOtherEx')
            self.SvcStop()
        else:
            write_winlog('Service received an event in SvcOtherEx: code={}, type={}, data={}'.
                         format(control, event_type, data))
            pass

    def onServicePoll(self):

        # Does the work that is normally done inside an infinite loop in SvcDoRum()
        # This allows an external caller (i.e. our Theas server) to have the service
        # poll for a stop signal as part of a periodic task in the asyncio eloop.

        # Wait for service stop signal.  If I timeout, loop again
        rc = win32event.WaitForSingleObject(self.hWaitStop, self.timeout)

        # Check to see if self.hWaitStop happened
        if rc == win32event.WAIT_OBJECT_0:
            # Stop signal encountered
            write_winlog('Service stop signal was encountered in onServicePoll(). Stopping.')

        else:
            write_winlog('Service is Alive and well in onServicePoll')
            pass

    def SvcStop(self):
        write_winlog('Service SvcStop() was called.')

        # Before we do anything, tell the SCM we are starting the stop process.
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        # And set my event.
        win32event.SetEvent(self.hWaitStop)

        # Tell the TheasServer event loop to stop
        thbase.G_server.stop(service=self, reason='Service SvcStop()')

def service_poll():
    global G_current_service
    if G_current_service is not None:
        G_current_service.onServicePoll()
def service_send_stop():
    global G_current_service
    if G_current_service is not None and G_current_service.hWaitStop is not None:
        write_winlog('service_send_stop() called')
        win32event.SetEvent(G_current_service.hWaitStop)

thbase.set_service_poll(service_poll)
thbase.set_service_send_stop(service_send_stop)

if __name__ == '__main__':
    _main()