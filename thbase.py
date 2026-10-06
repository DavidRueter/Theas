import logging
from logging.handlers import RotatingFileHandler
import sys
import os
import ctypes
import gc
import asyncio
import functools

from pympler import asizeof, muppy, summary as mem_summary

_logger = logging.getLogger('theas')

# Dedicated logger for the compact connection/auth lifecycle "trace slice".
# Its records go to logs/theas_trace.log only (propagate=False); trace() also
# mirrors each marker into _logger so it appears inline in the console/full log.
_trace_logger = logging.getLogger('theas.trace')

# Explicit, unambiguous timestamp so a reader can filter by a time window
# (e.g. "the last 120 seconds") without guessing the format.
_LOG_DATEFMT = '%Y-%m-%d %H:%M:%S'

# --- trace() gating -------------------------------------------------------
# Layer 1: master switch.  Set False for production builds to disable ALL
# trace() output globally.  Checked first in trace(), so it short-circuits
# everything else.
DEBUG_TRACE_ENABLED = False

# Layer 2: flat named groups.  Each trace() call may name one or more groups
# via trace_group='a;b' (';'-delimited).  Untagged calls belong to 'ungrouped'.
#   TRACE_ONLY_GROUPS -- focus mode: if non-empty, ONLY these groups emit
#                        (overrides the mute list).
#   TRACE_MUTE_GROUPS -- otherwise: every group emits EXCEPT these.
# Edit these two sets to centrally enable/disable trace points by group.
TRACE_ONLY_GROUPS = set()
TRACE_MUTE_GROUPS = set()


def _parse_trace_groups(trace_group):
    # Split ';'-delimited groups into a set; untagged -> the 'ungrouped' group
    # so the normal muting/focus controls still apply to it.
    groups = {g.strip() for g in trace_group.split(';') if g.strip()} if trace_group else set()
    return groups or {'ungrouped'}


def _trace_enabled(trace_group):
    groups = _parse_trace_groups(trace_group)
    if TRACE_ONLY_GROUPS:
        return bool(groups & TRACE_ONLY_GROUPS)   # focus overrides mute when set
    return not (groups & TRACE_MUTE_GROUPS)


def _log_dir(log_path=None):
    prog_dir, _ = get_program_directory()
    if log_path:
        # Configured via settings.cfg log_path.  Expand %ProgramData% etc.;
        # a relative path is taken as relative to the program directory.
        log_dir = os.path.expandvars(log_path)
        if not os.path.isabs(log_dir):
            log_dir = os.path.join(prog_dir, log_dir)
    else:
        log_dir = os.path.join(prog_dir, 'logs')
    log_dir = os.path.normpath(log_dir)
    os.makedirs(log_dir, exist_ok=True)
    return log_dir

# Local threshold for thbase.log() gating.  Semantics:
#   1  -> log everything (the default)
#   0  -> log nothing
#  <0  -> only log records whose severity is in [_LOG_THRESHOLD, -1]
#         (Theas convention: lower severity = more important)
_LOG_THRESHOLD = 1


def setup_logging():
    # Idempotently configure the 'theas' logger with a console handler so log()
    # can route through stdlib logging instead of print().  The local
    # _LOG_THRESHOLD gates output inside log().
    # Call once at application startup (e.g., from TheasServer.run()).  File
    # handlers are added later by setup_log_files(), once settings.cfg has been
    # read and the log directory is known.  To capture
    # Tornado's own logs through the same handlers, attach handlers to the root
    # logger instead and drop propagate=False.
    if not _logger.handlers:
        fmt = logging.Formatter('%(asctime)s %(message)s', datefmt=_LOG_DATEFMT)

        # Console (unchanged: this is what you watch in the PyCharm debugger).
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        _logger.addHandler(console)

        _logger.setLevel(logging.DEBUG)
        _logger.propagate = False

        # When running under TheasServerSvc, console output is not visible, and
        # the log files are not open yet.  Until setup_log_files() runs, also send
        # log() output to the Windows Event Log so early startup problems are seen.
        if getattr(theas_server(), 'service_name', None):
            _logger.addHandler(_startup_winlog_handler)


