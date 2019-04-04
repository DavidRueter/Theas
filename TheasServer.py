#!usr/bin/python

import sys
import os
import platform
import datetime
import threading
import time
import signal
import uuid
import binascii
import traceback
import string
import json

import tornado.web
import tornado.websocket
import tornado.ioloop
import tornado.options
import tornado.httpserver


from multiprocessing import Lock
from concurrent.futures import ThreadPoolExecutor
from tornado.concurrent import run_on_executor

import theas

import _mssql

import logging

import TheasCustom
import urllib.parse as urlparse

if platform.system() == 'Windows':
    from TheasServerSvc import write_winlog
else:
    def write_winlog(*args):
        if len(args) >= 2:
            print(args[1])
        else:
            print(args[0])


# import asyncio
# import msvcrt
# import mimetypes
# import os
# import time
# import gc
# from pympler import muppy, summary

# from jinja2 import Template, Untornado.options.defined
# from jinja2.environment import Environment
# from tornado.stack_context import ExceptionStackContext
# import contextlib
# import decimal
# import pymssql
# from tornado import gen, concurrent, ioloop

# from multiprocessing import Process, Lock
# from tornado.options import tornado.options.define, options
# import tornado.options
__author__ = 'DavidRueter'
"""
 Theas web application server.

 Author: David Rueter (drueter@assyst.com)
 Date: 5/9/2016
 Description : Wrapper to run TheasServer web server as a Windows service.
 Home:  https://github.com/davidrueter/Theas

 Usage : TheasServerSvc.exe

 See settings.cfg for additional options.  Options may be set in the config file, or may be passed in
 on the command line.

 It is recommended that you rename the TheasServerSvc.exe to something specific to your application.

 May be run as a Windows service. See TheasServerSvc.py and setup.py for more information.
"""
# @contextlib.contextmanager
# def catch_async_exceptions(type, value, traceback):
#    try:
#        print('ERROR: ' + str(value.args[0][1]))
#        #yield
#    except Exception:
#        print('ERROR: ' + str(value.args[0][1]))

THEAS_VERSION = '0.90.1.50'  # from version.cfg

SESSION_MAX_IDLE = 60  # Max idle time (in minutes) before TheasServer session is terminated
REMOVE_EXPIRED_THREAD_SLEEP = 60  # Seconds to sleep in between polls in background thread to check for expired sessions, 0 to disable
LOGGING_LEVEL = 1  # Enable all logging.  0 to disable all, other value to specify threshold.
LOGIN_RESOURCE_CODE = 'login'
LOGIN_AUTO_USER_TOKEN = None
DEFAULT_RESOURCE_CODE = None

FULL_SQL_IS_OK_CHECK = False

USE_WORKER_THREADS = False
MAX_WORKERS = 30
USE_SESSION_COOKIE = True
REMEMBER_USER_TOKEN = False
FORCE_REDIR_AFTER_POST = True

USE_SECURE_COOKIES = True
SESSION_HEADER_NAME = 'X-Theas-Sesstoken'
SESSION_COOKIE_NAME = 'theas:th:ST'
USER_COOKIE_NAME = 'theas:th:UserToken'

COOKIE_SECRET = 'tF7nGhE6nIcPMTvGPHlbAk5NIoCOrKnlHIfPQyej6Ay='

# NOTE:
# 1) This is the maximum number of threads per thread pool, not for the whole application.  In practice each
#    class that uses background threads via the @run_on_executor decorator has its own thread pool.  Thus the
#    total number of threads in the application will be {number of classes} x MAX_WORKERS (plus any other threads
#    used by the application).
# 2) Counter-intuitively, idle threads are not reused until MAX_WORKERS threads have been created.  For example,
#    suppose MAX_WORKERS = 30.  When the application is started and the first request comes in, a new thread
#    would be created.  The request is completed, the thread is idle.  Then a second request comes in.  A thread
#    would still be created (now two thread), and so on, until all 30 threads in the pool were created.  See
#    Tornado's module thread.py, class ThreadPoolExecutor._adjust_thread_count, and in particular, this comment:
#        # TODO(bquinlan): Should avoid creating new threads if there are more
#        # idle threads than items in the work queue.


G_sessions = None  # Global list of sessions
G_cached_resources = None  # Global list of cached resources
G_program_options = None
G_server_is_running = False
G_break_handler = None


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

class BreakHandler:
    """
    Trap CTRL-C, set a flag, and keep going.  This is very useful for
    gracefully exiting database loops while simulating transactions.

    To use this, make an instance and then enable it.  You can check
    whether a break was trapped using the trapped property.

    # Create and enable a break handler.
    ih = BreakHandler()
    ih.enable()
    for x in big_set:
        complex_operation_1()
        complex_operation_2()
        complex_operation_3()
        # Check whether there was a break.
        if ih.trapped:
            # Stop the loop.
            break
    ih.disable()
    # Back to usual operation...

    from:  http://stacyprowell.com/blog/2009/03/trapping-ctrlc-in-python/

    Also, consider:
            # see: https://docs.microsoft.com/en-us/windows/console/registering-a-control-handler-function
            import win32api

            def ctrlHandler(ctrlType):
                return True

            win32api.SetConsoleCtrlHandler(ctrlHandler, True)
    """

    def __init__(self, emphatic=9):
        """
        Create a new break handler.

        @param emphatic: This is the number of times that the user must
                    press break to *disable* the handler.  If you press
                    break this number of times, the handler is automagically
                    disabled, and one more break will trigger an old
                    style keyboard interrupt.  The default is nine.  This
                    is a Good Idea, since if you happen to lose your
                    connection to the handler you can *still* disable it.
        """
        self._count = 0
        self._enabled = False
        self._emphatic = emphatic
        self._oldhandler = None
        return

    def _reset(self):
        """
        Reset the trapped status and count.  You should not need to use this
        directly; instead you can disable the handler and then re-enable it.
        This is better, in case someone presses CTRL-C during this operation.
        """
        self._count = 0
        return

    def enable(self):
        """
        Enable trapping of the break.  This action also resets the
        handler count and trapped properties.
        """
        if not self._enabled:
            self._reset()
            self._enabled = True
            self._oldhandler = signal.signal(signal.SIGINT, self)
        return

    def disable(self):
        """
        Disable trapping the break.  You can check whether a break
        was trapped using the count and trapped properties.
        """
        if self._enabled:
            self._enabled = False
            signal.signal(signal.SIGINT, self._oldhandler)
            self._oldhandler = None
        return

    def __call__(self, signame, sf):
        """
        An break just occurred.  Save information about it and keep
        going.
        """
        self._count += 1

        print('Ctrl-C Pressed (caught by BreakHandler)')

        # If we've exceeded the "emphatic" count disable this handler.
        if self._count >= self._emphatic:
            self.disable()
        return

    def __del__(self):
        """
        Python is reclaiming this object, so make sure we are disabled.
        """
        self.disable()
        return

    @property
    def count(self):
        """
        The number of breaks trapped.
        """
        return self._count

    @property
    def trapped(self):
        """
        Whether a break was trapped.
        """
        return self._count > 0


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


def StopServer():
    global G_server_is_running

    G_server_is_running = False

    msg = 'StopServer() called'
    ThSession.cls_log('Shutdown', msg)
    write_winlog(msg)

    # this_ioloop = tornado.ioloop.IOLoop.current()
    # this_ioloop.add_callback(this_ioloop.stop)


# def set_exit_handler(func):
#    signal.signal(signal.SIGTERM, func)


# def on_exit(signum, frame):
#    ThSession.cls_log('Shutdown', 'on_exit() called')
#    StopServer()


def do_periodic_callback():
    global G_server_is_running
    global G_break_handler

    # Called by Tornado once a second.
    # ThSession.cls_log('Periodic', 'do_periodic_callback() called')

    if G_break_handler and G_break_handler.trapped:
        # Ctrl-C pressed
        G_server_is_running = False

    # if msvcrt.kbhit():
    #    # Any key pressed
    #    G_server_is_running = False

    if not G_server_is_running:
        ThSession.cls_log('Periodic', 'Trying to stop IOLoop.instance()')

        this_ioloop = tornado.ioloop.IOLoop.current()
        this_ioloop.add_callback(this_ioloop.stop)

        # tornado.ioloop.IOLoop.current().stop()
        # tornado.ioloop.IOLoop.instance().stop()
        # tornado.ioloop.IOLoop.instance().add_callback(tornado.ioloop.IOLoop.instance().stop)


class ThStoredProc:
    """#Class ThStoredProc is a helper class that wraps _mssql.MSSQLStoredProcedure.

    This allows us to conveniently use the session's SQL connection and to perform session-focused logging.

    ThStoredProc also provides parameter sniffing, to simplify working with arbitrary stored procedures
    without hard-coding parameter names.

    In the future we may want to consider moving theas parameter passing (to the stored procedure) and
    updating (for parameters returned by the stored procedure) to ThsStoredProc.  (At this point theas
    parameters are managed exclusively in ThSession.)
    """

    @property
    def is_ok(self):
        if not FULL_SQL_IS_OK_CHECK:
            return True
        else:
            self.th_session.log('StoredProc', 'Checking is_ok:', self.stored_proc_name)
            result = self._storedproc is not None and self.connection is not None and self.connection.connected

            if result and FULL_SQL_IS_OK_CHECK:
                try:
                    self.connection.execute_non_query('SELECT 1 AS IsOK')
                except:
                    result = False

            if not result:
                self.th_session.logged_in = False
                self.th_session.sql_conn = None
            return result

    def __init__(self, this_stored_proc_name, this_th_session):
        self._connection = None
        self._storedproc = None
        self.th_session = None
        self.stored_proc_name = None
        self.parameter_list = {}  # sniffed parameters.  See parameters for bound parameters.
        self.resultset = []

        self.stored_proc_name = this_stored_proc_name

        # Use provided session.
        self.th_session = this_th_session
        self._connection = this_th_session.sql_conn

        self.th_session.log('StoredProc', 'Initializing ThStoredProc:', self.stored_proc_name)

        if self.th_session.sql_conn is None or not self.th_session.sql_conn.connected:
            self.th_session.log('StoredProc', 'New connection', self.stored_proc_name)
            self.th_session.init_session()

        if self.th_session.sql_conn is not None and self.th_session.sql_conn.connected and self.stored_proc_name:
            self.th_session.log('StoredProc', 'Existing connection:', self.stored_proc_name)
            self._storedproc = self.th_session.sql_conn.init_procedure(self.stored_proc_name)
        else:
            self._storedproc = None

    def __del__(self):
        self._storedproc = None
        del self._storedproc

        self.th_session = None
        del self.th_session

    def refresh_parameter_list(self):
        self.th_session.log('StoredProc', 'Refreshing parameter list:', self.stored_proc_name)

        if self.parameter_list is not None:
            self.parameter_list = {}
        if self.stored_proc_name is not None and self.th_session is not None and self.th_session.sql_conn is not None and self.th_session.sql_conn.connected:
            try:
                self.th_session.sql_conn.execute_query(
                    'EXEC theas.sputilGetParamNames @ObjectName = \'{}\''.format(self.stored_proc_name))
                resultset = [row for row in self.th_session.sql_conn]
                for row in resultset:
                    this_param_info = {}
                    this_param_info['is_output'] = row['is_output']
                    self.parameter_list[row['ParameterName']] = this_param_info
                    # self.parameter_list.append(row['ParameterName'])

            except Exception as e:
                self.th_session.log('Sessions', '***Error accessing SQL connection', e)
                self.th_session.sql_conn = None
                self.parameter_list = None
                # self.th_session.init_session(force_init=True)
                # self._storedproc = self.th_session.sql_conn.init_procedure(self.stored_proc_name)
                # self.th_session.authenticate(username=None, password=None) #ToDo: need to think about this.  Can we safely re-authenticate?
                # self.th_session.log('Sessions', '***Cannot automatically log in after failed SQL connection', e.message)
                raise

    def execute(self, fetch_rows=True):
        self.th_session.comments = 'ThStoredProc.execute'
        self.th_session.log('StoredProc', 'Executing:', self.stored_proc_name)

        _mssql.min_error_severity = 1
        this_result = False

        if self.is_ok:
            self.th_session.do_on_sql_start(self)
            try:

                # pymssql and/or FreeTDS have a number of limitations.
                # a) They do not seem to support output parameters
                # b) They truncate input parameters at 8000 characters

                # To work around b), we must not use _storedproc.execute, and must instead build our own
                # SQL query to execute.

                # this_result = self._storedproc.execute(*args, **kwargs)

                this_sql = 'EXEC ' + self.stored_proc_name

                for this_name, this_value in self.parameters.items():
                    if isinstance(this_name, str) and this_name.startswith('@'):
                        this_sql += ' ' + this_name + '='
                        this_sql += 'NULL' if this_value is None else '\'' + str(this_value) + '\''
                        this_sql += ', '

                if this_sql.endswith(', '):
                    this_sql = this_sql[:-2]

                self.th_session.sql_conn.execute_query(this_sql)

                if fetch_rows:
                    self.resultset = [row for row in self.th_session.sql_conn]
                self.th_session.do_on_sql_done(self)
                this_result = True
            except Exception as e:
                if LOGGING_LEVEL:
                    print(e)
                raise e

        self.th_session.comments = None

        return this_result

    # def bind(self, *args, **kwargs):
    def bind(self, value, dbtype, param_name=None, output=False, null=False, max_length=-1):
        # def bind(self, object value, int dbtype, str param_name=None, int output=False, int null=False, int max_length=-1):
        this_result = None

        if self._storedproc is not None:
            if value is None:
                null = True
            elif dbtype in (_mssql.SQLCHAR, _mssql.SQLVARCHAR, _mssql.SQLUUID):
                value = str(value)

            this_result = self._storedproc.bind(value, dbtype, param_name=param_name, output=output, null=null,
                                                max_length=max_length)
        return this_result

    @property
    def connection(self):
        return self._storedproc.connection

    @property
    def name(self):
        return self._storedproc.name

    @property
    def parameters(self):
        return self._storedproc.parameters


# -------------------------------------------------
# Global cached resources
# -------------------------------------------------
class ThResource:
    """Class ThResource is to store a single web resource.

    A web resource may be an HTML template, an HTML fragment (i.e. a static block), an HTML page, or anything else
    to be sent to the browser:  .css, .js., .jpg, .img, etc.

    A resource may also have flags to help control access and behavior, such as is_public that indicates whether
    this resource can be directly served to a browser (versus being for use by the TheasServer only),
    render_jinja_template to indicate whether this resource needs to be rendered before sending, etc.

    Works with ThCachedResources.
    """

    def __init__(self):
        self.resource_code = ''
        self.filename = ''
        self.filetype = ''
        self.date_updated = ''
        self.data = ''
        self.api_stored_proc = None
        self.api_async_stored_proc = None
        self.api_stored_proc_resultset_str = None
        self.is_public = False
        self.is_static = False
        self.requires_authentication = False
        self.render_jinja_template = False
        self.skip_xsrf = False
        self.exists = True
        self.on_before = None
        self.on_after = None
        self.revision = None

    def __del__(self):
        self.data = None


