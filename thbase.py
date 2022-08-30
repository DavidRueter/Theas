import datetime
import sys
import os
#from pympler import asizeof, muppy, summary

_LOGGING_LEVEL = 1

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

def format_error(e):
    err_msg = ''
    err_msg_dblib = ''
    err_msg_friendly = ''
    err_msg_template = ''


    if isinstance(e, str):
        err_msg = e
    else:
        err_msg = e.text.decode('ascii')

    p = err_msg.find('DB-Lib error')
    if p >= 0:
        # The error message from pymmsql (annoyingly) appends:
        # DB-Lib error message 20018, severity 16: General SQL Server error: Check messages from the SQL Server
        # Strip that out.
        err_msg_dblib = err_msg[p:]
        err_msg = err_msg[:p]

    # By convention, if err_msg contains a pipe character | we take the first part of this message
    # to be the "technical" message, and the second part to be the "friendly" message, suitable for
    # display to an end user.

    # Additionally, a second pipe character | may be present, marking the end of the "friendly"message,
    # after which is a flag 1 or 0 to indicate whether the "technical" message should be displayed.

    err_msgs = err_msg.split('|')

    err_msg_tech = ''
    err_msg_friendly = ''
    err_msg_showtech = '1'
    err_msg_title = ''

    if len(err_msgs) == 1:
        err_msg_tech = err_msg
        err_msg_showtech = '1'
    else:
        err_msg_tech = err_msgs[0]
        err_msg_friendly = err_msgs[1]

    if len(err_msgs) > 2:
        err_msg_showtech = '1' if err_msgs[2] == '1' else '0'

    if len(err_msgs) > 3:
        err_msg_title = err_msgs[3]

    err_msg = ''

    err_msg_storedproc = None
    if hasattr(e, 'procname'):
        err_msg_storedproc = e.procname.decode('ascii')

        err_msg_tech += \
            ('Exception type ' + type(e).__name__ + '\n') if type(e).__name__ != 'str' else '' + \
             'Stored procedure ' + err_msg_storedproc if err_msg_storedproc is not None else '' + \
             (' error ' + e.number) if hasattr(e, 'number') else '' + \
             (' at line ' + e.line) if hasattr(e, 'line') else ''

    include_dblib_error = False

    if include_dblib_error:
        err_msg_tech = err_msg_tech + '\n' + err_msg_dblib

    err_msg = '{}|{}|{}|{}'.format(err_msg_tech, err_msg_friendly, err_msg_showtech, err_msg_title)

    return err_msg

def log(th_session, category, *args, severity=10000):
    if th_session is not None:
        th_session.log(category, *args, severity=severity)
    else:
        #ThSession.cls_log(category, *args, severity=severity)
        if _LOGGING_LEVEL == 1 or 0 > severity >= _LOGGING_LEVEL:
            print(datetime.datetime.now(), 'ThSessions [{}]'.format(category), *args)

def log_memory(obj=None, label="", print_details=False):
    # see https://pythonhosted.org/Pympler/muppy.html and https://pythonhosted.org/Pympler/muppy.html#the-tracker-module

    if False:
        if obj is None:
            all_objects = muppy.get_objects()
            log(None, 'Memory', 'Total memory used', '({})'.format(label) , len(all_objects))

            if print_details:
                sum1 = summary.summarize(all_objects)
                summary.print_(sum1)
        else:
            log(None, 'Memory', 'Memory used', '({})'.format(label) , asizeof.asizeof(obj))



class TheasServerError(BaseException):
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)

class TheasServerSQLError(TheasServerError):
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)


G_service_poll = None
G_service_send_stop = None

def set_service_poll(service_poll):
    global G_service_poll
    G_service_poll = service_poll

def set_service_send_stop(service_send_stop):
    global G_service_send_stop
    G_service_send_stop = service_send_stop


class TheasServerIsRunning():
    def __init__(self, shutdown_event=None):
        self.__is_running = False
        self.shutdown_event = shutdown_event

    def __del__(self):
        self.__is_running= False

    def __stop_server(self):

        try:
            log(None, 'TheasServerIsRunning', '__stop_server() called')

            if self.shutdown_event is not None:
                log(None, 'TheasServerIsRunning', 'in __stop_server()', 'shutdown_event is found')
                try:
                    self.shutdown_event.set()
                except Exception as e:
                    log(None, 'TheasServerIsRunning', 'in __stop_server()', 'Error calling shutdown_event.set()', e)
            else:
                log(None, 'TheasServerIsRunning', 'in __stop_server()', 'shutdown_event is NOT found')


            '''
            loop = None
            try:
                loop = asyncio.get_running_loop()
            except:
                loop = None

            if loop is not None:
                if loop.is_running():
                    log(None, 'TheasServerIsRunning', 'in __stop_server()', 'loop is running')
                    if self.shutdown_event is not None:
                        self.shutdown_event.set()
                    else:
                        loop.stop()
            else:
                log(None, 'TheasServerIsRunning', 'in __stop_server()', 'loop is NOT found')


            global G_service
            if G_service is not None:
                log(None, 'TheasServerIsRunning', 'in __stop_server() found G_service')
                G_service.SvcStop()
            else:
                log(None, 'TheasServerIsRunning', 'in __stop_server() G_service is not set')
                
            '''


        except Exception as e:
            log(None, 'TheasServerIsRunning', 'ERROR in __stop_server', e)

    @property
    def is_running(self):
        return self.__is_running

    @is_running.setter
    def is_running(self, running):
        try:
            if running:
                if not self.__is_running:
                    self.__is_running = running

            elif self.__is_running:
                self.__is_running = False
                self.__stop_server()


        except Exception as e:
            log(None, 'TheasServerIsRunning', 'Exception in TheasServerIsRunning.is_running setter', e)
            self.__is_running = False

        if self.__is_running:
            log(None, 'TheasServerIsRunning', 'Server is running in TheasServerIsRunning.is_running setter')
        else:
            log(None, 'TheasServerIsRunning', 'Server is stopped in TheasServerIsRunning.is_running setter')

    def stop(self, shutdown_event=None, service=None, reason='', skip_service_stop=False):
        if shutdown_event is not None:
            self.shutdown_event = shutdown_event

        if service is not None:
            global G_service
            G_service = None
            #G_service = service

        log(None, 'TheasServerIsRunning', 'Stop() called', reason)

        if G_service_send_stop is not None and not skip_service_stop:
            G_service_send_stop()

        self.is_running = False


    def start(self, shutdown_event=None, reason=''):
        if shutdown_event is not None:
            self.shutdown_event = shutdown_event

        log(None, 'TheasServerIsRunning', 'Start() called', reason)

        self.is_running = True



G_server = TheasServerIsRunning()