class _WinlogHandler(logging.Handler):
    # Forwards log records to write_winlog().  Used only during startup.
    def emit(self, record):
        try:
            write_winlog(record.getMessage())
        except Exception:
            self.handleError(record)


_startup_winlog_handler = _WinlogHandler()


def setup_log_files(log_path=None):
    # Add the rotating file handlers.  Call after settings.cfg has been read.
    # Returns the log directory used.  Raises if the directory or files cannot
    # be opened (e.g. no write permission); the caller decides how to report
    # that, and logging simply continues to the console.
    try:
        log_dir = _log_dir(log_path)

        if not any(isinstance(h, RotatingFileHandler) for h in _logger.handlers):
            # Full debug log: mirrors everything that goes to the console to a
            # rotating file so it can be tailed / reviewed after the fact.
            fmt = logging.Formatter('%(asctime)s %(message)s', datefmt=_LOG_DATEFMT)
            full_h = RotatingFileHandler(
                os.path.join(log_dir, 'theas_debug.log'),
                maxBytes=20_000_000, backupCount=5, encoding='utf-8')
            full_h.setFormatter(fmt)
            _logger.addHandler(full_h)

        if not _trace_logger.handlers:
            # Compact lifecycle trace slice: only the events emitted via trace().
            trace_fmt = logging.Formatter('%(asctime)s %(message)s', datefmt=_LOG_DATEFMT)
            trace_h = RotatingFileHandler(
                os.path.join(log_dir, 'theas_trace.log'),
                maxBytes=10_000_000, backupCount=5, encoding='utf-8')
            trace_h.setFormatter(trace_fmt)
            _trace_logger.addHandler(trace_h)
            _trace_logger.setLevel(logging.DEBUG)
            _trace_logger.propagate = False

        return log_dir

    finally:
        # Startup is over: stop forwarding the (chatty) log() stream to the Event Log,
        # whether or not the files could be opened.
        _logger.removeHandler(_startup_winlog_handler)


def _obj_ref(obj):
    # Short identity that is stable within a single run -- lets us tell whether
    # two requests are touching the SAME ThSession object or different ones.
    return hex(id(obj))[2:] if obj is not None else '-'