class ThCachedResources:
    """Class ThCachedResources is to manage a thread-safe global dictionary for storage of cached web resources
    (see ThResource).

    It provides a mutex, and methods for locking and unlocking the global dictionary, as well as methods for
    loading resources, retrieving resources, and deleting resources (i.e. purging cached resources).
    """
    _mutex = Lock()

    def lock(self):
        self._mutex.acquire()

    def unlock(self):
        self._mutex.release()

    def __init__(self):
        self.__resources = {}
        self.__static_blocks_dict = {}
        self.__resource_versions_dict = {}
        self.default_path = G_program_options.settings_path

    def __del__(self):
        self.lock()

        try:
            for resource_code in self.__resources:
                self.__resources[resource_code] = None

            self.__resources = None
            del self.__resources

            for resource_code in self.__static_blocks_dict:
                self.__resources[resource_code] = None

            self.__static_blocks_dict = None
            del self.__static_blocks_dict

            self.__resource_versions_dict = None
            del self.__resource_versions_dict

        finally:
            self.unlock()

    @property
    def static_blocks_dict(self):
        return self.__static_blocks_dict

    @static_blocks_dict.setter
    def static_blocks_dict(self, new_dict):
        self.__static_blocks_dict = new_dict

    @property
    def resource_versions_dict(self):
        return self.__resource_versions_dict

    @resource_versions_dict.setter
    def resource_versions_dict(self, new_dict):
        self.__resource_versions_dict = new_dict

    def len(self):
        return len(self.__resources)

    def add_resource(self, resource_code, resource_dict):
        self.lock()
        try:
            self.__resources[resource_code] = resource_dict
        finally:
            self.unlock()

    def load_resource(self, resource_code, th_session, all_static_blocks=False, sessionless=False, from_filename=None,
                      is_public=False, is_static=False, get_default_resource=False):
        this_resource = None

        if from_filename:
            # load resource from file

            if from_filename.endswith('Theas.js'):
                try:
                    with open(from_filename, 'r') as f:
                        buf = f.read()
                        f.close()
                except Exception:
                    raise TheasServerError('Error while starting the Theas Server:  File Theas.js could not be read.')

                this_resource = ThResource()
                this_resource.resource_code = resource_code
                this_resource.filename = from_filename
                this_resource.filename = 'application/javascript'
                this_resource.data = buf
                this_resource.api_stored_proc = None
                this_resource.api_async_stored_proc = None
                this_resource.api_stored_proc_resultset_str = None
                this_resource.is_public = is_public
                this_resource.is_static = is_static
                this_resource.requires_authentication = False

                self.add_resource(resource_code, this_resource)
            else:
                raise TheasServerError(
                    'Error due to request of file {} from the file system.  Server is configured to server resources only from the database.'.format(
                        from_filename))
        else:
            # load resource from database
            if th_session is None:
                if not sessionless:
                    assert th_session is not None, 'ThCachedResources: load_resource called without a valid session'
                else:
                    th_session = ThSession(None, sessionless=True)
                    resource_code = None

            if all_static_blocks:
                th_session.log('Resource', 'Will load all static resources from the database.')
            else:
                if resource_code is None or\
                                resource_code == '~' or\
                                resource_code == '/' or\
                                resource_code == '':
                    th_session.log('Resource',
                                   'No resource_code specified.  Will load default resource for this session.')
                    get_default_resource = 1
                else:
                    th_session.log('Resource', 'ThCachedResources.load_resource fetching from database',
                                   resource_code if resource_code is not None else 'None')

            # Get SysWebResourcesdata from database
            this_proc = ThStoredProc('theas.spgetSysWebResources', th_session)

            if this_proc.is_ok:

                # Note:  we could check for existence of @GetDefaultResource down below to help with backwards
                # compatibility ... but that would mean having to call refresh_parameter_list, which is
                # unnecessary overhead.
                # this_proc.refresh_parameter_list()

                this_proc.bind(resource_code, _mssql.SQLCHAR, '@ResourceCode', null=(resource_code is None))
                this_proc.bind(str(int(all_static_blocks)), _mssql.SQLCHAR, '@AllStaticBlocks')

                # if '@GetDefaultResource' in this_proc.parameter_list:
                this_proc.bind(str(int(get_default_resource)), _mssql.SQLCHAR, '@GetDefaultResource')

                proc_result = this_proc.execute(fetch_rows=False)
                assert proc_result, 'ThCachedResources.load_resource received error result from call to theas.spgetSysWebResources in the SQL database.'

                row_count = 0

                this_static_blocks_dict = {}

                if this_proc.th_session.sql_conn is not None:
                    for row in this_proc.th_session.sql_conn:
                        row_count += 1
                        buf = row['ResourceText']
                        if not buf:
                            buf = row['ResourceData']
                            if buf:
                                buf = bytes(buf)

                        elif not all_static_blocks and buf and '$thInclude_' in buf:
                            # Perform replacement of includes.  Template may include string like:
                            # $thInclude_MyResourceCode
                            # This will be replaced with the static block resource having a ResourceCode=MyResourceCode
                            tmp = string.Template(buf)
                            buf = tmp.safe_substitute(G_cached_resources.static_blocks_dict)

                        this_resource = ThResource()

                        this_resource.resource_code = row['ResourceCode']
                        this_resource.filename = row['Filename']
                        if 'Filetype' in row:
                            this_resource.filetype = row['Filetype']
                        if 'DateUpdated' in row:
                            this_resource.date_updated = row['DateUpdated']
                        this_resource.data = buf
                        this_resource.api_stored_proc = row['APIStoredProc']
                        this_resource.api_async_stored_proc = row['APIAsyncStoredProc']
                        this_resource.api_stored_proc_resultset_str = row['ResourceResultsets']
                        this_resource.is_public = row['IsPublic']
                        this_resource.is_static = row['IsStaticBlock']
                        this_resource.requires_authentication = row['RequiresAuthentication']
                        this_resource.render_jinja_template = row['RenderJinjaTemplate']
                        this_resource.skip_xsrf = row['SkipXSRF']

                        if 'OnBefore' in row:
                            this_resource.on_before = row['OnBefore']

                        if 'OnAfter' in row:
                            this_resource.on_after = row['OnAfter']

                        if 'Revision' in row:
                            this_resource.revision = row['Revision']

                        if this_resource.resource_code and \
                                        this_resource.resource_code != '~':  # added 2/11/2019:  don't want to cache default resource
                            self.add_resource(row['ResourceCode'], this_resource)

                        if all_static_blocks:
                            this_static_blocks_dict['//thInclude_' + row['ResourceCode']] = buf
                            this_static_blocks_dict['thInclude_' + row['ResourceCode']] = buf

                if resource_code and resource_code != '~' and row_count == 0:
                    # do negative cache
                    this_resource = ThResource()
                    this_resource.exists = False
                    self.add_resource(resource_code, this_resource)

                if all_static_blocks:
                    ThCachedResources.static_blocks_dict = this_static_blocks_dict

                    have_next_resultset = this_proc.th_session.sql_conn.nextresult()
                    if have_next_resultset:
                        for row in this_proc.th_session.sql_conn:
                            # note:  should only be one row
                            row_count += 1
                            buf = row['JSON_CurResourceRevisions']
                            ThCachedResources.resource_versions_dict = dict(
                                (v["ResourceCode"], v) for v in json.loads(buf))

                this_proc = None
                del this_proc

        return this_resource

    def delete_resource(self, resource_code=None, delete_all=False):
        result = False

        if delete_all and len(self.__resources) > 0:
            self.lock()
            try:
                self.__resources.clear()
                result = True
            finally:
                self.unlock()

            self.load_global_resources()

        elif resource_code is not None and resource_code in self.__resources:
            self.lock()
            try:
                self.__resources[resource_code] = None
                del self.__resources[resource_code]
                result = True
            finally:
                self.unlock()

        return result

    def get_resource(self, resource_code, th_session, for_public_use=False, all_static_blocks=False,
                     none_if_not_found=True, get_default_resource=False, from_file=None):
        global DEFAULT_RESOURCE_CODE

        this_resource = None

        if resource_code:
            resource_code = resource_code.strip()
        else:
            if th_session is not None:
                resource_code = th_session.bookmark_url

        if resource_code == '':
            resource_code = None

        if resource_code is not None and resource_code in self.__resources:
            # Cached resource
            this_resource = self.__resources[resource_code]
            if th_session is not None:
                th_session.log('Resource', 'Serving from cache', resource_code)
            else:
                ThSession.cls_log('Resource', 'Serving from cache', resource_code)
        else:
            if th_session is not None:
                # Load resource (which requires a session)
                this_resource = self.load_resource(resource_code, th_session, all_static_blocks,
                                                   get_default_resource=get_default_resource)

        log_msg = None

        if th_session is not None and (this_resource is None or not this_resource.exists):
            # if DEFAULT_RESOURCE_CODE:
            #    resource_code = DEFAULT_RESOURCE_CODE
            #    this_resource = self.load_resource(resource_code, th_session, all_static_blocks=False)

            if resource_code or th_session is not None:
                # suppress logging if there is no session and resource_code was not provided, because the caller
                # was probably just checking for a cached resource
                log_msg = 'Resource', 'Requested resource {} could not be loaded in ThCachedResources.get_resource'.format(
                    resource_code)
        else:
            if for_public_use and this_resource is None:
                log_msg = 'Resource', 'Requested resource {} could not be loaded in ThCachedResources.get_resource'.format(
                    resource_code)

        if log_msg is not None:
            if th_session is not None:
                th_session.log('Resource', log_msg)
            else:
                ThSession.cls_log('Resource', log_msg)

        # Careful:  we could be getting a cached resource in which case there may not yet be a session, in which
        # case we can't update current_resource here!  It is up to the caller to update current_resource
        if th_session is not None and this_resource is not None and this_resource.exists and this_resource.resource_code != LOGIN_RESOURCE_CODE and this_resource.render_jinja_template:
            # we are assuming that only a jinja template page will have a stored procedure / can serve
            # as the current resource for a session.  (We don't want javascript files and the like
            # to be recorded as the current resource.)
            th_session.current_resource = this_resource
            th_session.theas_page.set_value('th:CurrentPage', this_resource.resource_code)

        return this_resource

    def load_global_resources(self):
        self.load_resource('Theas.js', None, from_filename=self.default_path + 'Theas.js', is_public=True)
        self.load_resource(None, None, all_static_blocks=True, sessionless=True)


# -------------------------------------------------
# Global session list
# -------------------------------------------------
class ThSessions:
    """Class ThSessions is to manage a thread-safe global dictionary of active user sessions.

    It provides a mutex, and methods for locking and unlocking the global dictionary, as well as methods for
    creating, retrieving, and deleting sessions.

    It also provides support for a background thread that is responsible for automatically purging expired
    sessions.

    See class ThSession.  (ThSessions manages a dictionary of ThSession objects.)
    """
    _mutex = Lock()

    def __init__(self):
        self.__sessions = {}
        self.waiting_for_busy = {}
        self.background_thread_running = False

    def __del__(self):
        self.lock()
        try:
            for this_session_token in self.__sessions:
                if self.__sessions[this_session_token]:
                    if self.__sessions[this_session_token].sql_conn:
                        self.__sessions[this_session_token].sql_conn = None
                self.__sessions[this_session_token] = None
            self.__sessions.clear()
        finally:
            self.unlock()

    def lock(self):
        self._mutex.acquire()

    def unlock(self):
        self._mutex.release()

    def stop(self):
        self.background_thread_running = False

    def __len__(self):
        return len(self.__sessions)

    def add_session(self, session_token, this_session):
        self.lock()
        try:
            self.__sessions[session_token] = this_session
        finally:
            self.unlock()

    def remove_session(self, session_token):
        this_session = None
        self.lock()
        try:
            if session_token in self.__sessions:
                this_session = self.__sessions[session_token]
                del self.__sessions[session_token]
        except Exception:
            if LOGGING_LEVEL:
                print('Exception in remove_session')
        finally:
            self.unlock()
        return this_session

    def remove_all_sessions(self):
        self.lock()
        try:
            for session_token, this_sess in self.__sessions.items():
                if this_sess is not None and this_sess.sql_conn is not None:
                    this_sess.sql_conn.close()
        finally:
            self.unlock()

    def remove_expired(self, remove_all=False):
        global G_program_options
        self.lock()
        try:
            expireds = {}

            for session_token in self.__sessions:
                this_session = self.__sessions[session_token]
                if (
                    remove_all or
                    this_session is None or
                    this_session.date_expire is None or
                    this_session.date_expire < datetime.datetime.now() or

                        (
                        G_program_options.sql_timeout > 0 and
                        this_session.date_sql_timeout is not None and
                        this_session.date_sql_timeout < datetime.datetime.now()
                        )
                            ):
                        expireds[session_token] = this_session

            for session_token in expireds:
                this_session = expireds[session_token]
                self.__sessions[session_token] = None
                del self.__sessions[session_token]
                if this_session is not None:
                    del this_session

            del expireds
        finally:
            self.unlock()

    @staticmethod
    def log(category, *args, severity=10000):
        if LOGGING_LEVEL == 1 or 0 > severity >= LOGGING_LEVEL:
            print(datetime.datetime.now(), 'ThSessions [{}]'.format(category), *args)

    def retrieve_session(self, session_token=None, comments='', do_log=True):
        this_sess = None
        self.lock()
        try:
            if session_token and session_token in self.__sessions:
                # have existing session
                this_sess = self.__sessions[session_token]
                if do_log:
                    this_sess.log('Sessions', 'Trying to retrieve existing session', session_token, comments)
        finally:
            self.unlock()
        return this_sess

    def _poll_remove_expired(self):
        global G_server_is_running

        last_poll = datetime.datetime.now()

        while self.background_thread_running and G_server_is_running:
            # self.log('PollRemoveExpired', 'Running background_thread_running')
            if (datetime.datetime.now() - last_poll).total_seconds() > REMOVE_EXPIRED_THREAD_SLEEP:
                last_poll = datetime.datetime.now()
                self.log('PollRemoveExpired', 'Sessions at start', len(self.__sessions))
                self.remove_expired()
                self.log('PollRemoveExpired', 'Sessions at end', len(self.__sessions))
            time.sleep(3)  # sleep only for 3 seconds so the application can shutdown cleanly when needed

    def start_cleanup_thread(self):
        if REMOVE_EXPIRED_THREAD_SLEEP:
            self.background_thread_running = True
            expire_thread = threading.Thread(target=self._poll_remove_expired, name='ThSessions Cleanup')
            expire_thread.start()


