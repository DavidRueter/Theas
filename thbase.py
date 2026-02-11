import datetime
import sys
import os
import ctypes
import gc
import asyncio
import functools

from pympler import asizeof, muppy, summary as mem_summary

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
G_all_done = None


def set_service_poll(service_poll):
    global G_service_poll
    G_service_poll = service_poll

def set_service_send_stop(service_send_stop):
    global G_service_send_stop
    G_service_send_stop = service_send_stop

def set_all_done(all_done):
    global G_all_done
    G_all_done = all_done

class TheasServerRunner():
    def __init__(self, shutdown_event=None):
        self.__is_running = False
        self.__is_starting = True
        self.__is_stopping = False
        self.shutdown_event = shutdown_event
        self.http_server = None

        self.loop = asyncio.new_event_loop()

    def __del__(self):
        self.__is_running= False

    @property
    def is_running(self):
        return self.__is_running

    @property
    def is_starting(self):
        return self.__is_starting

    @property
    def is_stopping(self):
        return self.__is_stopping

    @is_running.setter
    def is_running(self, running):
        try:
            # regardless of whether running is True or False we want to set is_starting to False
            if self.__is_starting:
                self.__is_starting = False

            if running:
                if not self.__is_running:
                    self.__is_running = running

            elif self.__is_running:
                self.__is_running = False
                #self.__stop_server()


        except Exception as e:
            log(None, 'TheasServerRunner', 'Exception in TheasServerRunner.is_running setter', e)
            self.__is_running = False

        if self.__is_running:
            log(None, 'TheasServerRunner', 'Server is running in TheasServerRunner.is_running setter')
        else:
            log(None, 'TheasServerRunner', 'Server is stopped in TheasServerRunner.is_running setter')


    def stop(self, service=None, reason='', skip_service_stop=False):
        log(None, 'Shutdown', '***Stop() called because: {}'.format(reason))
        self.write_winlog('Shutting Down: In thbase TheasServerRunner because {}'.format(reason))

        if not self.__is_stopping:
            self.__is_stopping = True


            loop = self.loop

            if loop is None or not loop.is_running():
                self.write_winlog('Shutting Down: PROBLEM loop is not running in TheasServerRunner.stop()')
                log(None, 'Shutdown', 'PROBLEM: loop is not running in TheasServerRunner.stop()')


            if loop and loop.is_running():
                loop.create_task(shutdown())
                log(None, 'Shutdown', '***create_task(shutdown()')


            if self.http_server is not None:
                self.http_server.stop()
                log(None, 'Shutdown', '***HTTP server stopped')

            if self.is_running:
                self.is_running = False

            if self.shutdown_event is not None:
                self.shutdown_event.set()


            if service is not None:
                global G_service
                G_service = None
                G_service = service

                log(None, 'Shutdown', '***About to call G_service_send_stop()')

                if G_service_send_stop is not None and not skip_service_stop:
                    G_service_send_stop()



            global G_all_done
            if G_all_done is not None:
                #callback to shut down theas
                G_all_done()
                log(None, 'Shutdown', 'G_all_done() completed')
                G_all_done = None

        log(None, 'Shutdown', '***stop() done')

        self.write_winlog('Shutting Down: Done with thbase TheasServerRunner.stop()')



    def start(self, shutdown_event=None, http_server=None, loop=None, reason=''):
        if shutdown_event is not None:
            self.shutdown_event = shutdown_event

        if http_server is not None:
            self.http_server = http_server

        if loop is not None:
            self.loop = loop

        log(None, 'TheasServerRunner', 'Start() called', reason)

        self.is_running = True
        self.state = 'running'


    def write_winlog(self, *args, is_error=False):
        # for convenience, wrap LogInfoMsg for logging outside the TheasServerSvc class

        import servicemanager  # See note above

        fnc = None
        if is_error:
            fnc = servicemanager.LogErrorMsg
        else:
            fnc = servicemanager.LogInfoMsg

        if len(args) >= 2:
            fnc(args[1])
        else:
            fnc(args[0])

G_server = None

def theas_server():
    global G_server
    if G_server is None:
        G_server = TheasServerRunner()

    return G_server


#https://www.pythontutorial.net/advanced-python/python-references/
#def ref_count(address):
    #return ctypes.c_long.from_address(address).value

#https://stackify.com/python-garbage-collection/
3#sys.getrefcount(a)


def collect_garbage():
    #https://www.geeksforgeeks.org/garbage-collection-python/
    # Returns the number of objects it has collected and deallocated
    # lists are cleared whenever a full collection or collection of the highest generation (2) is run
    gc.set_debug(gc.DEBUG_UNCOLLECTABLE |  gc.DEBUG_SAVEALL)
    return gc.collect()

def memory_report():
    all_objects = muppy.get_objects()

    buf = ''

    sum1 = mem_summary.summarize(all_objects)

    #mem_summary.print_(sum1)

    lines = []

    for el in sum1:
        typ, cnt, sz = el
        lines.append(''.join(('<tr><td>', typ, '</td><td>', str(cnt), '</td><td>', str(sz), '</td></tr>')))

    buf = buf.join(lines)
    buf = '<h3>Total memory used: {}</h3><br /><table>{}</table>'.format(len(all_objects), buf)

    collect_garbage()
    return buf

def log_memory(obj=None, label="", print_details=False):
    # see https://pythonhosted.org/Pympler/muppy.html and https://pythonhosted.org/Pympler/muppy.html#the-tracker-module

        if obj is None:
            all_objects = muppy.get_objects()
            log(None, 'Memory', 'Total memory used', '({})'.format(label) , len(all_objects))

            if print_details:
                sum1 = mem_summary.summarize(all_objects)
                mem_summary.print_(sum1)
        else:
            log(None, 'Memory', 'Memory used', '({})'.format(label) , asizeof.asizeof(obj))

async def stop_loop():
    loop = theas_server().loop
    loop.call_soon_threadsafe(loop.stop)

async def shutdown():
    theas_server().write_winlog('thbase.shutdown()')
    log(None, 'TheasServerRunner', '***shutdown() called')

    loop = theas_server().loop
    if loop is None or not loop.is_running():
        log(None, 'TheasServerRunner', 'PROBLEM in thbase.shutdown(): loop is not running')

    if loop is not None and loop.is_running():
        #await asyncio.sleep(1)

        tasks = [
            t
            for t
            in asyncio.all_tasks()
            if (
                t is not asyncio.current_task()
                and t._coro.__name__ != 'main'
            )
        ]

        theas_server().loop.call_soon_threadsafe(loop.stop)

        [task.cancel() for task in tasks]

        await asyncio.gather(*tasks)


        log(None, 'Shutdown', '***shutdown() is done with await asyncio.gather(*tasks)')

        # note:  the rest of this code may be unreachable, for when all the tasks are cancelled
        # the running asyncio.run(parallel(run_as_svc=run_as_svc)) in TheasServer.run(run_as_svc=False)
        # will be complete and execution will continue there.


        theas_server().write_winlog('Near end of thbase.shutdown()')

        #if shutdown_event is not None:
        #   await shutdown_event.wait()

        # server is done running
        if theas_server() is not None:
            theas_server().stop(reason='TheasServer.main() exiting')

        theas_server().loop.call_soon_threadsafe(loop.stop)


        global G_service_poll

        theas_server().write_winlog('thbase.shutdown() calling G_service_poll()')

        if G_service_poll is not None:
            G_service_poll()

        theas_server().write_winlog('Done with thbase.shutdown()')
        log(None, 'Shutdown', '*Done with thbase.shutdown()')

def set_service_name(service_name: str):
    theas_server().service_name = service_name