def trace(event, th_session=None, conn=None, trace_group=None, **fields):
    """OPTIONAL developer trace — safe to remove; has NO effect on behavior.

    trace() is pure observability: it writes a diagnostic marker to the trace
    log and does nothing else.  Any individual trace() call may be deleted or
    commented out, and ALL tracing can be turned off for production by setting
    thbase.DEBUG_TRACE_ENABLED = False -- none of this changes how Theas runs.

    It writes to logs/theas_trace.log (the clean lifecycle slice) and mirrors
    the marker inline into the main log/console for context.

    Parameters (only `event` jois required):
        event       -- short label for this trace point (e.g. 'get.start').
        th_session  -- ThSession whose session/conn context to include.
        conn        -- explicit Conn; pass at release sites where self.conn was
                       already set to None, so the conn id is still captured.
        trace_group -- ';'-delimited group name(s) for central enable/disable
                       via TRACE_ONLY_GROUPS / TRACE_MUTE_GROUPS.  Untagged
                       calls belong to the 'ungrouped' group.
        **fields    -- extra key=value pairs appended to the marker.
    """
    if not DEBUG_TRACE_ENABLED:
        return
    if not _trace_enabled(trace_group):
        return

    parts = ['evt=' + str(event)]
    if trace_group:
        parts.append('grp=' + str(trace_group))

    if th_session is not None:
        parts.append('sess=' + str(getattr(th_session, 'session_token', None)))
        parts.append('id_sess=' + _obj_ref(th_session))
        parts.append('logged_in=' + str(getattr(th_session, 'logged_in', None)))
        parts.append('req=' + str(getattr(th_session, 'request_count', None)))

    this_conn = conn
    if this_conn is None and th_session is not None:
        this_conn = getattr(th_session, 'conn', None)
    if this_conn is not None:
        parts.append('conn=' + str(getattr(this_conn, 'id', None)))
        parts.append('is_user_authed=' + str(getattr(this_conn, 'is_user_authed', None)))
        parts.append('is_public_authed=' + str(getattr(this_conn, 'is_public_authed', None)))

    for k, v in fields.items():
        parts.append('{}={}'.format(k, v))

    msg = 'TRACE ' + ' '.join(parts)
    try:
        _trace_logger.info(msg)
    except Exception:
        pass
    try:
        # Mirror into the main log so the marker is visible inline in the
        # console and full debug log alongside surrounding activity.
        _logger.info(msg)
    except Exception:
        pass

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
    # Single entry point for all Theas logging.  When a session is provided, its
    # context (session_key, request_count, comments) is included in the message.
    # ThSession.log() is a thin pass-through that calls back here, so the actual
    # formatting and writing happens in one place.
    if (_LOG_THRESHOLD  <= 0 or severity < _LOG_THRESHOLD):
        return

    msg_args = ' '.join(str(a) for a in args)

    if th_session is not None:
        if not getattr(th_session, 'log_current_request', True):
            return
        _logger.info(
            'ThSession [%s:%s] - %s (%s) %s',
            th_session.session_key,
            th_session.request_count,
            category,
            th_session.comments if th_session.comments is not None else '',
            msg_args,
        )
    else:
        _logger.info('ThSessions [%s] %s', category, msg_args)

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
        self.service_name = None  # set by TheasServerSvc via set_service_name()

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
                log(None, 'Shutdown', 'PROBLEM: loop is not running in TheasServerRunner.stop()')
                self.write_winlog('Shutting Down: PROBLEM loop is not running in TheasServerRunner.stop()')


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
        write_winlog(*args, is_error=is_error)

G_server = None

def theas_server():
    global G_server
    if G_server is None:
        G_server = TheasServerRunner()

    return G_server


def write_winlog(*args, is_error=False):
    # Write to the Windows Event Log when running under TheasServerSvc (service_name set);
    # otherwise (non-Windows, or TheasServer.py run directly) just print.  Never raises.
    msg = ' '.join(str(a) for a in args)
    service_name = getattr(theas_server(), 'service_name', None)
    if service_name:
        try:
            import servicemanager
            fnc = servicemanager.LogErrorMsg if is_error else servicemanager.LogInfoMsg
            fnc('{}: {}'.format(service_name, msg))
            return
        except Exception:
            pass

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
    log(None, 'TheasServerRunner', '***shutdown() called')
    theas_server().write_winlog('thbase.shutdown()')

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
        theas_server().write_winlog('Near end of thbase.shutdown()')

        # note:  the rest of this code may be unreachable, for when all the tasks are canceled
        # the running asyncio.run(parallel(run_as_svc=run_as_svc)) in TheasServer.run(run_as_svc=False)
        # will be complete and execution will continue there.


        #if shutdown_event is not None:
        #   await shutdown_event.wait()

        # server is done running
        if theas_server() is not None:
            theas_server().stop(reason='TheasServer.main() exiting')

        theas_server().loop.call_soon_threadsafe(loop.stop)


        if G_service_poll is not None:
            log(None, 'Shutdown', 'thbase.shutdown() calling G_service_poll()')
            theas_server().write_winlog('thbase.shutdown() calling G_service_poll()')

            G_service_poll()

        log(None, 'Shutdown', '*Done with thbase.shutdown()')
        theas_server().write_winlog('Done with thbase.shutdown()')

def set_service_name(service_name: str):
    theas_server().service_name = service_name