# -------------------------------------------------
# ThSession
# -------------------------------------------------
class ThSession:
    """Class ThSession manages all aspects of an individual user session.

     Each session has a unique session_token, and is stored in a ThSessions object.

     Each session also has its own dedicated SQL connection, manages authentication (including rendering the
     login screen as needed), tracks elapsed time of individual requests, performs logging, provides locking
     to prevent multiple simultaneous requests for the same session, and provides methods for initializing
     a new session and for retrieving a session from the global ThSessions object.

     ThSession.get_session() currently tries to retrieve a session from the global ThSessions object.  In
     he future it might make sense to move this retrieval to a method of ThSessions()
    """

    def __init__(self, this_session_token, sessionless=False):
        self.theas_page = None
        self.sql_conn = None

        self.log_current_request = True
        self.current_handler = None
        self.comments = None

        self.session_token = None

        if sessionless:
            self.session_token = str(uuid.uuid4())
        else:
            self.session_token = this_session_token

        self.logged_in = False
        self.autologged_in = False
            # not a "real" login, but rather indicates a login using LOGIN_AUTO_USER_TOKEN

        self.__locked_by = None
        self.__date_locked = None

        self.__current_resource = None

        self.current_template_str = None

        self.current_data = None

        self.bookmark_url = None

        self.next_url = '/'
        self.request_count = 0
        self.initialized = False

        self.date_start = datetime.datetime.now()
        self.date_expire = None
        self.date_last = None

        self.date_last_sql_start = None
        self.date_last_sql_done = None
        self.date_sql_timeout = None

        self.date_request_start = None
        self.date_request_done = None

        self.history = []

        self.component_state = {}

        self.log('Session', 'Created new session', self.session_token)
        self.date_started = datetime.datetime.now()

        self.sql_files_init_done = False

        self.current_xsrf_form_html = None

        # username holds the username of the currently authenticated user, and will be updated by authenticate()
        self.username = None
        self.user_token = None

        # if set to true, upon successful authenticate the user's token will be saved to a cookie
        # for automatic login on future visits
        self.remember_user_token = REMEMBER_USER_TOKEN

        self.theas_page = theas.Theas(theas_session=self)

    @property
    def current_resource(self):
        return self.__current_resource

    @property
    def resource_versions(self):
        # Return master resource_versions_dict from ThCachedResources to make this available in Theas filters
        return ThCachedResources.resource_versions_dict

    @current_resource.setter
    def current_resource(self, value):
        if value is not None and value.render_jinja_template:

            if self.__current_resource is None or (value.resource_code != self.__current_resource.resource_code):
                self.log('Resource', 'Current_resource changed to: {}  Was: {}'.format(value.resource_code,
                                                                                       self.__current_resource.resource_code if self.__current_resource else 'not set'))
                self.__current_resource = value

    @property
    def locked(self):
        return False if self.__locked_by is None else True

    def release_lock(self, handler=None):
        if handler.handler_guid != self.__locked_by:
            self.log('Session',
                     'WARNING: Session release_lock called, but caller does not have the lock.  (Requestor={} locked_by={})'.format(
                         handler.handler_guid, self.__locked_by))

        now = time.time()
        elapsed = (now - self.__date_locked) * 1000 if self.__date_locked is not None else 0
        self.log('Session', 'UNLOCK by handler ({})'.format(handler.handler_guid))
        self.log('Timing', 'Session lock duration: {:.2f}ms'.format(elapsed))

        self.__locked_by = None
        self.__date_locked = None

    def get_lock(self, handler=None, handler_guid=None, no_log=False):
        result = False

        this_handler_guid = None
        if handler is not None:
            this_handler_guid = handler.handler_guid

        if this_handler_guid is None:
            this_handler_guid = handler_guid

        assert this_handler_guid is not None, 'ThSession.get_lock requires a value for handler_guid (or handler.handler_guid)'

        if self.__locked_by == this_handler_guid:
            # Requestor already has a lock.  Nothing to do.
            result = True
        else:
            this_give_up = False
            # while self.__locked_by is not None and self.__locked_by != handler.handler_guid and not this_give_up:
            # note:  can't really wait for a lock here.  Return quickly, and let the caller retry.

            if self.__locked_by is not None and self.__locked_by != this_handler_guid and not this_give_up:
                this_give_up = True
                self.log('Session', 'Waiting for busy session. Wanted by {}'.format(this_handler_guid))
            # if self.__date_locked is not None and time.time() - self.__date_locked > 30000:
            #                    self.log('Session', 'Giving up waiting for busy session:  killing stuck session wanted by {}'.format(handler.handler_guid))
            #                    if self.sql_conn is not None and\
            #                            self.date_sql_timeout is not None and\
            #                            datetime.datetime.now() > self.date_sql_timeout:
            # Still waiting for a response from sql in a different thread.  Yuck.
            #                        self.log('Session', 'SQL connection is stuck waiting for a response in a different thread!!!')
            #
            #                    # We can't forcibly access this session--not thread-safe to do so.  Must abandon.
            #                    this_give_up = True
            # self.__date_busy_start = None
            # self.sql_conn.cancel()  # is likely to crash us / is not thread-safe
            # self.sql_conn = None # discard this SQL connection
            # self.logged_in = False # without a SQL connection we will need to re-authenticate
            # this_sess.logout()
            # G_sessions.remove_session(self.session_token)
            # this_sess = None

            # Note:  We expect this code to be run in a separate thread.  If it is run in the main thread, it will
            # never be able to access the busy session (because the main thread will just be running this loop and
            # will never be allowed to release the other lock on the session.

            if not this_give_up:
                result = True
                self.__locked_by = handler_guid
                self.__date_locked = time.time()
                self.request_count += 1
                if not no_log:
                    self.log('Session', 'LOCK obtained by handler ({})'.format(self.__locked_by))
        return result

    def __del__(self):
        if self.theas_page is not None:
            self.theas_page = None
            del self.theas_page

        if self.sql_conn is not None:
            if self.sql_conn.connected:
                self.sql_conn.close()
            self.sql_conn = None
            del self.sql_conn

    @classmethod
    def cls_log(cls, category, *args, severity=10000):
        if LOGGING_LEVEL == 1 or 0 > severity >= LOGGING_LEVEL:
            print(datetime.datetime.now(), 'ThSessions [' + category + ']:', *args)

    @classmethod
    def get_session(cls, retrieve_from_db=False, inhibit_create=False,
                    comments=None, defer_sql=False, do_log=True, session_token=None, handler_guid=None):

        global G_sessions
        # Retrieve or create a session as needed.
        # See if requestor provided a session token (in cookie, URI, or form field).  If so, look up in global
        # list of sessions.  If no session token or session is not in list, create a new session.
        date_start = datetime.datetime.now()
        this_sess = None
        lock_succeeded = False  # indicates we received a lock
        failed_to_lock = False  # indicates we attempted a lock, but failed

        if session_token:
            # try to retrieve the session from the global list
            this_sess = G_sessions.retrieve_session(session_token, comments=comments, do_log=do_log)

        if this_sess is not None:
            this_sess.log('Session', 'Obtained existing session', this_sess.session_token)
            lock_succeeded = this_sess.get_lock(handler_guid=handler_guid)

            if not lock_succeeded:
                this_sess = None
                failed_to_lock = True

        if this_sess is not None:
            this_sess.log_current_request = do_log
            this_sess.comments = comments
        elif not failed_to_lock:
            if inhibit_create:
                # not allowed to start new session
                cls.cls_log('Sessions', 'Need to create new session, but inhibit_crecate prevents new session')
            else:
                # start new session
                session_token = str(uuid.uuid4())
                this_sess = ThSession(session_token)

                #if USE_SECURE_COOKIES:
                #    secval = tornado.web.create_signed_value(COOKIE_SECRET, SESSION_COOKIE_NAME, session_token)
                #    this_sess.theas_page.set_value(SESSION_COOKIE_NAME, secval)
                #else:
                #    this_sess.theas_page.set_value(SESSION_COOKIE_NAME, session_token)

                this_sess.log_current_request = do_log

                G_sessions.add_session(session_token, this_sess)

                this_sess.log('Sessions', 'Active session count', len(G_sessions))

                # get lock on the new session
                lock_succeeded = this_sess.get_lock(handler_guid=handler_guid)

                if not lock_succeeded:
                    this_sess = None
                    failed_to_lock = True

        # we should now always have a session unless inhibit_create==True
        # assert this_sess is not None and this_sess.get_lock(handler=handler, no_log=True), 'Could not obtain session in ThSession.get_session'

        if this_sess is not None:
            this_sess.date_request_start = date_start
            this_sess.date_expire = datetime.datetime.now() + datetime.timedelta(minutes=SESSION_MAX_IDLE)

        return this_sess, failed_to_lock

    def log(self, category, *args, severity=10000):
        if LOGGING_LEVEL == 1 or 0 > severity >= LOGGING_LEVEL:
            if self.log_current_request:
                # print(datetime.datetime.now(), 'ThSession [{}:{}] ({}) - {} ({})'.format(
                print(datetime.datetime.now(), 'ThSession [{}:{}] - {} ({})'.format(
                    self.session_token,
                    self.request_count,
                    # self.__locked_by,
                    category,
                    self.comments if self.comments is not None else '',
                ), *args)

    def init_session(self, defer_sql=False, force_init=False):
        global G_program_options
        global G_sessions

        if force_init:
            self.sql_conn = None

        if force_init or self.sql_conn is None or (self.sql_conn is not None and not self.sql_conn.connected):
            defer_sql = False
            self.initialized = False

        if not defer_sql and (self.sql_conn is None or not self.initialized):

            # Establish SQL connection, initialize
            if not defer_sql:
                if self.sql_conn is None:
                    self.log('SQL', 'Creating new SQL connection')
                    try:
                        self.sql_conn = _mssql.connect(
                            server=G_program_options.sql_server,
                            port=G_program_options.sql_port,
                            user=G_program_options.sql_user,
                            password=G_program_options.sql_password,
                            database=G_program_options.sql_database,
                            appname=G_program_options.sql_appname
                        )
                        self.log('SQL', 'FreeTDS version: ' + str(self.sql_conn.tds_version))
                    except Exception as e:
                        self.log('SQL', 'Error creating new SQL connection: ' + str(e))

                if self.sql_conn is not None:
                    self.sql_conn.query_timeout = G_program_options.sql_timeout
                    # Note:  we have created a new user session, but the user still needs to be authenticated
                    self.initialized = True

                    # make sure session has been initialized to handle uploaded files
                    if not self.sql_files_init_done:
                        # Initialize theas session:  stored proc returns SQL statements we need to execute
                        proc = ThStoredProc('theas.spgetInitSession', self)  # SOS Agri:  must be spInitSession2
                        if proc.is_ok:
                            result_value = proc.execute()
                            for row in proc.resultset:
                                self.sql_conn.execute_non_query(row['SQLToExecute'])

                            self.sql_files_init_done = True

                    if LOGIN_AUTO_USER_TOKEN and not self.logged_in and not self.autologged_in and self.current_handler is not None:
                        self.log('Auth', 'Authenticating as AUTO user (i.e. public)')
                        try:
                            self.authenticate(user_token=LOGIN_AUTO_USER_TOKEN)
                        except:
                            self.autologged_in = False

                        if not self.autologged_in:
                            self.log('Auth',
                                     'Error: Authentication as AUTO user (i.e. public) FAILED.  Is your config file wrong?')
                            self.log('Auth', 'Bad AUTO user token: {}'.format(LOGIN_AUTO_USER_TOKEN))

        return self

    def finished(self):
        if not self.__locked_by:
            pass
        else:

            self.date_request_done = datetime.datetime.now()

            self.current_data = None  # clear out data that was used by this request's template

            if len(self.history) > 0 and self.history[-1]['PageName'] == self.theas_page.get_value('theas:th:NextPage'):
                # self.history[-1]['stepGUID'] = self.get_param('stepGUID')
                # self.history[-1]['stepDefID'] = self.get_param('stepDefID')
                pass
            else:
                this_history_entry = {}
                this_history_entry['DateRequestDone'] = self.date_request_done
                this_history_entry['PageName'] = self.theas_page.get_value('theas:th:NextPage')
                # this_history_entry['stepGUID'] = self.get_param('stepGUID')
                # this_history_entry['stepDefID'] = self.get_param('stepDefID')
                self.history.append(this_history_entry)

            self.log('Session', 'Total requests for this session: ', self.request_count)
            self.log('Session', 'Finished with this request')

            if self.sql_conn is None:
                self.log('Session', 'Destroying session')
                G_sessions.remove_session(self.session_token)
            else:
                self.log('Session', 'Will time out at', self.date_expire)

            self.log_current_request = True
            self.current_handler.cookies_changed = False

            self.release_lock(handler=self.current_handler)

    def authenticate(self, username=None, password=None, user_token=None, retrieve_existing=False):
        """
        :param username: Username of user.  If provided, provide password as well
        :param password: Password of user.  Provide if username is provided
        :param user_token: Token for user authentication.  May be provided INSTEAD of username and password
        :param retrieve_existing: Boolean flag.  If set, does not authenticate, but does retrieve existing session
        :return: logged_in (boolean), error_message (string)
        """
        error_message = ''
        self.logged_in = False
        result = False

        if self.current_handler is not None:
            if username is None and password is None and user_token is None and not retrieve_existing:
                # caller didn't specify username/password or user-token, so check for a form
                # post from the login page
                if 'u' in self.current_handler.request.arguments:
                    username = self.current_handler.get_argument('u')[0]
                elif 'theas:Login:UserName' in self.current_handler.request.arguments:
                    username = self.current_handler.get_argument('theas:Login:UserName')

                if 'pw' in self.current_handler.request.arguments:
                    password = self.current_handler.request.get_argument('pw')
                elif 'theas:Login:Password' in self.current_handler.request.arguments:
                    password = self.current_handler.get_argument('theas:Login:Password')

                # theas:th:RememberUser is a checkbox (which will not be submitted if unchecked)--so default to '0'
                temp_remember = '0'

                # see if form tells us whether to remember the user
                temp_remember_arg = self.current_handler.get_arguments('theas:th:RememberUser')
                if len(temp_remember_arg):
                    temp_remember = temp_remember_arg[0]
                self.theas_page.set_value('theas:th:RememberUser', temp_remember)

        if self.theas_page:
            temp_remember = self.theas_page.get_value('theas:th:RememberUser', auto_create=False)
            if temp_remember is not None:
                self.remember_user_token = temp_remember == '1'

        self.log('Session', 'Attempting authentication')

        # The session keeps a copy of the user_name for convenience / to access in templates
        self.username = None

        # authenticate user into database app
        proc = ThStoredProc('theas.spdoAuthenticateUser', self)
        if proc.is_ok:
            if retrieve_existing:
                proc.bind(retrieve_existing, _mssql.SQLVARCHAR, '@RetrieveExisting')
            else:
                if username is not None:
                    proc.bind(username, _mssql.SQLVARCHAR, '@UserName')
                if password is not None:
                    proc.bind(password, _mssql.SQLVARCHAR, '@Password')
                if user_token is not None:
                    proc.bind(user_token, _mssql.SQLVARCHAR, '@UserToken')
                if self.session_token is not None:
                    # @SessionToken is informational only:  allows the web session to be logged in the database
                    proc.bind(self.session_token, _mssql.SQLVARCHAR, '@SessionToken')

            try:
                session_guid = None

                result_value = proc.execute()

                for row in proc.resultset:
                    session_guid = row['SessionGUID']
                    user_token = row['UserToken']
                    username = row['UserName']

                if session_guid is not None:
                    if user_token == LOGIN_AUTO_USER_TOKEN:
                        self.logged_in = False
                        self.autologged_in = True
                        self.log('Auth', 'Authenticated as AUTO (public)... not a real login')

                    else:
                        self.logged_in = True

                        # Store some user information (so the information can be accessed in templates)
                        self.username = username
                        self.user_token = user_token

                        if self.current_data:
                            # update data for template (in case Authenticate() was called at the request
                            # of a resource's stored procedure just before rendering the page)
                            self.current_data['_Theas']['UserName'] = self.username
                            self.current_data['_Theas']['LoggedIn'] = self.logged_in
                            self.current_data['_Theas']['UserToken'] = self.user_token

                        self.log('Auth', 'Authenticated as actual user {}'.format(self.username))

                proc = None
                del proc

            except Exception as e:
                self.logged_in = False
                self.user_token = None
                self.log('Session', 'Authentication failed:', e)
                error_message = repr(e) + '|' + 'Invalid username or password.|1|Could Not Log In'

        else:
            self.logged_in = False
            self.log('Session', 'Could not access SQL database server to attempt Authentication.')
            error_message = 'Could not access SQL database server|Sorry, the server is not available right now|1|Cannot Log In'

        if self.current_handler:
            # If authentication was successful, we want to make sure the UserToken
            # cookie is set properly.  (If authentication was not successful,
            # we make no changes to the UserToken cookie.)
            self.current_handler.cookie_usertoken = None

            if self.logged_in and self.remember_user_token:
                self.current_handler.cookie_usertoken = self.user_token

            # always write the cookie...even if authentication failed (in which case we need to clear it)
            self.current_handler.write_cookies()

        return self.logged_in, error_message

    def logout(self):
        self.log('Session', 'Logged out.')

        self.release_lock(handler=self.current_handler)

        self.logged_in = False

        if self.sql_conn is not None and self.sql_conn.connected:
            self.log('SQL', 'Closing SQL connection in ThSession.logout')

            try:
                self.sql_conn.cancel()
            except Exception as e:
                self.log('SQL', 'In ThSession.logout, exception calling sql_conn.cancel(). {}'.format(e))
            finally:
                self.log('SQL', 'Call to cancel() on SQL connection complete')

            try:
                proc = ThStoredProc('theas.spdoLogout', self)
                if proc.is_ok:
                    proc.bind(self.session_token, _mssql.SQLVARCHAR, '@SessionToken')
                    proc.execute()
            except Exception as e:
                self.log('SQL', 'In ThSession.logout, exception calling theas.spdoLogout. {}'.format(e))

            try:
                self.sql_conn.close()
                self.sql_conn = None
            except Exception as e:
                self.log('SQL', 'In ThSession.logout, exception calling sql_conn.close(). {}'.format(e))
            finally:
                self.log('SQL', 'In ThSession.logout, call to close() on SQL connection complete')

    def clientside_redir(self, url=None, action='get'):
        # returns tiny html document to send to browser to cause the browser to post back to us
        if not url:
            if self.bookmark_url:
                url = self.bookmark_url
                self.bookmark_url = None
            elif self.current_resource and self.current_resource.resource_code:
                url = self.current_resource.resource_code
            else:
                url = '/'

        if action == 'get':
            buf = '''<!doctype html>
        <html>
        <head>
        <script>window.location = "{action}";</script>
        </head>
        <body>
        </body>
        </html>'''

            buf = buf.format(action=url)

        else:
            buf = '''<!doctype html>
<html>
<body>
<form id="frmBounce" method="POST" action="{action}" onSubmit="noDef();">
    <input type="hidden" name={session_cookie_name} value="{session_token}"/>
    {xsrf}
</form>
<script>
    function noDef(e) {{
        if (!e) {{
            e = window.event;
        }}
        if (e.preventDefault) {{
            e.preventDefault();
        }}
        if (e.stopPropagation) {{
            // IE9 & Other Browsers
            e.stopPropagation();
        }}
        else {{
            // IE8 and Lower
            e.cancelBubble = true;
        }}
    }}
    document.getElementById("frmBounce").submit();
</script>
</body>
</html>'''

            buf = buf.format(action=url, session_token=self.session_token,
                             xsrf=self.current_handler.xsrf_form_html(),
                             session_cookie_name=SESSION_COOKIE_NAME)

        return buf

    def do_on_sql_start(self, proc):
        self.date_last_sql_start = time.time()
        self.date_sql_timeout = datetime.datetime.now() + datetime.timedelta(seconds=G_program_options.sql_timeout)
        self.log('Timing', 'SQL Start for procedure: ', proc.stored_proc_name)
        self.log('Timing', 'SQL execution times out at:', self.date_sql_timeout)

    def do_on_sql_done(self, proc):
        now = time.time()

        self.date_last_sql_done = now
        self.date_sql_timeout = None

        elapsed = (now - self.date_last_sql_start) * 1000 if self.date_last_sql_start is not None else 0
        self.log('Timing', 'SQL Done.  Duration: {:.2f}ms'.format(elapsed))

    def init_template_data(self):
        this_data = {}
        this_data['_Theas'] = {}
        #this_data['_Theas']['ST'] = self.session_token
        this_data['_Theas']['UserName'] = self.username
        this_data['_Theas']['LoggedIn'] = self.logged_in
        #this_data['_Theas']['UserToken'] = self.user_token

        if self.current_handler is not None:
            this_data['_Theas']['xsrf_token'] = self.current_handler.xsrf_token.decode('ascii')
            this_data['_Theas']['__handler_guid'] = self.current_handler.handler_guid

        this_data['_Theas']['theasServerPrefix'] = G_program_options.server_prefix

        # this_data['_Theas']['xsrf_formHTML'] = self.current_handler.xsrf_form_html()
        this_data['_Theas']['theasParams'] = self.theas_page.get_controls()

        if self.current_resource is not None:
            this_data['_Theas']['theasCurrentPage'] = self.current_resource.resource_code
        this_data['_Theas']['theasIncludes'] = G_cached_resources.static_blocks_dict
        this_data['_Theas']['theasJS'] = 'Theas.js'

        now_time = datetime.datetime.now().strftime("%I:%M%p")
        this_data['_Theas']['Now'] = now_time

        # Note:  if an APIStoredProc is called, data._resultsetMeta will be added,
        # but we do not add this dictionary here during initialization
        # this_data['_resultsetMeta'] = {}

        self.current_data = this_data

        return this_data

    def build_login_screen(self):
        global G_cached_resources

        self.log('Response', 'Building login screen')

        buf = '<html><body>No data in build_login_screen</body></html>'

        resource = None
        template_str = ''

        self.log('Resource', 'Fetching login page resource')
        resource = G_cached_resources.get_resource(LOGIN_RESOURCE_CODE, self)

        if resource is None:
            # raise Exception ('Could not load login screen template from the database.  Empty template returned from call to theas.spgetSysWebResources.')
            buf = '<html><head><meta http-equiv="refresh" content="30"></meta><body>Could not load login screen template from the database server.  Empty template returned from call to theas.spgetSysWebResources.<br /><br />Will try again shortly... </body></html>'

        else:
            template_str = resource.data
            this_data = self.init_template_data()

            buf = self.theas_page.render(template_str, data=this_data)

        return buf


# -------------------------------------------------
# ThResponseInfo
# -------------------------------------------------
class ThResponseInfo:
    def __init__(self):
        self.current_date = None
        self.date_updated = None
        self.expires = None
        self.content_length = None
        self.cache_control = None
        self.content_type = None
        self.content_filename = None
        self.etag = None

# -------------------------------------------------
# ThHandler main request handler
# -------------------------------------------------
class ThHandler(tornado.web.RequestHandler):
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)
        self.session = None
        self.handler_guid = str(uuid.uuid4())
        self.deferred_xsrf = False
        self.set_header('Server', 'Theas/{}'.format(THEAS_VERSION))
        self.filename = None

        self.__cookies_changed = False

        self.__cookie_st = None
        self.__cookie_usertoken = None

        # Retrieve session and user token cookie values and save
        # them in the new session in __cookie_st and __cookie_usertoken
        self.retrieve_cookies()

    def __del__(self):
        self.session = None

    @property
    def cookie_st(self):
        return self.__cookie_st

    @cookie_st.setter
    def cookie_st(self, new_val):
        if new_val == '':
            new_val = None

        if new_val is not None and self.__cookie_st != new_val:
            self.__cookie_st = new_val
            self.cookies_changed = True

    @property
    def cookie_usertoken(self):
        return self.__cookie_usertoken

    @cookie_usertoken.setter
    def cookie_usertoken(self, new_val):
        if self.__cookie_usertoken != (None if new_val == '' else new_val):
            self.__cookie_usertoken = new_val
            self.cookies_changed = True

    @property
    def cookies_changed(self):
        return self.__cookies_changed

    @cookies_changed.setter
    def cookies_changed(self, new_val):
        if self.__cookies_changed != new_val:
            ThSession.cls_log('Cookies', 'Flag cookies_changed set to {}'.format(new_val))
            self.__cookies_changed = new_val


    def get_response_info(self, resource_code, th_session, sessionless=False):
        '''
        Determine response length and content type.  Used for HEAD requests.
        :param resource_code:
        :param th_session:
        :param all_static_blocks:
        :param sessionless:
        :param from_filename:
        :param is_public:
        :param is_static:
        :param get_default_resource:
        :return:
        '''


        # load resource from database
        if th_session is None:
            if not sessionless:
                assert th_session is not None, 'ThHandler: get_response_info called without a valid session'
            else:
                th_session = ThSession(None, sessionless=True)

        # Get stored proc thes.spGetResponseInfo
        this_proc = ThStoredProc('theas.spgetResponseInfo', th_session)

        if this_proc.is_ok:
            this_proc.bind(resource_code, _mssql.SQLCHAR, '@ResourceCode', null=(resource_code is None))

            proc_result = this_proc.execute(fetch_rows=False)
            assert proc_result, 'ThHandler: get_response_info received error result from call to theas.spgetResponseInfo in the SQL database.'

            response_info = ThResponseInfo()

            row_count = 0

            self.set_header('Server', 'theas')

            th_session = None

            if this_proc.th_session.sql_conn is not None:
                for row in this_proc.th_session.sql_conn:
                    # note:  should only be one row
                    row_count += 1
                    response_info.current_date = row['CurrentDate']
                    response_info.date_updated = row['DateUpdated']
                    response_info.content_length = row['ContentLength']
                    response_info.cache_control = row['CacheControl']
                    response_info.content_type = row['ContentType']
                    response_info.content_filename = row['ContentFilename']
                    response_info.content_expires = row['ContentExpires']
                    response_info.etag = row['Etag']

            this_proc = None
            del this_proc

        return response_info


    def retrieve_cookies(self):
        self.__cookie_st = None
        self.__cookie_usertoken = None


        orig_cookie = self.get_secure_cookie(SESSION_COOKIE_NAME)
        if orig_cookie is not None and orig_cookie != b'':
            self.__cookie_st = orig_cookie.decode(encoding='ascii')
        else:
            self.__cookie_st = self.get_cookie(SESSION_COOKIE_NAME)

        orig_cookie = self.get_secure_cookie(USER_COOKIE_NAME)
        if orig_cookie is not None and orig_cookie != b'':
            self.__cookie_usertoken = orig_cookie.decode(encoding='ascii')
        else:
             self.__cookie_usertoken = self.get_cookie(USER_COOKIE_NAME)

        #else:
        #    self.current_handler.cookie_st = None
        #    self.current_handler.write_cookies()
        #    self.log('Cookies',
        #             'Cleared cookie {} because USE_SESSION_COOKIE is not true'.format(SESSION_COOKIE_NAME))

    def write_cookies(self):
        if self.cookie_st is None or len(self.cookie_st) == 0:
            self.clear_cookie(SESSION_COOKIE_NAME, path='/')
        else:
            if USE_SECURE_COOKIES:
                self.set_secure_cookie(SESSION_COOKIE_NAME, self.cookie_st, path='/')
            else:
                self.set_cookie(SESSION_COOKIE_NAME, self.cookie_st, path='/')

        if self.cookie_usertoken is None or len(self.cookie_usertoken) == 0:
            self.clear_cookie(USER_COOKIE_NAME, path='/')
        else:
            if USE_SECURE_COOKIES:
                self.set_secure_cookie(USER_COOKIE_NAME, self.cookie_usertoken, path='/')
            else:
                self.set_cookie(USER_COOKIE_NAME, self.cookie_usertoken, path='/')


    def check_xsrf_cookie(self):
        """
        Normally we want to allow Tornado to validate XSRF tokens as normal.  However
        certain special resources (such as those that must accept a form post form an
        external site that does not have access to the XSRF token) may allow for XSRF
        token validation to be disabled.

        Since XSRF checking is performed by Torndao before the request is processed,
        the caller must indicate that XRSF checking is to be skipped by providing
        skipXSRF=1 (in either a query string parameter or form field).  However,
        if skipXSRF=1, an error will be raised later when processing the request if
        the resource's skip_xsrf flag is not set.  (In other words, in order for XSRF
        checking to be skiped, the requestor must indicate skipXSRF=1 AND the resource
        must be configured to accept SkipXSRF as well.)
        """

        if self.get_argument('skipXSRF', default='0') == '1':
            self.deferred_xsrf = True

            # since we are skipping XSRF validation we can't trust the session cookie
            self.cookie_st = None
            self.cookie_usertoken = None
            self.write_cookies()
            ThSession.cls_log('Cookies',
                              'Cleared cookies {} and theas:th:UsersToken due to skipXSRF'.format(SESSION_COOKIE_NAME))

            return True
        else:
            xsrf_ok = False
            xsrf_message = ''

            try:
                tornado.web.RequestHandler.check_xsrf_cookie(self)
                xsrf_ok = True
            except Exception as e:
                # Tornado normally just raises an exception, such as:
                #   raise HTTPError(403, "'_xsrf' argument missing from POST")
                xsrf_ok = False
                xsrf_message = str(e)

        if not xsrf_ok:
            ThSession.cls_log('xsrf', xsrf_message)
            self.send_error(status_code=403, message=xsrf_message)

    def write_error(self, status_code, **kwargs):
        global G_program_options
        buf = '<html><body>Unhandled error in ThHandler</body></html>'
        try:
            this_err_cls = None
            this_err = ''
            this_trackback = None
            lines = []

            if 'exc_info' in kwargs:
                this_err_cls, this_err, this_trackback = kwargs['exc_info']

            if not this_err and 'message' in kwargs:
                this_err = kwargs['message']

            if status_code == 404:
                buf = '<html><body>Error 404:  File not found</body></html>'
            else:
                if 'exc_info' in kwargs:
                    for line in traceback.format_exception(this_err_cls, this_err, this_trackback):
                        lines.append(line)

                buf = '<html><body><p>Sorry, but you encountered an error at {}.</p>' \
                      '<p>Click <a href="{}">here</a> to log in and try again.</p>' \
                      '<p>{}</p><p>{}</p></body></html>'
                buf = buf.format(
                    str(datetime.datetime.now()),
                    G_program_options.server_prefix + '/logout',
                    str(this_err),
                    str(lines)
                )

        finally:
            self.write(buf)
            self.finish()

            if self.session is not None:
                self.session.finished()

    def process_uploaded_files(self):

        def process_file(bindata=None, filename=None, file_obj=None, fieldname=None, filetype=None):
            buf = None

            if bindata is not None:
                buf = '0x' + binascii.hexlify(bindata).decode('ascii')
            elif file_obj is not None:
                buf = '0x' + binascii.hexlify(file_obj['body']).decode('ascii')
                filename = file_obj['filename']
                filetype = file_obj['content_type']

                # fileProc = ThStoredProc('theas.spinsHTTPFiles', self.session)
                # if fileProc.is_ok:
                #    if bindata is not None:
                #        buf = '0x' + binascii.hexlify(bindata).decode('ascii')
                #        filename = 'body'
                #    else:
                #        buf = '0x'.encode('ascii') + binascii.hexlify(file_obj['body']).decode('ascii')
                #        filename = this_file['filename']

                # fileProc.bind(fieldname, _mssql.SQLVARCHAR, '@FieldName')
                # fileProc.bind(this_filename, _mssql.SQLVARCHAR, '@FileName')
                # fileProc.bind(buf, _mssql.SQLVARCHAR, '@FileCharData')
                # should work, but does not: #fileProc.bind(this_file['body'], _mssql.SQLVARBINARY, '@FileData')
                # fileResultValue = fileProc.execute()

                # callproc() is broken as of 6/16/2015, in that it truncates long values:
                # https://github.com/pymssql/pymssql/issues/275
                # So we are forced to use execute instead

            sql_str = "exec theas.spinsHTTPFiles @FieldName={this_fieldname}, @FileName={this_filename}, @FileType={this_filetype}, @FileData={this_filedata}".format(
                this_fieldname='\'' + fieldname + '\'' if fieldname else 'NULL',
                this_filename='\'' + filename + '\'' if filename else 'NULL',
                this_filetype='\'' + filetype + '\'' if filename else 'NULL',
                this_filedata=buf if buf else 'NULL'
            )

            self.session.sql_conn.execute_non_query(sql_str)

        if self.session.sql_conn is None or not self.session.sql_conn.connected:
            self.session.log('POST Files', 'Process_uploaded_files(', 'New connection')
            self.session.init_session()

        if self.request.headers.get('Content-Type') == 'application/octet-stream':
            self.session.log('POST Files', 'Delivering binary body to SQL')
            process_file(bindata=self.request.body,
                         filename=self.request.headers.get('X-File-Name'),
                         filetype=self.request.headers.get('X-File-Type')
                         )

        if len(self.request.files) > 0:
            self.session.log('POST Files', 'Delivering upload files to SQL')

            # pass upload files to SQL
            for this_file_field in list(self.request.files.keys()):
                for this_file in self.request.files[this_file_field]:
                    process_file(file_obj=this_file, fieldname=this_file_field)

    def get_template(self, resource_code):
        global G_cached_resources
        global G_program_options

        # Get template
        template_str = None

        resultset_str = None

        resource = None

        self.session.log('Resource', 'Fetching resource ', resource_code)
        resource = G_cached_resources.get_resource(resource_code, self.session)

        if resource is None:
            if template_str is None:
                msg = 'Could not load {} from the database.  '.format(
                    'default template' if resource_code is None else 'template "{}"'.format(resource_code)
                ) + ' Probably this user is not configured to use this server.' + \
                      '<p>Click <a href="{}">here</a> to log in and try again.</p>'.format(
                          G_program_options.server_prefix + '/logout')

                template_str = '<html><body>' + msg + '</body></html/>'

        else:
            template_str = resource.data

            if resource is not None and resource.exists and resource.resource_code != LOGIN_RESOURCE_CODE and \
                    resource.render_jinja_template and self.session.current_resource != resource:
                # We may have retrieved a cached resource.  Set current_resource.
                self.session.current_resource = resource

            self.session.current_template_str = template_str

            if template_str is None or len(template_str) == 0:
                msg = 'Could not load {} from the database.  '.format(
                    'default template' if resource_code is None else 'template "{}"'.format(resource_code)
                ) + ' Empty template was returned.' + \
                      '<p>Click <a href="{}">here</a> to log in and try again.</p>'.format(
                          G_program_options.server_prefix + '/logout')

                template_str = '<html><body>' + msg + '</body></html>'

        return template_str, resource

    def get_data(self, resource, suppress_resultsets=False):
        # Get actual quest data

        had_error = False

        self.session.comments = 'ThHandler.get_data'

        # Always initialize data--even if there is no APIStoredProc to call.
        # This way a Jinja template can always access data._Theas
        this_data = self.session.init_template_data()

        # serialize form parameters (excluding theas: parameters) to pass into the stored procedure
        form_params = self.request.body_arguments

        form_params_str = ''
        for key in form_params:
            if not key.startswith('theas:'):
                this_val = form_params[key]

                if isinstance(this_val, list) and len(this_val) > 0:
                    this_val = this_val[0]

                if isinstance(this_val, bytes):
                    this_val = this_val.decode('utf-8')
                elif this_val:
                    this_val = str(this_val)

                form_params_str = form_params_str + key + '=' + urlparse.unquote(this_val) + '&'

        # serialize theas paramters to pass into the stored procedure
        theas_params_str = self.session.theas_page.serialize()

        proc = None

        if resource and resource.api_stored_proc:
            proc = ThStoredProc(resource.api_stored_proc, self.session)

            try:
                if proc.is_ok:
                    try:
                        proc.refresh_parameter_list()
                    except:
                        self.session.logout()
                        raise

                # if '@QuestGUID' in proc.parameter_list and self.session.theas_page.get_value('questGUID') is not None:
                #    proc.bind(self.session.theas_page.get_value('questGUID'), _mssql.SQLCHAR, '@QuestGUID')

                # if '@StepGUID' in proc.parameter_list and self.session.theas_page.get_value('stepGUID') is not None:
                #    proc.bind(self.session.theas_page.get_value('stepGUID'), _mssql.SQLCHAR, '@StepGUID')

                # if '@StepDefID' in proc.parameter_list and self.session.theas_page.get_value('stepDefID') is not None:
                #    proc.bind(self.session.theas_page.get_value('stepDefID'), _mssql.SQLCHAR, '@StepDefID')

                first_path_elem = self.request.path.split('/')[1]

                if '@Document' in proc.parameter_list:
                    this_document = None

                    if first_path_elem == 'r':
                        this_document = self.request.path.split('/')[2]
                    else:
                        this_document = self.request.path

                    if this_document is not None:
                        proc.bind(this_document, _mssql.SQLCHAR, '@Document')

                if '@PathFull' in proc.parameter_list:
                    proc.bind(self.request.path, _mssql.SQLCHAR, '@PathFull')

                if '@PathParams' in proc.parameter_list:
                    this_path = None

                    if first_path_elem == 'r':
                        this_path = "/".join(self.request.path.split('/')[3:])

                    if this_path is not None:
                        proc.bind(this_path, _mssql.SQLCHAR, '@PathParams')

                if '@HTTPParams' in proc.parameter_list:
                    proc.bind(self.request.query, _mssql.SQLCHAR, '@HTTPParams')

                if '@FormParams' in proc.parameter_list:
                    proc.bind(form_params_str, _mssql.SQLCHAR, '@FormParams')
                    # proc.bind(urlparse.urlencode(self.request.body_arguments, doseq=True), _mssql.SQLCHAR, '@FormParams')

                if '@HTTPHeaders' in proc.parameter_list:
                    headers_str = ''
                    this_dict = dict(self.request.headers)
                    for key in this_dict:
                        this_val = this_dict[key]

                        if isinstance(this_val, list) and len(this_val) > 0:
                            this_val = this_val[0]

                        if isinstance(this_val, bytes):
                            this_val = this_val.decode('utf-8')
                        elif this_val:
                            this_val = str(this_val)

                        headers_str = headers_str + '&' + key + '=' + urlparse.quote(this_val)

                    proc.bind(headers_str, _mssql.SQLCHAR, '@HTTPHeaders')

                if '@TheasParams' in proc.parameter_list:
                    # proc.bind(theas_params_str, _mssql.SQLCHAR, '@TheasParams', output=proc.parameter_list['@TheasParams']['is_output'])
                    # Would prefer to use output parameter, but this seems not to be supported by FreeTDS.  So
                    # we look to the resultest(s) returned by the stored proc instead.
                    proc.bind(theas_params_str, _mssql.SQLCHAR, '@TheasParams')

                if '@SuppressResultsets' in proc.parameter_list:
                    proc.bind(str(int(suppress_resultsets)), _mssql.SQLCHAR, '@SuppressResultsets')

                # Execute stored procedure
                proc_result = proc.execute(fetch_rows=False)

            except Exception as e:
                had_error = True

                # err_msg = self.format_error(e)
                err_msg = e.text.decode('ascii')

                self.session.theas_page.set_value('theas:th:ErrorMessage', '{}'.format(urlparse.quote(err_msg)))

        # if not suppress_resultsets:
        if not had_error:
            #  The stored procedure may return one or more resultsets.
            #  Resultsets may return a single row--most appropariately stored in a dictionary, or may contain many rows--most
            #  appropriately stored in a list of dictionaries.
            #
            #  For a single-row resultset stored in a dictionary, values can be accessed as:
            #    this_data['General']['MO_Number']
            #
            #  For multi-row resultsets stored in a list of dictionaries, values can be accessed  while looping through the
            #  list of rows (dictionaries), or for a particular row in the list, such as:
            #    this_data['rows'][0]['MO_Number']
            #
            #  resultsetStr contains a string of multiple lines, such as:
            #    resultset1
            #    resultest2:Field1,Field2,Field3
            #
            #  Each line in resultsetStr indicates a resultset.  If a : is present, this indicates a delimiter to a
            #  list of a subset of the list of fields contained in the resultset.  This is to make it easy to control
            #  the columns from a resultet that will be displayed, without hard-coding fields into a template.

            # Since we did call APIStoredProc to get data, add data._resultsetMeta
            this_data['_resultsetMeta'] = {}

            redirect_to = None
            history_go_back = False
            perform_authenticate_existing = False

            resultset_list = []

            resultset_strs = resource.api_stored_proc_resultset_str.splitlines()
            self.session.log('SQL', 'Expecting ' + str(len(resultset_strs)) + ' resultsets')

            # resultset_str is in the form:
            #   MyResultsetName:{max_rows}:{column1,column2}

            # {max_rows} is optional.  If present, will be an integer.  If equals 1, resultset will be stored in a
            # simple dictionary (not in a list of dictionaries).  If < 1, value is ignored.  If > 1, value limits
            # the number of rows stored in data passed to the the template.

            # {column1,column2} is optional.  If present, will be a comma-separated list of column names.  This list
            # will be used instead of the list of all columns returned in the resultset.  (i.e. will limit the
            # columns stored in the data passed to the template)

            this_resultset_info = {}

            for resultset_str in resultset_strs:
                this_resultset_fields = resultset_str.split(':')

                this_resultset_info = {}
                this_resultset_info['name'] = this_resultset_fields[0]
                this_resultset_info['max_rows'] = None

                this_data['_session'] = self.session
                this_data['_resultsetMeta'][this_resultset_fields[0]] = {}
                if len(this_resultset_fields) > 1:
                    collist_index = 1
                    if this_resultset_fields[1].isnumeric():
                        this_resultset_info['max_rows'] = int(this_resultset_fields[1])
                        collist_index = 2

                    if len(this_resultset_fields) > collist_index:
                        this_data['_resultsetMeta'][this_resultset_fields[0]]['columns'] = this_resultset_fields[
                            collist_index].split(',')
                        this_resultset_info['columns'] = this_data['_resultsetMeta'][this_resultset_fields[0]][
                            'columns']

                this_resultset_info['max_rows'] = this_resultset_info['max_rows']

                resultset_list.append(this_resultset_info)

            row = None
            for this_resultset_info in resultset_list:
                max_rows = this_resultset_info['max_rows']
                if max_rows is None:
                    max_rows = 0

                if max_rows == 1:
                    this_data[this_resultset_info['name']] = {}
                else:
                    this_data[this_resultset_info['name']] = []

                resultset = [row for row in self.session.sql_conn]

                row_count = 0
                for row in resultset:
                    row_count += 1
                    if (max_rows > 1) and (row_count > max_rows):
                        break
                    else:
                        if this_resultset_info['max_rows'] == 1:
                            this_data[this_resultset_info['name']] = row
                        else:
                            this_data[this_resultset_info['name']].append(row)

                self.session.log('SQL', 'Processed {} row(s) in resultest {}'.format(
                    str(len(this_data[this_resultset_info['name']]))
                    if this_data[this_resultset_info['name']] is list else 1,

                    this_resultset_info['name'])
                                 )

                if this_resultset_info['name'] in ('General'):  # should we also include 'general' here??
                    if row is not None:
                        if 'TheasParams' in row:
                            theas_params_str = row['TheasParams']
                            if theas_params_str:
                                # Incorporate any Theas control changes from SQL, so these values can be used
                                # when rendering the template.
                                self.session.theas_page.process_client_request(buf=theas_params_str, accept_any=True,
                                                                               from_stored_proc=True)

                                if theas_params_str.find('th:LoggedIn=') >= 0:
                                    # Stored procedure is indicating authentication status changed.  Retrieve
                                    # current session info.
                                    perform_authenticate_existing = True

                                # Since Theas controls may have changed, update the copy in data._Theas
                                this_data['_Theas']['theasParams'] = self.session.theas_page.get_controls()

                        if 'ErrorMessage' in row:
                            if not row['ErrorMessage'] is None and row['ErrorMessage'] != '':
                                self.session.theas_page.set_value('theas:th:ErrorMessage', row['ErrorMessage'])

                        if 'Cookies' in row:
                            cookies_str = row['Cookies']
                            # Cookies returns a string like name1=value1&name2=value2...

                            if cookies_str:
                                for this_pair in cookies_str.split('&'):
                                    this_name, this_value = this_pair.split('=')

                                    if this_name == SESSION_COOKIE_NAME:
                                        self.cookie_st = this_value
                                    elif this_name == USER_COOKIE_NAME:
                                        self.cookie_usertoken = this_value
                                    else:
                                        self.clear_cookie(this_name, path='/')
                                        self.set_cookie(this_name, this_value, path='/')

                                self.write_cookies()
                                self.session.log('Cookies', 'Updating cookies as per stored procedure E')
                                self.cookies_changed = True

                        # Check to see if stored proc indicates we should redirect
                        if 'RedirectTo' in row:
                            redirect_to = row['RedirectTo']

                        # Check to see if stored proc indicates we should go back in history
                        if 'DoHistoryGoBack' in row:
                            if str(row['DoHistoryGoBack']) == '1':
                                history_go_back = True

                        if 'Filename' in row:
                            self.filename = row['Filename']

                        if 'HTTPHeaders' in row:
                            header_str = row['HTTPHeaders']
                            # HTTPHeaders returns a string like name1=value1&name2=value2...

                            if header_str:
                                for this_pair in header_str.split('&'):
                                    this_name, this_value = this_pair.split('=')
                                    self.set_header(this_name, this_value)

                                self.session.log('Headers', 'Updating HTTP headers as per stored procedure E')

                have_next_resultset = self.session.sql_conn.nextresult()
                if not have_next_resultset:
                    break

                    # stored proc may have updated Theas controls, so update the copy in data._Theas
                    # this_data['_Theas']['theasParams'] = self.session.theas_page.get_controls()

            # One of our stored procedure resultsets indicated that authentication had been performed.
            # Have the session retrieve existing authentication from the database.
            if perform_authenticate_existing:
                self.session.log('Auth', 'Authenticating due to resource stored proc th:LoggedIn')
                self.session.authenticate(retrieve_existing=True)

            self.session.comments = None
            return this_data, redirect_to, history_go_back
        else:
            self.session.comments = None
            return None, None, None

    @run_on_executor
    def get_data_background(self, resource, suppress_resultsets=False):
        return self.get_data(resource, suppress_resultsets=suppress_resultsets)

    @run_on_executor
    def authenticate_user_background(self, u, pw):
        return self.session.authenticate(username=u, password=pw)

    @run_on_executor
    def build_login_screen_background(self):
        return self.session.build_login_screen()

    def do_render_response(self, this_resource=None):
        # Gets data and renders template.  Used by GET only.
        # Note that this method will be called whenever the resource indicates that there is an APIStoredProc,
        # even if a Jinja template is not actually used.

        buf = None
        this_data = None
        redirect_to = None
        history_go_back = False

        if this_resource is not None:

            if this_resource.api_stored_proc or this_resource.render_jinja_template:
                this_data, redirect_to, history_go_back = self.get_data(this_resource)

            if this_resource.render_jinja_template:
                # resource indicates that we should render a Jinja template
                buf = self.session.theas_page.render(this_resource.data, data=this_data)
            elif this_resource.api_stored_proc:
                # resource does not indicate that we should render a Jinja template (but does specify an
                # api stored proc) so just return the raw content retrieved by get_data
                if not self.session.theas_page.get_value('theas:th:ErrorMessage') and \
                                'General' in this_data and \
                                'Content' in this_data['General']:
                    buf = this_data['General']['Content']

        return buf, redirect_to, history_go_back

    @run_on_executor
    def do_render_response_background(self, this_resource=None):
        return self.do_render_response(this_resource=this_resource)

    # @run_on_executor
    # def get_resource_background(self, resource_code, th_session, for_public_use=False, all_static_blocks=False, none_if_not_found=True, from_file=None):
    #    global G_cached_resources
    #    return G_cached_resources.get_resource(resource_code, th_session, for_public_use=for_public_use, all_static_blocks=all_static_blocks, none_if_not_found=none_if_not_found, from_file=from_file)

    # Background disabled
    def get_resource_background(self, resource_code, th_session, for_public_use=False, all_static_blocks=False,
                                none_if_not_found=True, from_file=None):
        return G_cached_resources().get_resource(resource_code, th_session, for_public_use=for_public_use,
                                                 all_static_blocks=all_static_blocks,
                                                 none_if_not_found=none_if_not_found, from_file=from_file)

    def do_post(self, *args, **kwargs):

        handled = False

        # Do everything that is needed to process an HTTP post on an authenticated session
        buf = None  # we return buf to the caller
        redirect_to = None
        history_go_back = False
        this_data = None

        this_page = None
        next_page = None
        next_page_query = None

        self.session.theas_page.process_client_request(request_handler=self, accept_any=False)
        self.process_uploaded_files()

        # self.session.theas_page.controls['ctrlinputHelloWorld'].value = self.get_body_argument('theasParams', 'NONE')

        if self.get_argument('DoHistoryGoBack', default='0') == '1':
            history_go_back = True

        cmd = None
        if self.get_arguments('cmd'):
            cmd = self.get_argument('cmd')
        if not cmd and self.get_body_arguments('cmd'):
            cmd = self.get_body_argument('cmd')

        # this_page = self.session.theas_page.get_value('th:CurrentPage')
        # if not this_page:
        this_page = self.request.path.rsplit('/', 1)[1]
        if '?' in this_page:
            this_page = this_page[:this_page.find('?')]

            # if self.session.current_resource and this_page == self.session.current_resource.resource_code:
            #    pass
            # else:
            # Browser provided a different value for current_page.  Perhaps the user used the back button?
            # In any case, we want to use the correct stored procedure for this request.  Getting the template
            # will set that from us.
        template_str, this_resource = self.get_template(this_page)

        if self.deferred_xsrf:
            self.session.theas_page.set_value('th:PerformUpdate', '1')

        if cmd is not None:
            pass
            buf = '<html><body>Parameter cmd provided, but not implemented.</body></html>'
        else:
            if self.session.theas_page.get_value('th:PerformUpdate') == '1':
                # Before we can process next_page, we need to submit to process this_page post
                self.session.log('Data', 'Performing update of posted data')

                if self.session and self.session.current_resource:
                    this_data, redirect_to, history_go_back = \
                        self.get_data(self.session.current_resource, suppress_resultsets=True)
                    self.session.theas_page.set_value('th:PerformUpdate', '0')

                    # determine what page is being requested
                    next_page = self.session.theas_page.get_value('th:NextPage')
                    if next_page in ('None', 'default', 'index'):
                        next_page = DEFAULT_RESOURCE_CODE
                    if not next_page:
                        next_page = this_page

            if redirect_to:
                self.session.log('Nav', 'PerformUpdate stored proc sent redirect to {}'.format(redirect_to))
            else:
                self.session.log('Nav', 'After PerformUpdate stored proc th:NextPage={}'.format(next_page))
                # Force a redirect
                redirect_to = next_page
                # Perform redirect after processing the post (i.e. Post-Redirect-Get PRG) pattern
                # Redir will be to redirect_to if set, else will be to next_page.
                # This is true even if FORCE_REDIR_AFTER_POST == False, because th:PerformUpdate == 1

            if redirect_to:
                pass
            else:
                # determine what page is being requested
                next_page = self.session.theas_page.get_value('th:NextPage')
                if next_page and '?' in next_page:
                    next_page = next_page[:next_page.find('?')]
                if next_page in ('None', 'default', 'index'):
                    next_page = DEFAULT_RESOURCE_CODE
                if not next_page:
                    next_page = this_page

                if FORCE_REDIR_AFTER_POST:
                    # We want to force a redirect even if next_page == this_page because this request
                    # is a POST, and we only want to serve up content on a GET
                    redirect_to = next_page

            if not redirect_to:
                self.session.log('Nav', 'Before processing for POST th:NextPage={}'.format(next_page))

                if not self.session.current_resource or next_page != self.session.current_template_str:
                    template_str, this_resource = self.get_template(next_page)
                else:
                    this_resource = self.session.current_resource

                # if not self.deferred_xsrf, then XSRF token has already been validated by Tornado
                xsrf_ok = not self.deferred_xsrf
                xsrf_message = ''

                if not xsrf_ok:
                    # XSRF token has not yet been validated
                    if this_resource is not None and this_resource.skip_xsrf:
                        # resource indicates that XSRF token validation is not needed
                        xsrf_ok = True
                    else:
                        # resource indicates that XSRF token validation is required...so do it now
                        try:
                            tornado.web.RequestHandler.check_xsrf_cookie(self)
                            xsrf_ok = True
                        except Exception as e:
                            # Tornado normally just raises an exception, such as:
                            #   raise HTTPError(403, "'_xsrf' argument missing from POST")
                            xsrf_ok = False
                            xsrf_message = str(e)

                if not xsrf_ok:
                    ThSession.cls_log('xsrf', xsrf_message)
                    self.send_error(status_code=403, message=xsrf_message)
                    handled = True
                else:
                    if this_resource is not None:
                        if this_resource.requires_authentication and not self.session.logged_in:
                            self.session.log('Auth', 'Resource requires auth and user not logged in')
                            # still not logged in:  present login screen
                            self.session.bookmark_url = this_resource.resource_code
                            buf = self.session.build_login_screen()
                            self.session.log('Auth', 'Sending login screen')

                        else:
                            if this_resource.on_before:
                                this_function = getattr(TheasCustom, this_resource.on_before)
                                if this_function is not None:
                                    handled = this_function(self, args, kwargs)

                            if not handled and not history_go_back and self.session is not None:
                                # render output using template and data

                                if this_resource and this_resource.api_stored_proc:
                                    self.session.log('Data', 'Calling get_data')
                                    this_data, redirect_to, history_go_back = self.get_data(this_resource)

                                if this_resource and this_resource.render_jinja_template and redirect_to is None and not history_go_back:
                                    self.session.log('Render', 'Calling theas_page.render')
                                    buf = self.session.theas_page.render(template_str, data=this_data)
                                    self.session.log('Render', 'Done with theas_page.render')
                                else:
                                    # template_str does not need to be merged with data
                                    buf = template_str

                                if this_resource and this_resource.on_after:
                                    this_function = getattr(TheasCustom, this_resource.on_after)
                                    if this_function is not None:
                                        handled = this_function(self, args, kwargs)

        return buf, redirect_to, history_go_back, handled

    @run_on_executor
    def do_post_background(self, *args, **kwargs):
        return self.do_post(args, kwargs)

    @tornado.gen.coroutine
    def wait_for_session(self, seconds_to_wait=10, write_to_cookie=True):
        this_sess = None

        orig_cookie_session_token = self.cookie_st

        # We might have a session token in a cookie.  But we might also have a session token in
        # a form field, or in an HTTP header.  Which one do we trust?  Rationale:  We'd like the
        # most explicit one to be used, i.e.: query string, form field, header, cookie in that
        # order.

        # But in the case of an async request from a stale browser request the session token
        # provided in the form field might be old, and the cookie value might be new.
        # The browser really must update the session token in the form if an async response
        # provides an updated cookie value.

        '''
        if self.get_arguments(SESSION_COOKIE_NAME):
            # Look for session token in request
            this_session_token = self.get_argument(SESSION_COOKIE_NAME)
            if USE_SECURE_COOKIES:
                this_session_token = tornado.web.decode_signed_value(COOKIE_SECRET, SESSION_COOKIE_NAME, this_session_token)

        elif SESSION_HEADER_NAME in self.request.headers:
            this_session_token = self.request.headers[SESSION_HEADER_NAME]
        else:
            # fall back to cookie
            this_session_token = orig_cookie_session_token
        '''

        # The rudimentary partial support for session tokens via forms or headers was removed on 9/7/2018, pending
        # reconsideration of the best way to handle this.

        this_session_token = orig_cookie_session_token

        give_up = False
        failed_to_lock = False
        start_waiting = time.time()
        while this_sess is None and not give_up:
            this_sess, failed_to_lock = ThSession.get_session(session_token=this_session_token,
                                                              handler_guid=self.handler_guid,
                                                              defer_sql=True,
                                                              comments='ThHandler.wait_for_session')
            if failed_to_lock and this_sess is None:
                yield tornado.gen.sleep(.500)
                give_up = (time.time() - start_waiting) / 1000 > seconds_to_wait
            else:
                give_up = True

        if this_sess:
            this_sess.current_handler = self
            this_sess.current_xsrf_form_html = self.xsrf_form_html()

            if USE_SESSION_COOKIE and write_to_cookie:
                # next_url = '/'
                if orig_cookie_session_token != this_sess.session_token:
                    self.cookie_st = this_sess.session_token
                    ThSession.cls_log('Cookies',
                                      'Updating cookie {} wait_for_session() gave different token ({} vs {})'.format(
                                          SESSION_COOKIE_NAME, orig_cookie_session_token, this_sess.session_token))

            # silently re-authenticate if needed and there is a user cookie
            if not this_sess.logged_in and REMEMBER_USER_TOKEN:
                # try to auto-login if there is a user cookie
                if self.cookie_usertoken:
                    ThSession.cls_log('Sessions', 'Reauthenticating user from usertoken cookie')
                    this_sess.authenticate(user_token=self.cookie_usertoken)
                    if not this_sess.logged_in:
                        ThSession.cls_log('Sessions', 'FAILED to reauthenticate user from usertoken cookie')
                        self.cookie_usertokeon = None
                        ThSession.cls_log('Cookies',
                                          'Updating cookie {} wait_for_session() could not authenticate original usertoken'.format(
                                              USER_COOKIE_NAME))

            else:
                self.cookie_st = None


        else:
            ThSession.cls_log('Sessions', 'Failed to obtain session in wait_for_session()')

        self.write_cookies()

        this_sess.comments = None

        return this_sess

    @tornado.gen.coroutine
    def head(self, *args, **kwargs):
        # Partial support for HTTP HEAD requests
        # Currently only supports cached public resources that are in cache

        # try to find required resource
        resource_code = args[0]
        resource = None

        if resource_code and resource_code.count('.') >= 2:
            # A versioned filename, i.e. my.23.css for version #23 of my.css
            # We just want to cut out the version, and return the unversioned
            # filename as the resource code (i.e. my.css)

            # That is, Theas will always / only serve up the most recent version
            # of a resource.  There is not support for serving up a particular
            # historical version.  The version number in the file name is merely
            # for the browser's benefit, so that we can "cache bust" / have the
            # browser request the latest version even if it has an old version in
            # cache.

            # For this reason, we don't really need to inspect the resources.
            # We need only manipulate the resource_code to strip out the version
            # number.
            segments = resource_code.split('.')
            if len(segments) >= 3 and 'ver' in segments:
                ver_pos = segments.index('ver')
                if ver_pos > 0:
                    resource_code = '.'.join(segments[:ver_pos]) + '.' + '.'.join(segments[ver_pos + 2:])

        self.set_header('Server', 'Theas/01')

        th_session = None

        # Look up response info.
        # Will not return info for dynamic requests (only static requests for SysWebResource or attachment)
        response_info = self.get_response_info(resource_code, th_session, sessionless=True)

        if response_info is None:
            self.send_error(status_code=405)
        else:
            self.set_header('accept-ranges', 'bytes')  # but not really...
            self.set_header('Content-Type', response_info.content_type)
            self.set_header('Content-Length', response_info.content_length)
            self.set_header('Date', response_info.current_date)
            self.set_header('Expires', response_info.content_expires)
            self.set_header('Cache-Control', response_info.cache_control)
            self.set_header('Last-Modified', response_info.date_updated)
            self.set_header('Content-Disposition', 'inline; filename="{}"'.format(response_info.content_filename))
            if response_info.etag:
                self.set_header('Etag', response_info.etag)


    @tornado.gen.coroutine
    def post(self, *args, **kwargs):
        # MAIN ENTRY POINT FOR HTTP POST REQUEST

        ThSession.cls_log('POST', '*******************************')

        self.session = yield self.wait_for_session()

        self.session.log('POST Request', 'Received request for: {}'.format(self.request.path))
        self.session.log('Authentication' 'User is logged in' if self.session.logged_in else 'User is NOT logged in')

        this_finished = False
        handled = False

        buf = None
        redirect_to = None
        history_go_back = False

        if self.session is not None:
            # This is a post.  The next page may be specified in a form field theas:th:NextPage.
            if not self.session.logged_in and self.get_arguments('u') and self.get_arguments('pw'):
                # The requested page is the login screen
                error_message = ''
                if USE_WORKER_THREADS:
                    success, error_message = yield self.authenticate_user_background(self.get_argument('u'),
                                                                                     self.get_argument('pw'))
                else:
                    success, error_message = self.session.authenticate(username=self.get_argument('u'),
                                                                       password=self.get_argument('pw'))

                # if not self.session.authenticate(username=self.get_argument('u'), password=self.get_argument('pw')):
                if not success:
                    # authentication failed, so send the login screen
                    self.session.theas_page.set_value('theas:th:ErrorMessage', 'Error: {}.'.format(error_message))
                    buf = self.session.build_login_screen()
                    self.write(buf)

                else:
                    # Authentication succeeded, so continue with redirect
                    # self.session.theas_page.set_value('theas:th:ErrorMessage', '')

                    if self.session.bookmark_url:
                        self.session.log('Proceeding with bookmarked page', self.session.bookmark_url)
                        self.get_template(self.session.bookmark_url)
                        self.session.bookmark_url = None

                    else:
                        self.session.log('Response', 'Sending clientside redir after login page success')
                        self.write(self.session.clientside_redir())

            if not handled:

                # Handle the actual form processing here. When done, we will persist session data and redirect.
                if USE_WORKER_THREADS:
                    buf, redirect_to, history_go_back, handled = yield self.do_post_background(args, kwargs)
                else:
                    buf, redirect_to, history_go_back, handled = self.do_post(args, kwargs)

                if not handled:
                    if redirect_to is not None:
                        if self.cookies_changed:
                            # must perform a client-side redirect in order to set cookies
                            self.session.log('Session', 'Sending client-side redirect to: ({}) after do_post()'.format(
                                redirect_to))
                            self.write(self.session.clientside_redir(redirect_to))
                            self.session.finished()
                        else:
                            # can send a normal redirect, since no cookies need to be written
                            this_finished = True
                            self.session.log('Session',
                                             'Sending normal redirect to: ({}) after do_post()'.format(redirect_to))
                            self.session.finished()
                            self.redirect(redirect_to)

                    else:
                        if history_go_back and self.session is not None:

                            if len(self.session.history) > 0:
                                this_history_entry = self.session.history.pop()

                                self.session.theas_page.set_value('theas:th:NextPage', this_history_entry['PageName'])

                                self.session.log('Response', 'Sending clientside redir due to history_go_back')
                                this_finished = True
                                buf = self.session.clientside_redir()

                        if buf is None:
                            buf = '<html><body>No content to send in ThHandler.post()</body></html>'
                        self.write(buf)

                        # CORS
                        self.set_header('Access-Control-Allow-Origin', '*')  # allow CORS from any domain
                        self.set_header('Access-Control-Max-Age', '0')  # disable CORS preflight caching

                        self.session.log('Response', 'Sending response')

        else:
            self.write('<html><body>Error: cannot process request without a valid session</body></html>')

        if not handled and not this_finished:
            if self.session and self.session.locked:
                self.session.finished()

            self.finish()

        self.session = None

    @tornado.gen.coroutine
    def get(self, *args, **kwargs):
        ##########################################################
        # MAIN ENTRY POINT FOR HTTP GET REQUEST
        ##########################################################
        global G_cached_resources

        if self.session:
            self.session.comments = 'ThHandler.get'

        # do everything needed to process an HTTP GET request

        def write_log(sess, category, *args):
            if sess is not None:
                sess.log(category, *args)
            else:
                ThSession.cls_log(category, *args)

        handled = False
        buf = None
        redirect_to = None
        history_go_back = False

        # try to find required resource
        resource_code = None
        resource = None

        request_path = None
        if len(args) >= 0:
            request_path = args[0]

        if request_path is not None and request_path.split('/')[0] == 'r':
            # Special case:  an "r" as the first segment of the path, such as:
            # r/resourcecode/aaa/bbb
            # indicates that the second segment is to be the resource code.
            # This allows URLs such as /r/img/myimg.jpg to be handled dynamically:  the resource img is
            # loaded, and then myimg.jpg is passed in.  (Otherwise the resource would be taken to be
            # img/myimg.jpg
            resource_code = request_path.split('/')[1]
        else:
            resource_code = request_path
            if resource_code and resource_code.count('.') >= 2:
                # A versioned filename, i.e. my.23.css for version #23 of my.css
                # We just want to cut out the version, and return the unversioned
                # filename as the resource code (i.e. my.css)

                # That is, Theas will always / only serve up the most recent version
                # of a resource.  There is not support for serving up a particular
                # historical version.  The version number in the file name is merely
                # for the browser's benefit, so that we can "cache bust" / have the
                # browser request the latest version even if it has an old version in
                # cache.

                # For this reason, we don't really need to inspect the resources.
                # We need only manipulate the resource_code to strip out the version
                # number.
                segments = resource_code.split('.')
                if len(segments) >= 3 and 'ver' in segments:
                    ver_pos = segments.index('ver')
                    if ver_pos > 0:
                        resource_code = '.'.join(segments[:ver_pos]) + '.' + '.'.join(segments[ver_pos + 2:])

        # note: self.session is probably not yet assigned

        # A request for a cached public resource does not need a database connection.
        # We can serve up such requests without even checking the session.
        # If we do not check the session, multiple simultaneous requests can be processed,
        if resource_code or self.session:
            resource = G_cached_resources.get_resource(resource_code, self.session, none_if_not_found=True)

        # see if the resource is public (so that we can serve up without a session)
        if resource is not None and resource.exists and resource.is_public and \
                not resource.render_jinja_template and \
                not resource.on_before and not resource.on_after:
            # note:  resource.data will usually be str but might be bytes
            ThSession.cls_log('CachedGET', 'Serving up cached resource', resource_code)
            buf = resource.data

        else:
            # Retrieve or create a session.  We want everyone to have a session (even if they are not authenticated)
            # We need to use the session's SQL connection to retrieve the resource

            ThSession.cls_log('GET', '*******************************')
            ThSession.cls_log('GET', args[0])

            self.session = yield self.wait_for_session()

            if self.session is None:
                ThSession.cls_log('GET Error', 'No session.  Cannot continue to process request.')
                self.write('<html><body>Error: cannot process request without a valid session</body></html>')
            else:
                # we have a session, but are not necessarily logged in
                self.session.log('GET', 'Have session')
                self.session.log('GET', 'Received request for: {}'.format(self.request.path))

                self.session.log('Auth' 'User is logged in' if self.session.logged_in else 'User is NOT logged in')

                # Take logged-in userss back to where they were
                if not resource_code and self.session.logged_in:
                    resource = self.session.current_resource

                if not resource_code and DEFAULT_RESOURCE_CODE and not self.session.logged_in:
                    # resource_code was not provided and user is not logged in:  use default resource
                    # If the user is logged in, we want get_resource to select the appropriate
                    # resource for the user.
                    resource_code = DEFAULT_RESOURCE_CODE

                if resource is None or not resource.exists:
                    # Call get_resources again, this time with a session
                    resource = G_cached_resources.get_resource(resource_code, self.session, none_if_not_found=True)

                    if resource is None or not resource.exists:
                        # If the user is logged in, but resource_code is not specified, we explicitly set get_default_resource
                        # so that the stored proc can look up the correct resource for us.
                        # This change was made 9/21/2017 to correct a problem that led to 404 errors resulting in serving
                        # up the default resource.
                        self.session.log('Get Resource', 'Logged in?', self.session.logged_in)
                        self.session.log('Get Resource', 'resource_code', resource_code if resource_code is not None else 'None')
                        resource = G_cached_resources.get_resource(resource_code, self.session, none_if_not_found=True,
                                                                   get_default_resource=self.session.logged_in)

                if resource is not None and resource.exists and resource.resource_code != LOGIN_RESOURCE_CODE and \
                        resource.render_jinja_template:
                    # We may have retrieved a cached resource.  Set current_resource.
                    self.session.current_resource = resource

                if resource is not None and resource.exists:
                    if resource.on_before:
                        this_function = getattr(TheasCustom, resource.on_before)
                        if this_function:
                            handled = this_function(self, args, kwargs)

                    if resource.requires_authentication and not self.session.logged_in:

                        if not self.session.logged_in:
                            # still not logged in:  present login screen
                            self.session.bookmark_url = resource.resource_code
                            # self.session.bookmark_url = self.request.path.rsplit('/', 1)[1]
                            self.session.current_resource = resource

                            # NOTE:  this needs further thought.
                            # Sometimes it is nice to send the login screen in response to a request
                            # for an auth-required resource if the user is not logged in.
                            # Other times, we might prefer to send a 404 error, or to navigate
                            # to index, etc. (consider <img src="xxx">, <audio>, etc.)
                            buf = self.session.build_login_screen()

                            write_log(self.session, 'Response', 'Sending login screen')

                    if buf is None and (not resource.requires_authentication or self.session.logged_in):
                        if resource.api_stored_proc or resource.render_jinja_template:
                            buf, redirect_to, history_go_back = self.do_render_response(this_resource=resource)
                        else:
                            # note:  resource.data will usually be str but might be bytes
                            buf = resource.data

                    if resource.on_after:
                        this_function = getattr(TheasCustom, resource.on_after)
                        if this_function:
                            handled = this_function(self, args, kwargs)

        if not handled:
            if redirect_to is not None:
                if self.cookies_changed:
                    # must perform a client-side redirect in order to set cookies
                    self.write(self.session.clientside_redir(redirect_to))
                    self.session.finished()
                else:
                    # can send a normal redirect, since no cookies need to be written
                    self.session.finished()
                    buf = None
                    self.redirect(redirect_to)

            else:
                if history_go_back:
                    pass
                else:
                    if buf is None:
                        write_log(self.session, 'Response',
                                  'Sending 404 error in response to HTTP GET request for {}'.format(resource_code))
                        self.send_error(status_code=404)

            if buf is not None:
                write_log(self.session, 'Response', 'Sending response to HTTP GET request for {}'.format(resource_code))

                self.write(buf)

                # CORS
                self.set_header('Access-Control-Allow-Origin', '*')  # allow CORS from any domain
                self.set_header('Access-Control-Max-Age', '0')  # disable CORS preflight caching

                if resource is not None and resource.is_public:
                    self.set_header('Cache-Control', ' max-age=900')  # let browser cache for 15 minutes
                else:
                    self.set_header('Cache-Control', 'Cache-Control: no-store, no-cache, must-revalidate, max-age=0')
                    self.add_header('Cache-Control', 'Cache-Control: post-check=0, pre-check=0')
                    self.add_header('Cache-Control', 'Pragma: no-cache')

                if self.filename is not None:
                    self.set_header('Content-Type', theas.Theas.mimetype_for_extension(self.filename))
                    self.set_header('Content-Disposition', 'inline; filename=' + self.filename)

                elif resource is not None and resource.filename:
                    if resource.filetype:
                        self.set_header('Content-Type', resource.filetype)
                    else:
                        self.set_header('Content-Type', theas.Theas.mimetype_for_extension(resource.filename))
                    self.set_header('Content-Disposition', 'inline; filename=' + resource.filename)

                self.finish()

            if self.session is not None:
                self.session.comments = None
                self.session.finished()

                self.session.log('Request',
                                 'At end, Current Resource is {}'.format(
                                     self.session.current_resource.resource_code
                                     if self.session.current_resource
                                     else 'Not Assigned!'
                                 ))




                # def write_error(self, status_code, **kwargs):
                #    msg = ''
                #    if self.this_sess.sql_conn == None:
                #        msg = 'There is no database connection.  '
                #    msg = msg + e.args[0] + ' ' + e.message
                #    print('Error: ' + msg)
                #    self.write('<html><body>Sorry, you encountered an error.  Error message:  ' + msg + '</body></html>')
                #    self.finish()
                #    #if 'exc_info' in kwargs and issubclass(kwargs['exc_info'][0], ForbiddenException):
                #    #    self.set_status(403)

                # def _handle_request_exception(self, e):

    @tornado.gen.coroutine
    def options(self, resource_code=None, *args, **kwargs):
        # CORS
        self.set_header('Access-Control-Allow-Origin', '*')  # allow CORS from any domain
        self.set_header('Access-Control-Allow-Methods', 'POST, GET, PUT, DELETE')
        self.set_header('Access-Control-Allow-Headers', 'X-Requested-With, Content-Type')
        self.set_header('Access-Control-Max-Age', '0')  # disable CORS preflight caching

    def data_received(self, chunk):
        pass


# -------------------------------------------------
# ThHandler_Attach attachment handler
# -------------------------------------------------
class ThHandler_Attach(ThHandler):
    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)

    def __del__(self):
        self.session = None

    def retrieve_attachment(self):
        # Do everything that is needed to process a request for a quest attachment
        self.session.log('Attach', 'Retrieving quest attachment')

        attachment = None
        attachment_guid = None
        filename = None
        filetype = None
        buf = None

        attachment_guid = self.get_argument('guid', default=None)

        if attachment_guid is None:
            attachment_guid = self.request.path.split('/')[-1]
            if attachment_guid.lower() == 'attach':
                attachment_guid = None

        if attachment_guid is not None:
            # Get attachment data from database
            proc = ThStoredProc('theas.spgetAttachment', self.session)
            if proc.is_ok:
                proc.bind(attachment_guid, _mssql.SQLCHAR, '@AttachmentGUID')

                proc_result = proc.execute(fetch_rows=False)
                for row in proc.th_session.sql_conn:
                    filename = row['Filename']
                    buf = row['AttachmentData']
                    if 'Filetype' in row:
                        filetype = row['Filetype']

        if buf is not None:
            attachment = {}
            attachment['filename'] = filename
            attachment['data'] = buf
            attachment['filetype'] = filetype

        return attachment

    @run_on_executor
    def retrieve_attachment_background(self):
        return self.retrieve_attachment()

    def retrieve_webresource(self):
        global G_cached_resources

        # Do everything that is needed to process a request for a sys web resource
        self.session.log('Attach', 'Retrieving web resource')

        resource_code = None
        resource = None

        if self.get_arguments('rc'):
            resource_code = self.get_argument('rc')

            resource = G_cached_resources.get_resource(resource_code, self.session, for_public_use=True)

        return resource

    @run_on_executor
    def retrieve_webresource_background(self):
        return self.retrieve_webresource_background(self)

    @tornado.gen.coroutine
    def get(self, *args, **kwargs):
        # MAIN ENTRY POINT FOR ATTACH HTTP GET REQUEST

        # retrieve or create session
        ThSession.cls_log('Attach', '*******************************')
        ThSession.cls_log('Attach', args[0])

        self.session = yield self.wait_for_session(write_to_cookie=False)

        if self.session is not None:
            self.session.log('Attach', 'Have session')

            self.session.log('Attach',
                             'Current Resource is {}'.format(
                                 self.session.current_resource.resource_code
                                 if self.session.current_resource
                                 else 'Not Assigned!'
                             ))

            if self.get_arguments('rc'):
                if USE_WORKER_THREADS:
                    resource = yield self.retrieve_webresource_background()
                else:
                    resource = self.retrieve_webresource()

                self.session.log('Attach', 'Sending SysWebResource')
                self.write(resource.data)

                if resource.filetype:
                    self.set_header('Content-Type', resource.filetype)
                else:
                    self.set_header('Content-Type', theas.Theas.mimetype_for_extension(resource.filename))

                self.set_header('Content-Disposition', 'inline; filename=' + resource.filename)

            else:
                # if not self.session.logged_in:
                #    self.send_error(status_code=404)
                #    self.session.log('Response', 'Sending 404 for attachment request due to no login')
                # else:
                if USE_WORKER_THREADS:
                    attachment = yield self.retrieve_attachment_background()
                else:
                    attachment = self.retrieve_attachment()

                if attachment is not None:
                    self.session.log('Attach', 'Sending attachment response')
                    self.write(attachment['data'])
                    self.set_header('Content-Type', theas.Theas.mimetype_for_extension(attachment['filename']))

                    if attachment['filetype']:
                        self.set_header('Content-Type', attachment['filetype'])
                    else:
                        self.set_header('Content-Type', theas.Theas.mimetype_for_extension(attachment['filename']))

                    self.set_header('Content-Disposition', 'inline; filename=' + attachment['filename'])
                    self.finish()
                else:
                    self.send_error(status_code=404)

            self.session.finished()
            self.session = None

    def data_received(self, chunk):
        pass


# -------------------------------------------------
# TestThreadedHandler sample thread handler
# -------------------------------------------------
class TestThreadedHandler(ThHandler):
    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)

    def __del__(self):
        self.session = None

    def process_request(self):
        # This will be executed in 'executor' pool.
        return '<html><body>Made it to TestThreadedHandler.background_process_requesat!</body></html>'

    @run_on_executor
    def process_request_background(self):
        return self.process_request

    @tornado.gen.coroutine
    def get(self, *args, **kwargs):

        if USE_WORKER_THREADS:
            buf = yield self.process_request_background()
        else:
            buf = self.process_request()
        self.write(buf)
        self.finish()

    def data_received(self, chunk):
        pass


# -------------------------------------------------
# ThHandler_Logout logout handler
# -------------------------------------------------
class ThHandler_Logout(ThHandler):
    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)

    def __del__(self):
        self.session = None

    @tornado.gen.coroutine
    def get(self, *args, **kwargs):
        global G_sessions

        if self.session is None:
            self.session = yield self.wait_for_session()

        nextURL = '/'

        if self.session is not None:
            # after logout, try to navigate to the same page
            #if self.session.current_resource:
                #nextURL = self.session.current_resource.resource_code

            self.session.logout()
            G_sessions.remove_session(self.session.session_token)

        self.cookie_st = None
        self.cookie_usertoken = None
        self.write_cookies()
        ThSession.cls_log('Cookies',
                          'Clearing cookies {} and {} in Logout'.format(SESSION_COOKIE_NAME, USER_COOKIE_NAME))

        if self.cookies_changed:
            self.write(self.session.clientside_redir(nextURL))
            self.session.finished()
            self.finish()
        else:
            self.redirect(nextURL)
            self.session = None
            # no self.finish needed, due to redirect
            # self.finish()

    def data_received(self, chunk):
        pass


# -------------------------------------------------
# ThHandler_Login login handler
# -------------------------------------------------
class ThHandler_Login(ThHandler):
    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)

    def __del__(self):
        self.session = None

    @tornado.gen.coroutine
    def get(self, *args, **kwargs):
        global G_sessions

        if self.session is None:
            self.session = yield self.wait_for_session()

        if self.session is not None:
            self.session.logout()
            G_sessions.remove_session(self.session.session_token)

        self.cookie_st = None
        self.cookie_usertoken = None
        self.write_cookies()
        ThSession.cls_log('Cookies',
                          'Clearing cookies {} and {} due to login'.format(SESSION_COOKIE_NAME, USER_COOKIE_NAME))

        # self.redirect('/')
        # self.session = None
        ##no self.finish needed, due to redirect
        ##self.finish()

        self.session = yield self.wait_for_session()
        buf = self.session.build_login_screen()

        if self.session is not None:
            self.session.log('Response', 'Sending login screen')

        self.set_header('Content-Type', theas.Theas.mimetype_for_extension('login.html'))
        self.set_header('Content-Disposition', 'inline; filename=' + 'login.html')

        self.write_cookies()

        self.write(buf)
        self.finish()

        if self.session is not None:
            self.session.finished()

    @tornado.gen.coroutine
    def post(self, *args, **kwargs):
        global G_sessions

        if self.session is None:
            self.session = yield self.wait_for_session()

        success = False
        error_message = ''

        success, error_message = self.session.authenticate()
        self.session.theas_page.set_value('theas:th:ErrorMessage', '{}'.format(error_message))

        resource = G_cached_resources.get_resource(None, self.session, none_if_not_found=True,
                                                   get_default_resource=self.session.logged_in)

        self.write_cookies()

        next_page = ''
        if self.session.logged_in:
            if resource:
                next_page = resource.resource_code
            else:
                next_page = DEFAULT_RESOURCE_CODE
        else:
            next_page = ''


        buf = 'theas:th:LoggedIn={}&theas:th:ErrorMessage={}&theas:th:NextPage={}'.format(
            '1' if self.session.logged_in else '0',
            error_message,
            next_page)

        self.write(buf)
        self.finish()

        if self.session is not None:
            self.session.finished()

    def data_received(self, chunk):
        pass


# -------------------------------------------------
# ThHandler_Async async (AJAX) handler
# -------------------------------------------------
class ThHandler_Async(ThHandler):
    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)

    def __del__(self):
        self.session = None

    @tornado.gen.coroutine
    def post(self, *args, **kwargs):

        global G_cached_resources

        ThSession.cls_log('Async', '*******************************')

        # Note:  The async request is to a generic url of /async
        # To determine what type of async request is being made, we look to the session's current_resource
        # If current_resource is not set (such as due to a new session), we look to the Theas param
        # th:CurrentPage

        buf = ''

        cmd = None
        if self.get_arguments('cmd'):
            cmd = self.get_argument('cmd')
        if not cmd and self.get_body_arguments('cmd'):
            cmd = self.get_body_argument('cmd')

        self.session = yield self.wait_for_session()

        if self.session is not None:

            # update theas parameters based on this post...even if there is not an async stored proc
            self.session.theas_page.process_client_request(request_handler=self, accept_any=False)

            if self.session.current_resource is None:

                if cmd == 'resetPassword':
                    resource_code = 'login'
                else:
                    # Request may have provided Theas param 'th:CurrentPage'
                    # If session does not have current_resource set, trust 'th:CurrentPage'
                    # This allows us to process the async request in situations where the session went away due
                    # to timeout or server restart (assuming "remember me" / user token in cookie is enabled)

                    resource_code = self.session.theas_page.get_value('th:CurrentPage')
                    if resource_code.strip() == '':
                        resource_code = None

                if resource_code is not None:
                    self.session.current_resource = G_cached_resources.get_resource(resource_code, self.session)

            self.session.log('Async:',
                             'Current Resource Code',
                             self.session.current_resource.resource_code
                             if self.session.current_resource
                             else 'No current resource for this session!')

            self.process_uploaded_files()
            # process uploaded files, even if there is no async proc


            # do_log=(not cmd == 'heartbeat'))

            if cmd == 'heartbeat':
                if self.session is not None and self.session.sql_conn is not None:
                    self.write('sessionOK')
                else:
                    self.write('invalidSession')

                if self.session is not None:
                    self.session.finished()
            if cmd == 'clearError':
                if self.session is not None and self.session.sql_conn is not None:
                    self.session.theas_page.set_value('th:ErrorMessage', '')

                self.write('clearError')

                self.session.finished()
            else:
                async_proc_name = None
                theas_params_str = ''

                if self.session is not None:
                    self.session.log('Async', str(self.request.body_arguments))

                    try:

                        if self.session.current_resource is None:
                            # Something is wrong.  Perhaps the async request came in before a resource had been served?
                            # This could happen if the TheasServer was restarted after a page was sent to the browser,
                            # Javascript on the page could submit an async requests...which we can't handle, because
                            # the original session no longer exists.

                            raise TheasServerError(
                                'There is a problem with your session. Click the "reload" button in your browser.' +
                                '|Invalid Session|Async request was received before a SysWebResource was served.  Perhaps ' +
                                'your session expired, or the server was restarted after this page was loaded.')
                        else:

                            async_proc_name = self.session.current_resource.api_async_stored_proc

                        if async_proc_name:

                            # 5/11/2018 moved up, to as soon as we have a session.  We want to update theas parameters
                            # even if there is no async stored proc.
                            # self.session.theas_page.process_client_request(request_handler=self, accept_any=False)

                            row_count = 0

                            form_params = self.request.body_arguments

                            # We want to serialize form data (excluding theas: fields)
                            form_params_str = ''
                            for key in form_params:
                                if not key.startswith('theas:'):
                                    this_val = form_params[key]

                                    if isinstance(this_val, list) and len(this_val) > 0:
                                        this_val = this_val[0]

                                    if isinstance(this_val, bytes):
                                        this_val = this_val.decode('utf-8')
                                    elif this_val:
                                        this_val = str(this_val)

                                    form_params_str = form_params_str + key + '=' + urlparse.quote(this_val) + '&'

                            # We also want to serialize all Theas controls
                            theas_params_str = self.session.theas_page.serialize()

                            self.session.log('Async', 'Async stored proc is: {}'.format(async_proc_name))
                            self.session.log('Async',
                                             'Resource code is: {}'.format(self.session.current_resource.resource_code))

                            proc = ThStoredProc(async_proc_name, self.session)

                            if not proc.is_ok:
                                self.session.log('Async',
                                                 'ERROR: AsyncProcName {} is not valid. in ThHandler_Async.Post'.format(
                                                     async_proc_name))
                            else:
                                proc.refresh_parameter_list()

                                # if '@QuestGUID' in proc.parameter_list and self.session.theas_page.get_value('questGUID') is not None:
                                #    proc.bind(self.session.theas_page.get_value('questGUID'), _mssql.SQLCHAR, '@QuestGUID')

                                # if '@StepGUID' in proc.parameter_list and self.session.theas_page.get_value('stepGUID') is not None:
                                #    proc.bind(self.session.theas_page.get_value('stepGUID'), _mssql.SQLCHAR, '@StepGUID')

                                # if '@StepDefID' in proc.parameter_list and self.session.theas_page.get_value('stepDefID') is not None:
                                #    proc.bind(self.session.theas_page.get_value('stepDefID'), _mssql.SQLCHAR, '@StepDefID')

                                if '@Command' in proc.parameter_list:
                                    proc.bind(cmd, _mssql.SQLCHAR, '@Command')

                                if '@Document' in proc.parameter_list:
                                    proc.bind(self.request.path.rsplit('/', 1)[1], _mssql.SQLCHAR, '@Document')

                                if '@HTTPParams' in proc.parameter_list:
                                    proc.bind(self.request.query, _mssql.SQLCHAR, '@HTTPParams')

                                if '@FormParams' in proc.parameter_list:
                                    proc.bind(form_params_str, _mssql.SQLCHAR, '@FormParams')

                                if '@TheasParams' in proc.parameter_list:
                                    # proc.bind(theas_params_str, _mssql.SQLCHAR, '@TheasParams', output=proc.parameter_list['@TheasParams']['is_output'])
                                    # Would prefer to use output parameter, but this seems not to be supported by FreeTDS.  So
                                    # we look to the resultest(s) returned by the stored proc instead.
                                    proc.bind(theas_params_str, _mssql.SQLCHAR, '@TheasParams')

                                # Execute stored procedure
                                proc_result = proc.execute(fetch_rows=False)

                                # For the async stored proc, we are expecting it to return only a single resultset, and in most
                                # cases to return only a single row.

                                # We watch for a few special column names:  TheasParams is a column the stored proc can use to
                                # return name/value pairs that should update the theas_page.controls.  AsyncResponse is a column
                                # that the stored proc can use to return raw data that will be passed on to the browser as the
                                # response to the async request.

                                # If the async stored proc does return multiple rows, these column values from each row are
                                # concatenated together.

                                theas_params_str = ''
                                if proc.th_session.sql_conn is not None:
                                    theas_params_str = ''
                                    buf = ''

                                    for row in proc.th_session.sql_conn:
                                        row_count += 1

                                        if 'ErrorMessage' in row:
                                            if not row['ErrorMessage'] is None and row['ErrorMessage'] != '':
                                                # self.session.theas_page.set_value('theas:th:ErrorMessage',
                                                #                                  row['ErrorMessage'])
                                                buf = 'theas:th:ErrorMessage=' + \
                                                      urlparse.quote(format_error(row['ErrorMessage'])) + '&'

                                        if 'TheasParams' in row:
                                            if row['TheasParams'] is not None:
                                                theas_params_str = theas_params_str + row['TheasParams']

                                        if 'AsyncResponse' in row:
                                            if row['AsyncResponse'] is not None:
                                                buf = buf + row['AsyncResponse'] + '&'

                                self.session.log('Async', '{row_count} rows returned by async stored proc'.format(
                                    row_count=row_count))

                                if row_count == 0:
                                    raise (TheasServerError('No result row returned by async stored proc.'))
                                changed_controls = None

                                if theas_params_str:
                                    changed_controls = self.session.theas_page.process_client_request(
                                        buf=theas_params_str, accept_any=True, from_stored_proc=True)

                                    # let stored proc create any desired Theas controls, so these values can be used
                                    # when rendering the template.



                    except TheasServerError as e:
                        # e = sys.exc_info()[0]
                        err_msg = e.value if hasattr(e, 'value') else e

                        buf = 'theas:th:ErrorMessage=' + urlparse.quote(format_error(err_msg))

                    except Exception as e:
                        # We would like to catch specific MSSQL exceptions, but these are declared with cdef
                        # in _mssql.pyx ... so they are not exported to python.  Should these be declared
                        # with cpdef?


                        err_msg = None

                        err_msg = e.text.decode('ascii')

                        buf = 'theas:th:ErrorMessage=' + urlparse.quote(format_error(err_msg))
                        self.session.log('Async',
                                         'ERROR when executing stored proc {}: {}'.format(
                                             async_proc_name, err_msg))

                if len(buf) > 0:
                    # stored proc specified an explicit response
                    self.write(buf)
                else:
                    # stored proc did not specify an explicit response:  send updated controls only
                    # if there are any, otherwise send all controls
                    # self.write(self.session.theas_page.serialize(control_list = changed_controls))

                    # send ALL Theas controls
                    self.write(self.session.theas_page.serialize())

                # CORS
                self.set_header('Access-Control-Allow-Origin', '*')  # allow CORS from any domain
                self.set_header('Access-Control-Max-Age', '0')  # disable CORS preflight caching

                self.session.finished()
                self.session = None
                self.finish()

    @tornado.gen.coroutine
    def get(self, *args, **kwargs):

        return self.post(*args, **kwargs)

    def data_received(self, chunk):
        pass


# -------------------------------------------------
# ThHandler_REST async (AJAX) handler
# -------------------------------------------------
'''
ThHandler_REST is similar to ThHandler_Async, except for:

1) Async is for calls associated with a normal page (i.e. page
is served up, and then subsequent async calls are made),
whereas REST is not associated with a normal page.

2) Async uses SysWebResources.  REST does not.  (REST
uses SysRequestTypes instead)

3) By default, REST will destroy the session after each
request.

4) REST does not do anything with Theas Params

'''


class ThHandler_REST(ThHandler):
    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)

    def __del__(self):
        self.session = None

    def get_rest_resource(self, resource_code, th_session):
        this_resource = None

        if resource_code:
            resource_code = resource_code.strip()

        if resource_code == '':
            resource_code = None

        # load resource from database
        th_session.log('Resource', 'ThCachedResources.get_rest_resource fetching from database',
                       resource_code if resource_code is not None else 'None')

        # Get SysWebResourcesdata from database
        this_proc = ThStoredProc('theas.spgetSysWebResources', th_session)

        if this_proc.is_ok:

            # Note:  we could check for existence of @GetDefaultResource down below to help with backwards
            # compatibility ... but that would mean having to call refresh_parameter_list, which is
            # unnecessary overhead.
            # this_proc.refresh_parameter_list()

            this_proc.bind(resource_code, _mssql.SQLCHAR, '@ResourceCode', null=(resource_code is None))

            proc_result = this_proc.execute(fetch_rows=False)
            assert proc_result, 'ThCachedResources.load_resource received error result from call to theas.spgetSysWebResources in the SQL database.'

            row_count = 0

            this_static_blocks_dict = {}

            if this_proc.th_session.sql_conn is not None:
                for row in this_proc.th_session.sql_conn:
                    row_count += 1
                    buf = row['ResourceText']
                    if not buf:
                        buf = row['ResourceData']
                        if buf:
                            buf = bytes(buf)

                    this_resource = ThResource()

                    this_resource.resource_code = row['ResourceCode']
                    this_resource.filename = row['Filename']
                    this_resource.data = buf
                    this_resource.api_stored_proc = row['APIStoredProc']
                    this_resource.api_async_stored_proc = row['APIAsyncStoredProc']
                    this_resource.api_stored_proc_resultset_str = row['ResourceResultsets']
                    this_resource.is_public = row['IsPublic']
                    this_resource.is_static = row['IsStaticBlock']
                    this_resource.requires_authentication = row['RequiresAuthentication']
                    this_resource.render_jinja_template = row['RenderJinjaTemplate']
                    this_resource.skip_xsrf = row['SkipXSRF']

                    if 'OnBefore' in row:
                        this_resource.on_before = row['OnBefore']

                    if 'OnAfter' in row:
                        this_resource.on_after = row['OnAfter']

                    if 'Revision' in row:
                        this_resource.revision = row['Revision']

                    if this_resource.resource_code:
                        self.add_resource(row['ResourceCode'], this_resource)

            this_proc = None
            del this_proc

        return this_resource

    @tornado.gen.coroutine
    def post(self, resource_code=None, *args, **kwargs):
        global G_cached_resources

        buf = ''

        try:
            # spin up a new session
            self.session = yield self.wait_for_session()

            if self.session is None:
                raise TheasServerError('Session could not be established for REST request.')

            # try to find required resource
            resource_code = None
            resource = None

            request_path = None
            if len(args) >= 0:
                request_path = args[0]

            if request_path is not None and request_path.split('/')[0] == 'r':
                # Special case:  an "r" as the first segment of the path, such as:
                # r/resourcecode/aaa/bbb
                # indicates that the second segment is to be the resource code.
                # This allows URLs such as /r/img/myimg.jpg to be handled dynamically:  the resource img is
                # loaded, and then myimg.jpg is passed in.  (Otherwise the resource would be taken to be
                # img/myimg.jpg
                resource_code = request_path.split('/')[1]
            else:
                resource_code = request_path
                if resource_code and resource_code.count('.') >= 2:
                    # A versioned filename, i.e. my.23.css for version #23 of my.css
                    # We just want to cut out the version, and return the unversioned
                    # filename as the resource code (i.e. my.css)

                    # That is, Theas will always / only serve up the most recent version
                    # of a resource.  There is not support for serving up a particular
                    # historical version.  The version number in the file name is merely
                    # for the browser's benefit, so that we can "cache bust" / have the
                    # browser request the latest version even if it has an old version in
                    # cache.

                    # For this reason, we don't really need to inspect the resources.
                    # We need only manipulate the resource_code to strip out the version
                    # number.
                    segments = resource_code.split('.')
                    if len(segments) >= 3 and 'ver' in segments:
                        ver_pos = segments.index('ver')
                        if ver_pos > 0:
                            resource_code = '.'.join(segments[:ver_pos]) + '.' + '.'.join(segments[ver_pos + 2:])

            resource = self.get_rest_resource(resource_code)

            rest_proc_name = resource.api_async_stored_proc

            # allow REST to receive file uploads
            self.process_uploaded_files()

            form_params = self.request.body_arguments

            # We want to serialize form data
            form_params_str = ''
            for key in form_params:
                this_val = form_params[key]

                if isinstance(this_val, list) and len(this_val) > 0:
                    this_val = this_val[0]

                if isinstance(this_val, bytes):
                    this_val = this_val.decode('utf-8')
                elif this_val:
                    this_val = str(this_val)

                form_params_str = form_params_str + key + '=' + urlparse.quote(this_val) + '&'

            self.session.log('REST', 'REST stored proc is: {}'.format(rest_proc_name))

            proc = ThStoredProc(rest_proc_name, self.session)

            if not proc.is_ok:
                self.session.log('REST',
                                 'ERROR: REST proc name {} is not valid. in ThHandler_Async.Post'.format(
                                     rest_proc_name))
            else:
                proc.refresh_parameter_list()

                if '@Document' in proc.parameter_list:
                    proc.bind(self.request.path.rsplit('/', 1)[1], _mssql.SQLCHAR, '@Document')

                if '@HTTPParams' in proc.parameter_list:
                    proc.bind(self.request.query, _mssql.SQLCHAR, '@HTTPParams')

                if '@FormParams' in proc.parameter_list:
                    proc.bind(form_params_str, _mssql.SQLCHAR, '@FormParams')

                # Execute stored procedure
                proc_result = proc.execute(fetch_rows=False)

                # For the rest stored proc, we are expecting it to return only a single resultset that
                # contains only a single row.

                # We watch for a few special column names: RESTResponse is a column
                # that the stored proc can use to return raw data that will be passed on to the browser as the
                # response to the REST request. Similarly, RESTResponseBin can contain binary data
                # to send to the browser.  (If present and not null, RESTResponseBin will be served
                # instead of RestResponse.)

                row_count = 0

                if proc.th_session.sql_conn is not None:
                    buf = ''

                    for row in proc.th_session.sql_conn:
                        row_count += 1

                        if 'ErrorMessage' in row:
                            if not row['ErrorMessage'] is None and row['ErrorMessage'] != '':
                                buf = 'Stored procedure returned an error:' + \
                                      urlparse.quote(format_error(row['ErrorMessage']))

                        if 'RESTResponse' in row:
                            if row['RESTResponse'] is not None:
                                buf = row['RESTResponse']

                assert row_count > 0, 'No result row returned by REST stored proc.'


        except TheasServerError as e:
            # e = sys.exc_info()[0]
            err_msg = e.value if hasattr(e, 'value') else e

            buf = 'theas:th:ErrorMessage=' + urlparse.quote(err_msg)

        except Exception as e:
            # We would like to catch specific MSSQL exceptions, but these are declared with cdef
            # in _mssql.pyx ... so they are not exported to python.  Should these be declared
            # with cpdef?


            err_msg = None

            err_msg = str(e)

            buf = 'theas:th:ErrorMessage=' + urlparse.quote(err_msg)
            self.session.log('Async',
                             'ERROR when executing stored proc {}: {}'.format(
                                 rest_proc_name, err_msg))

        self.write(buf)

        # CORS
        self.set_header('Access-Control-Allow-Origin', '*')  # allow CORS from any domain
        self.set_header('Access-Control-Max-Age', '0')  # disable CORS preflight caching

        self.session.finished()
        self.session = None
        self.finish()

    @tornado.gen.coroutine
    def get(self, *args, **kwargs):

        return self.post(*args, **kwargs)

    def data_received(self, chunk):
        pass


# -------------------------------------------------
# ThHandler_Back "back" handler
# -------------------------------------------------
class ThHandler_Back(ThHandler):
    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)

    def __del__(self):
        self.session = None

    @tornado.gen.coroutine
    def get(self, *args, **kwargs):

        if self.session is None:
            # try to get the session, but do not wait for it
            self.session = yield self.wait_for_session(seconds_to_wait=0)

        if self.session is not None:
            if len(self.session.history) > 1:
                self.session.history.pop()
                this_history_entry = self.session.history[-1]

                self.session.theas_page.set_value('theas:th:NextPage', this_history_entry['PageName'])

            self.session.log('Response', 'Sending clientside redir')
            self.write(self.session.clientside_redir())

            ##Handle the actual form processing here. When done, we will persist session data and redirect.
            # buf = yield self.background_process_post_authenticated()
            ##buf = self.background_process_post_authenticated()

            # self.write(buf)
            # self.session.log('Response', 'Sending response for back request')

            self.session.finished()
        else:
            if self.cookies_changed():
                # must perform a client-side redirect in order to set cookies
                self.session.finished()
                # Could redirect if desired.  But instead, we'll send an error message and let the browser handle it
                # self.redirect('/')
            else:
                # can send a normal redirect, since no cookies need to be written
                # Could redirect if desired.  But instead, we'll send an error message and let the browser handle it
                # self.write(self.session.clientside_redir('/'))
                self.session.finished()

        self.session = None
        self.finish()

    def data_received(self, chunk):
        pass


# -------------------------------------------------
# ThHandler_PurgeCache purge cache handler
# -------------------------------------------------
class ThHandler_PurgeCache(ThHandler):
    def data_received(self, chunk):
        pass

    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)

    def __del__(self):
        self.session = None

    @tornado.gen.coroutine
    def get(self, *args, **kwargs):
        global G_cached_resources

        message = 'No resource code specified.  Nothing to do.'

        if len(self.get_arguments('rc')) > 0:
            resource_code = self.get_argument('rc')

            if resource_code == '_all':
                if G_cached_resources.delete_resource(resource_code=None, delete_all=True):
                    message = 'Purged all cached resources.'
                else:
                    message = 'Nothing purged. Nothing in the cache.'

            else:
                if G_cached_resources.delete_resource(resource_code=resource_code, delete_all=False):
                    message = 'Purged cached resource: ' + resource_code
                else:
                    message = 'Nothing purged.  Resource code "' + resource_code + '" not found.'

        message = message + ' Items remaining in cache: ' + str(G_cached_resources.len())

        ThSession.cls_log('Cache', message)

        self.write('<html><body>' + message + '</body></html>')
        self.finish()



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



# -------------------------------------------------
# ThWSHandler test websocket handler
# -------------------------------------------------
class ThWSHandler_Test(tornado.websocket.WebSocketHandler):
    def open(self):
        ThSession.cls_log('WebSocket', 'New client connected')
        self.write_message("You are connected")

    # the client sent the message
    def on_message(self, message):
        self.write_message('DoFetchData')

    # client disconnected
    def on_close(self):
        ThSession.cls_log('WebSocket', 'Client disconnected')


def run(run_as_svc=False):
    global G_program_options
    global G_server_is_running
    global G_cached_resources
    global G_sessions
    global G_break_handler

    global LOGGING_LEVEL
    global SESSION_MAX_IDLE
    global REMOVE_EXPIRED_THREAD_SLEEP
    global LOGIN_RESOURCE_CODE
    global LOGIN_AUTO_USER_TOKEN
    global REMEMBER_USER_TOKEN
    global DEFAULT_RESOURCE_CODE

    global FULL_SQL_IS_OK_CHECK
    global FORCE_REDIR_AFTER_POST

    global USE_SECURE_COOKIES
    global SESSION_HEADER_NAME
    global SESSION_COOKIE_NAME
    global USER_COOKIE_NAME

    global USE_WORKER_THREADS
    global MAX_WORKERS


    if LOGGING_LEVEL:
        msg = 'Theas app getting ready...'
        write_winlog(msg)
        print(msg)

    if not run_as_svc:
        # Trap breaks.
        G_break_handler = BreakHandler()

    if G_break_handler:
        G_break_handler.enable()

    program_directory, program_filename = get_program_directory()

    msg = 'Theas app: Program directory is: {}'.format(program_directory)
    if LOGGING_LEVEL:
        print(msg)
    write_winlog(msg)

    msg = 'Theas app: program filename is {}'.format(program_filename)
    if LOGGING_LEVEL:
        print(msg)
    write_winlog(msg)

    msg = 'Theas app: program parameters: {}'.format(str(sys.argv[1:]))
    if LOGGING_LEVEL:
        print(msg)
    write_winlog(msg)

    G_program_options = tornado.options.options

    G_program_options.define("settings_path",
                             default=program_directory,
                             help="The path to the folder with configuration files.", type=str)

    G_program_options.define("server_prefix",
                             default="locaohost:8881",
                             help="The web server address prefix to prepend to URLs that need it.", type=str)

    G_program_options.define("port",
                             default=8881,
                             help="The TCP/IP port that the web server will listen on", type=int)

    G_program_options.define("sql_server",
                             default=None,
                             help="Server name of your MSSQL server instance", type=str)

    G_program_options.define("sql_port",
                             default=1433,
                             help="TCP/IP port for your MSSQL server connections", type=int)

    G_program_options.define("sql_user",
                             help="MSSQL login user name for SQL connections", type=str)

    G_program_options.define("sql_password",
                             help="MSSQL login password for SQL connections", type=str)

    G_program_options.define("sql_database",
                             help="MSSQL default database for SQL connections", type=str)

    G_program_options.define("sql_appname",
                             default="TheasServer",
                             help="Descriptive name for SQL connections to know the name of this application", type=str)

    G_program_options.define("sql_timeout",
                             default=60,
                             help="Time (in seconds) to wait for SQL results before timing out.  Zero means wait indefinitely.",
                             type=int)

    G_program_options.define("sql_max_connections",
                             default=100,
                             help="Maximum number of simultaneous SQL connections allowed.",
                             type=int)

    G_program_options.define("session_max_idle_minutes",
                             default=SESSION_MAX_IDLE,
                             help="Maximum idle time (in minutes) that user sessions will remain active", type=int)

    G_program_options.define("session_expired_poll_seconds",
                             default=REMOVE_EXPIRED_THREAD_SLEEP,
                             help="Time (in seconds) between polls to check for expired sessions", type=int)

    G_program_options.define("logging_level",
                             default=LOGGING_LEVEL,
                             help="Controls logging.  0 to disable all, 1 to enable all, or threshold to exceed.",
                             type=int)

    G_program_options.define("login_resource_code",
                             default=LOGIN_RESOURCE_CODE,
                             help="Resource code of the login screen template.",
                             type=str)

    G_program_options.define("login_auto_user_token",
                             default=LOGIN_AUTO_USER_TOKEN,
                             help="User token for the default (public) login.",
                             type=str)

    G_program_options.define("remember_user_token",
                             default=REMEMBER_USER_TOKEN,
                             help="Save the user token in a cookie, and automatically log user in on future visits.",
                             type=bool)

    G_program_options.define("default_resource_code",
                             default=DEFAULT_RESOURCE_CODE,
                             help="Resource code to use when a resource is not specified (i.e. like index.htm)",
                             type=str)

    G_program_options.define("full_sql_is_ok_check",
                             default=FULL_SQL_IS_OK_CHECK,
                             help="Explicitly test SQL connection before each call.",
                             type=bool)

    G_program_options.define("force_redir_after_post",
                             default=FORCE_REDIR_AFTER_POST,
                             help="After a POST, perform a redirect even if no update was requested.",
                             type=bool)

    G_program_options.define("use_secure_cookies",
                             default=USE_SECURE_COOKIES,
                             help="When storing session and user tokens in cookies, use secure cookies.",
                             type=bool)

    G_program_options.define("session_header_name",
                             default=SESSION_HEADER_NAME,
                             help="Name of HTTP header used to send session token.)",
                             type=str)

    G_program_options.define("session_cookie_name",
                             default=SESSION_COOKIE_NAME,
                             help="Name of cookie used to store session token.)",
                             type=str)

    G_program_options.define("user_cookie_name",
                             default=USER_COOKIE_NAME,
                             help="Name of cookie used to store user token (if applicable).",
                             type=str)

    G_program_options.define("use_worker_threads",
                             default=USE_WORKER_THREADS,
                             help="Indicates if individual requests should be processed in their own thread.",
                             type=bool)

    G_program_options.define("max_worker_threads",
                             default=MAX_WORKERS,
                             help="If use_worker_threads is true, indicates the maximum number of worker threads allowed.",
                             type=int)

    G_program_options.parse_command_line()

    msg = 'Theas app: trying to use configuration from {}'.format(G_program_options.settings_path + 'settings.cfg')
    if LOGGING_LEVEL:
        print(msg)
    write_winlog(msg)

    try:
        if G_program_options.sql_server is None:
            tornado.options.parse_config_file(G_program_options.settings_path + 'settings.cfg')
    except Exception as e:
        msg = 'Theas app: error processing settings.cfg file in {}  {}'.format(
            G_program_options.settings_path + 'settings.cfg',
            e)
        if LOGGING_LEVEL:
            print(msg)
        write_winlog(msg)

    if G_program_options.sql_server is None:
        tornado.options.print_help()
        sys.exit()

    # Now we have settings set in G_program_options elements.
    # Some of these used a hard-coded constant as the default. (For example, we don't want to have hard-coded
    # constants for credentials, and we don't need them for certain other values that are retrieved only
    # once in our code.  But other non-sensitive settings, a constant is used.)
    # But these values could have been changed by settings in the config file.

    # In our code we can directly use G_program_options.xxx to access the configured values.  But for readability
    # (and possibly other reasons) in some cases we prefer to access the global constants directly.  So we now
    # want to update the value of the global constants based on what has been configured.

    SESSION_MAX_IDLE = G_program_options.session_max_idle_minutes
    REMOVE_EXPIRED_THREAD_SLEEP = G_program_options.session_expired_poll_seconds
    LOGGING_LEVEL = int(G_program_options.logging_level)
    LOGIN_RESOURCE_CODE = G_program_options.login_resource_code
    LOGIN_AUTO_USER_TOKEN = G_program_options.login_auto_user_token
    REMEMBER_USER_TOKEN = G_program_options.remember_user_token
    DEFAULT_RESOURCE_CODE = G_program_options.default_resource_code
    FULL_SQL_IS_OK_CHECK = G_program_options.full_sql_is_ok_check
    FORCE_REDIR_AFTER_POST = G_program_options.force_redir_after_post
    USE_SECURE_COOKIES = G_program_options.use_secure_cookies
    SESSION_HEADER_NAME = G_program_options.session_header_name
    SESSION_COOKIE_NAME = G_program_options.session_cookie_name
    USER_COOKIE_NAME = G_program_options.user_cookie_name
    USE_WORKER_THREADS = G_program_options.use_worker_threads
    MAX_WORKERS = G_program_options.max_worker_threads

    msg = 'Starting Theas server {} (in {}) on port {}.'.format(
        program_filename, program_directory, G_program_options.port)
    print(msg)
    write_winlog(msg)

    if not LOGGING_LEVEL:
        print("Note: Logging is disabled")

    global G_cached_resources

    G_cached_resources = ThCachedResources()  # Global list of cached resources

    try:
        G_cached_resources.load_global_resources()

    except Exception as e:
        msg = 'Theas app: error global cached resources when calling G_cached_resources.load_global_resources(): {}'.format(
            e)
        print(msg)
        write_winlog(msg)
        sys.exit()

    G_sessions = ThSessions()  # Global list of sessions

    _mssql.set_max_connections(G_program_options.sql_max_connections)

    if run_as_svc:
        # make sure there is an ioloop in this thread (needed for Windows service)
        io_loop = tornado.ioloop.IOLoop()
        io_loop.make_current()

    application = tornado.web.Application([
        (r'/attach', ThHandler_Attach),
        (r'/attach/(.*)', ThHandler_Attach),
        (r'/logout', ThHandler_Logout),
        (r'/login', ThHandler_Login),
        (r'/back', ThHandler_Back),
        (r'/purgecache', ThHandler_PurgeCache),
        (r'/test', TestThreadedHandler),
        (r'/testws', ThWSHandler_Test),
        (r'/async', ThHandler_Async),
        (r'/async/(.*)', ThHandler_Async),
        (r'/(.*)', ThHandler)
        # note that /r/* has special meaning, though it is handled by ThHandler.  When /r/resourcecode/param1/param2
        # is specified, this indicates that the resource code is "resourcecode".  "param1/param2" will be passed
        # in to @PathParams in the stored procedure.
    ],
        debug=False,
        autoreload=False,
        xsrf_cookies=True,
        cookie_secret=COOKIE_SECRET)

    http_server = tornado.httpserver.HTTPServer(application)

    try:
        http_server.listen(G_program_options.port)
    except Exception as e:
        msg = 'Theas app:  Could not start HTTP server on port {}. Is something else already running on that port? {}'.format(
            G_program_options.port, e)
        print(msg)
        write_winlog(msg)
        sys.exit()

    G_server_is_running = True

    # disable Tornado's built-in logging to stderr
    # see:  http://stackoverflow.com/questions/21234772/python-tornado-disable-logging-to-stderr
    logging.getLogger('tornado.access').disabled = True

    G_sessions.start_cleanup_thread()

    tornado.ioloop.PeriodicCallback(do_periodic_callback, 2000).start()

    tornado.ioloop.IOLoop.instance().start()

    # all_objects = muppy.get_objects()
    # sum1 = summary.summarize(all_objects)
    # summary.print_(sum1)


    # tornado.ioloop.IOLoop.current().close()
    # tornado.ioloop.IOLoop.instance().close()


    msg = 'Shutting down...Exited IOLoop'
    ThSession.cls_log('Shutdown', msg)
    write_winlog(msg)

    # ioloop = tornado.ioloop.IOLoop.current()
    # ioloop.add_callback(ioloop.stop)
    http_server.stop()

    # ThHandler.executor.shutdown()
    # ThSession.cls_log('Shutdown', 'Winding down #1')
    # ThHandler_Attach.executor.shutdown()
    # ThSession.cls_log('Shutdown', 'Winding down #2')
    # TestThreadedHandler.executor.shutdown()
    # ThSession.cls_log('Shutdown', 'Winding down #3')

    http_server = None
    del http_server

    G_cached_resources = None
    ThSession.cls_log('Shutdown', 'Winding down #4')

    G_sessions.stop()
    # ThSessions.remove_all_sessions()
    G_sessions = None

    ThSession.cls_log('Shutdown', 'Winding down #5')

    if G_break_handler:
        G_break_handler.disable()

    msg = 'Stopped Theas server {} (in {}) on port {}.'.format(
        program_filename, program_directory, G_program_options.port)

    print(msg)
    write_winlog(msg)


if __name__ == "__main__":

    try:
        # all_objects = muppy.get_objects()
        # sum1 = summary.summarize(all_objects)
        # summary.print_(sum1)

        # gc.set_debug(gc.DEBUG_UNCOLLECTABLE |  gc.DEBUG_SAVEALL)
        # set_exit_handler(on_exit)
        run()
        ThSession.cls_log('Shutdown', 'Application has ended')

        # all_objects = muppy.get_objects()
        # sum1 = summary.summarize(all_objects)
        # summary.print_(sum1)

        # os.kill(0, signal.CTRL_BREAK_EVENT)
    finally:
        pass

        # Clean up _mssql resources
# _mssql.exit_mssql()
