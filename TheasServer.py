#!/usr/bin/env python
__author__ = 'DavidRueter'
'''
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
'''

# import msvcrt
import sys
import os
import datetime
import threading
import time
import signal
import uuid
import binascii
# import mimetypes
import traceback
import string

import asyncio

import tornado.web
import tornado.ioloop
import tornado.options
import tornado.httpserver
import tornado.ioloop

from multiprocessing import Lock
from concurrent.futures import ThreadPoolExecutor
from tornado.concurrent import run_on_executor

import theas

import _mssql

from win32 import servicemanager
import logging

import TheasCustom

# import os
# import time
# import gc
# from pympler import muppy, summary
# import urllib.parse as urlparse
# from jinja2 import Template, Untornado.options.defined
# from jinja2.environment import Environment
# from tornado.stack_context import ExceptionStackContext
# import contextlib
import decimal
# import pymssql
from tornado import gen, concurrent, ioloop

# from multiprocessing import Process, Lock
# from tornado.options import tornado.options.define, options
# import tornado.options


# @contextlib.contextmanager
# def catch_async_exceptions(type, value, traceback):
#    try:
#        print('ERROR: ' + str(value.args[0][1]))
#        #yield
#    except Exception:
#        print('ERROR: ' + str(value.args[0][1]))

SESSION_MAX_IDLE = 60  # Max idle time (in minutes) before TheasServer session is terminated
REMOVE_EXPIRED_THREAD_SLEEP = 60  # Seconds to sleep in between polls in background thread to check for expired sessions, 0 to disable
LOGGING_LEVEL = 1  # Enable all logging.  0 to disable all, other value to specify threshold.
LOGIN_RESOURCE_CODE = 'login'
# LOGIN_AUTO_USER_TOKEN = '21FCDB16-CE2C-45BB-B739-9228C716505E'
LOGIN_AUTO_USER_TOKEN = None
DEFAULT_RESOURCE_CODE = None

FULL_SQL_IS_OK_CHECK = True

USE_WORKER_THREADS = False
MAX_WORKERS = 30
USE_SESSION_COOKIE = True

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


class BreakHandler:
    '''
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
    '''

    def __init__(self, emphatic=9):
        '''
        Create a new break handler.

        @param emphatic: This is the number of times that the user must
                    press break to *disable* the handler.  If you press
                    break this number of times, the handler is automagically
                    disabled, and one more break will trigger an old
                    style keyboard interrupt.  The default is nine.  This
                    is a Good Idea, since if you happen to lose your
                    connection to the handler you can *still* disable it.
        '''
        self._count = 0
        self._enabled = False
        self._emphatic = emphatic
        self._oldhandler = None
        return

    def _reset(self):
        '''
        Reset the trapped status and count.  You should not need to use this
        directly; instead you can disable the handler and then re-enable it.
        This is better, in case someone presses CTRL-C during this operation.
        '''
        self._count = 0
        return

    def enable(self):
        '''
        Enable trapping of the break.  This action also resets the
        handler count and trapped properties.
        '''
        if not self._enabled:
            self._reset()
            self._enabled = True
            self._oldhandler = signal.signal(signal.SIGINT, self)
        return

    def disable(self):
        '''
        Disable trapping the break.  You can check whether a break
        was trapped using the count and trapped properties.
        '''
        if self._enabled:
            self._enabled = False
            signal.signal(signal.SIGINT, self._oldhandler)
            self._oldhandler = None
        return

    def __call__(self, signame, sf):
        '''
        An break just occurred.  Save information about it and keep
        going.
        '''
        self._count += 1

        print('Ctrl-C Pressed (caught by BreakHandler)')

        # If we've exceeded the "emphatic" count disable this handler.
        if self._count >= self._emphatic:
            self.disable()
        return

    def __del__(self):
        '''
        Python is reclaiming this object, so make sure we are disabled.
        '''
        self.disable()
        return

    @property
    def count(self):
        '''
        The number of breaks trapped.
        '''
        return self._count

    @property
    def trapped(self):
        '''
        Whether a break was trapped.
        '''
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
    ThSession.cls_log('Shutown', 'StopServer() called')
    G_server_is_running = False


def set_exit_handler(func):
    signal.signal(signal.SIGTERM, func)


def on_exit(signum, frame):
    ThSession.cls_log('Shutown', 'on_exit() called')
    StopServer()


def do_periodic_callback():
    global G_server_is_running
    global G_break_handler

    # Called by Tornado once a second.
    # ThSession.cls_log('Periodic', 'do_periodic_callback() called')


    if G_break_handler.trapped:
        # Ctrl-C pressed
        G_server_is_running = False

    # if msvcrt.kbhit():
    #    # Any key pressed
    #    G_server_is_running = False

    if not G_server_is_running:
        ThSession.cls_log('Periodic', 'Trying to stop IOLoop.instance()')
        tornado.ioloop.IOLoop.current().stop()
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
        self._th_session.log('StoredProc', 'Checking is_ok:', self.stored_proc_name)
        result = self._storedproc is not None and self.connection is not None and self.connection.connected

        if result and FULL_SQL_IS_OK_CHECK:
            try:
                self.connection.execute_non_query('SELECT 1 AS IsOK')
            except:
                result = False

        if not result:
            self._th_session.logged_in = False
            self._th_session.sql_conn = None
        return result

    def __init__(self, this_stored_proc_name, this_th_session):
        self._connection = None
        self._storedproc = None
        self._th_session = None
        self.stored_proc_name = None
        self.parameter_list = {}  # sniffed parameters.  See parameters for bound parameters.
        self.resultset = []

        self.stored_proc_name = this_stored_proc_name

        # Use provided session.
        self._th_session = this_th_session
        self._connection = this_th_session.sql_conn

        self._th_session.log('StoredProc', 'Initializing ThStoredProc:', self.stored_proc_name)

        if self._th_session.sql_conn is None or not self._th_session.sql_conn.connected:
            self._th_session.log('StoredProc', 'New connection', self.stored_proc_name)
            self._th_session.init_session()

        if self._th_session.sql_conn is not None and self._th_session.sql_conn.connected:
            self._th_session.log('StoredProc', 'Existing connection:', self.stored_proc_name)
            self._storedproc = self._th_session.sql_conn.init_procedure(self.stored_proc_name)
        else:
            self._storedproc = None

    def __del__(self):
        self._storedproc = None
        del self._storedproc

        self._th_session = None
        del self._th_session

    def refresh_parameter_list(self):
        self._th_session.log('StoredProc', 'Refreshing parameter list:', self.stored_proc_name)

        if self.parameter_list is not None:
            self.parameter_list = {}
        if self.stored_proc_name is not None and self._th_session is not None and self._th_session.sql_conn is not None and self._th_session.sql_conn.connected:
            try:
                self._th_session.sql_conn.execute_query(
                    'EXEC theas.sputilGetParamNames @ObjectName = \'{}\''.format(self.stored_proc_name))
                resultset = [row for row in self._th_session.sql_conn]
                for row in resultset:
                    this_param_info = {}
                    this_param_info['is_output'] = row['is_output']
                    self.parameter_list[row['ParameterName']] = this_param_info
                    # self.parameter_list.append(row['ParameterName'])

            except Exception as e:
                self._th_session.log('Sessions', '***Error accessing SQL connection', e)
                self._th_session.sql_conn = None
                self.parameter_list = None
                # self._th_session.init_session(force_init=True)
                # self._storedproc = self._th_session.sql_conn.init_procedure(self.stored_proc_name)
                # self._th_session.authenticate(None, None) #ToDo: need to think about this.  Can we safely re-authenticate?
                # self._th_session.log('Sessions', '***Cannot automatically log in after failed SQL connection', e.message)
                raise

    def execute(self, fetch_rows=True, *args, **kwargs):
        self._th_session.log('StoredProc', 'Executing:', self.stored_proc_name)

        _mssql.min_error_severity = 1
        this_result = None

        if self.is_ok:
            self._th_session.do_on_sql_start(self)
            try:
                this_result = self._storedproc.execute(*args, **kwargs)
                if fetch_rows:
                    self.resultset = [row for row in self._th_session.sql_conn]
                self._th_session.do_on_sql_done(self)
            except Exception as e:
                if LOGGING_LEVEL:
                    print(e)
                raise e
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
        return (this_result)

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
        self.data = ''
        self.api_stored_proc = None
        self.api_async_stored_proc = None
        self.api_stored_proc_resultset_str = None
        self.is_public = False
        self.is_static = False
        self.requires_authentication = False
        self.render_jinja_template = False
        self.exists = True
        self.on_before = None
        self.on_after = None

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

        finally:
            self.unlock()

    @property
    def static_blocks_dict(self):
        return self.__static_blocks_dict

    @static_blocks_dict.setter
    def static_blocks_dict(self, new_dict):
        self.__static_blocks_dict = new_dict

    def len(self):
        return len(self.__resources)

    def add_resource(self, resource_code, resource_dict):
        self.lock()
        try:
            self.__resources[resource_code] = resource_dict
        finally:
            self.unlock()

    def load_resource(self, resource_code, th_session, all_static_blocks=False, sessionless=False, from_filename=None,
                      is_public=False, is_static=False):
        this_resource = None

        if from_filename:
            # load resource from file
            buf = None
            try:
                with open(from_filename, 'r') as f:
                    buf = f.read()
                    f.close()
            except Exception as e:
                raise TheasServerError('Error while starting the Theas Server:  File Theas.js could not be read.')

            this_resource = ThResource()
            this_resource.resource_code = resource_code
            this_resource.filename = from_filename
            this_resource.data = buf
            this_resource.api_stored_proc = None
            this_resource.api_async_stored_proc = None
            this_resource.api_stored_proc_resultset_str = None
            this_resource.is_public = is_public
            this_resource.is_static = is_static
            this_resource.requires_authentication = False

            self.add_resource(resource_code, this_resource)


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
                if resource_code is None:
                    th_session.log('Resource',
                                   'No resource_code specified.  Will load default resource for this session.')
                else:
                    th_session.log('Resource', 'ThCachedResources.load_resource fetching from database',
                                   resource_code if resource_code is not None else 'None')

            # Get SysWebResourcesdata from database
            this_proc = ThStoredProc('theas.spgetSysWebResources', th_session)

            if this_proc.is_ok:
                this_proc.bind(resource_code, _mssql.SQLCHAR, '@ResourceCode', null=(resource_code is None))
                this_proc.bind(str(int(all_static_blocks)), _mssql.SQLCHAR, '@AllStaticBlocks')

                proc_result = this_proc.execute(fetch_rows=False)
                assert not proc_result, 'ThCachedResources.load_resource received error result from call to opsusr.spapiGetSysWebResources in the SQL database.'

                row_count = 0

                this_static_blocks_dict = {}

                if this_proc._th_session.sql_conn is not None:
                    for row in this_proc._th_session.sql_conn:
                        row_count = row_count + 1
                        buf = row['ResourceText']
                        if len(buf.strip()) == 0:
                            buf = bytes(row['ResourceData'])
                        elif not all_static_blocks and '$thInclude_' in buf:
                            # Perform replacement of includes.  Template may include string like:
                            # $thInclude_MyResourceCode
                            # This will be replaced with the static block resource having a ResourceCode=MyResourceCode
                            tmp = string.Template(buf)
                            buf = tmp.safe_substitute(G_cached_resources.static_blocks_dict)

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

                        if 'OnBefore' in row:
                            this_resource.on_before = row['OnBefore']

                        if 'OnAfter' in row:
                            this_resource.on_after = row['OnAfter']

                        if this_resource.resource_code:
                            self.add_resource(row['ResourceCode'], this_resource)

                        if all_static_blocks:
                            this_static_blocks_dict['thInclude_' + row['ResourceCode']] = buf

                if resource_code and row_count == 0:
                    # do negative cache
                    this_resource = ThResource()
                    this_resource.exists = False
                    self.add_resource(resource_code, this_resource)

                if all_static_blocks:
                    ThCachedResources.static_blocks_dict = this_static_blocks_dict

                this_proc = None
                del this_proc

            if sessionless:
                th_session = None

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
                     none_if_not_found=True, from_file=None):
        global DEFAULT_RESOURCE_CODE

        this_resource = None

        if resource_code:
            resource_code = resource_code.strip()

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
                this_resource = self.load_resource(resource_code, th_session, all_static_blocks)

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
            if for_public_use and not this_resource.is_public:
                log_msg = 'Resource', 'Requested resource {} could not be loaded in ThCachedResources.get_resource'.format(
                    resource_code)

        if log_msg is not None:
            if th_session is not None:
                th_session.log('Resource', log_msg)
            else:
                ThSession.cls_log('Resource', log_msg)

        if th_session is not None and this_resource is not None and this_resource.render_jinja_template:
            # we are assuming that only a jinja template page will have a stored procedure / can serve
            # as the current resource for a session.  (We don't want javascript files and the like
            # to be recorded as the current resource.)
            th_session.current_resource = this_resource

        return this_resource


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
        except Exception as e:
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
                    this_sess = None
        finally:
            self.unlock()

    def remove_expired(self, remove_all=False):
        global G_program_options
        self.lock()
        try:
            expireds = {}

            for session_token in self.__sessions:
                this_session = self.__sessions[session_token]
                if remove_all or this_session is None or this_session.date_expire is None or \
                                this_session.date_expire < datetime.datetime.now() or \
                        (
                                            G_program_options.sql_timeout > 0 and this_session.date_sql_timeout is not None and this_session.date_sql_timeout < datetime.datetime.now()):
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

    def log(self, category, *args, severity=10000):
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
                    this_sess.log('Sessions', 'Retrieving existing session {}', session_token, comments)
        finally:
            self.unlock()
        return this_sess

    def _poll_remove_expired(self):
        global G_server_is_running

        last_poll = datetime.datetime.now()

        while self.background_thread_running and G_server_is_running:
            # self.log('PollRemoveExpired', 'Running background_thread_running')
            if ((datetime.datetime.now() - last_poll).total_seconds() > REMOVE_EXPIRED_THREAD_SLEEP):
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
        self.log_current_request = True
        self.current_handler = None
        self.comments = None

        self.theas_page = theas.Theas(theas_session=self)

        self.session_token = None

        if sessionless:
            self.session_token = str(uuid.uuid4())
        else:
            self.session_token = this_session_token

        self.logged_in = False

        self.__locked_by = None
        self.__date_locked = None

        self.sql_conn = None

        self.current_resource = None

        self.current_template_str = None

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
                self.request_count = self.request_count + 1
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

        # try to retrieve the session from the global list
        this_sess = G_sessions.retrieve_session(session_token, comments=comments, do_log=do_log)

        if this_sess is not None:
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
                cls.cls_log('Sessions', 'Need to create new session, but inhibit_create prevents new session')
            else:
                # start new session
                session_token = str(uuid.uuid4())
                this_sess = ThSession(session_token)
                this_sess.theas_page.set_value('theas:th:ST', session_token)
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
                print(datetime.datetime.now(), 'ThSession [{}:{}] ({}) - {} ({})'.format(
                    self.session_token,
                    self.request_count,
                    self.__locked_by,
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

                    if LOGIN_AUTO_USER_TOKEN and not self.logged_in and self.current_handler is not None:
                        self.authenticate(None, None, LOGIN_AUTO_USER_TOKEN)

        return self

    def finished(self, persist_to_db=False):
        if not self.__locked_by:
            pass
        else:
            self.date_request_done = datetime.datetime.now()

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

            # if self.initialized:
            #    if USE_SESSION_COOKIE and self.current_handler:
            #        self.current_handler.clear_cookie('theas:th:ST')
            #        self.current_handler.set_secure_cookie('theas:th:ST', self.session_token)
            #        self.next_url = '/'
            #    else:
            #        self.next_url = '/?theasST=' + self.session_token
            #        self.current_handler.clear_cookie('theas:th:ST')



            if self.sql_conn is None:
                self.log('Session', 'Destroying session')
                G_sessions.remove_session(self.session_token)
            else:
                self.log('Session', 'Will time out at', self.date_expire)

            self.log_current_request = True

            self.release_lock(handler=self.current_handler)

    def authenticate(self, username, password, user_token=None):
        error_message = ''
        self.logged_in = False

        self.log('Session', 'Attempting authentication')

        # authenticate user into database app
        proc = ThStoredProc('theas.spdoAuthenticateUser', self)
        if proc.is_ok:
            if username is not None:
                proc.bind(username, _mssql.SQLVARCHAR, '@UserName')
            if password is not None:
                proc.bind(password, _mssql.SQLVARCHAR, '@Password')
            if user_token is not None:
                proc.bind(user_token, _mssql.SQLVARCHAR, '@UserToken')

            # proc.bind(self.session_token, _mssql.SQLVARCHAR, '@SessionToken')

            try:
                session_guid = None

                result_value = proc.execute()

                for row in proc.resultset:
                    session_guid = row['SessionGUID']
                    user_token = row['UserToken']

                if session_guid is not None and (LOGIN_AUTO_USER_TOKEN is None or user_token != LOGIN_AUTO_USER_TOKEN):
                    self.logged_in = True

                proc = None
                del proc

            except Exception as e:
                self.logged_in = False
                self.log('Session', 'Authentication failed:', e)
                error_message = 'Invalid username or password.'
        else:
            self.logged_in = False
            self.log('Session', 'Could not access SQL database server to attempt Authentication.')
            error_message = 'Could not access SQL database server'

        if USE_SESSION_COOKIE and self.current_handler:
            self.current_handler.clear_cookie('theas:th:ST')
            self.current_handler.set_secure_cookie('theas:th:ST', self.session_token, path='/')
            next_url = '/'

            if self.logged_in:
                self.current_handler.clear_cookie('theas:th:UserToken')
                if user_token is not None:
                    self.current_handler.set_secure_cookie('theas:th:UserToken', user_token, path='/')

        else:
            next_url = '/?theasST=' + self.session_token
            self.current_handler.clear_cookie('theas:th:ST')

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
                self.log('SQL', 'In ThSession.logout, exception calling sql_conn.cancel(). {}'.format(e.message))
            finally:
                self.log('SQL', 'Call to cancel() on SQL connection complete')

            try:
                proc = ThStoredProc('theas.spdoLogout', self)
                if proc.is_ok:
                    proc.bind(self.session_token, _mssql.SQLVARCHAR, '@SessionToken')
                    proc.execute()
            except Exception as e:
                self.log('SQL', 'In ThSession.logout, exception calling theas.spdoLogout. {}'.format(e.message))

            try:
                self.sql_conn.close()
                self.sql_conn = None
            except Exception as e:
                self.log('SQL', 'In ThSession.logout, exception calling sql_conn.close(). {}'.format(e.message))
            finally:
                self.log('SQL', 'In ThSession.logout, call to close() on SQL connection complete')

    def bounce_back(self, url=None):
        # returns tiny html document to send to browser to cause the browser to post back to us
        if not url:
            if self.bookmark_url:
                url = self.bookmark_url
                self.bookmark_url = None
            else:
                url = '/'

        buf = '''<!doctype html>
<html>
<body>
<form id="frmBounce" method="POST" action="{action}" onSubmit="noDef();">
    <input type="hidden" name="theas:th:ST" value="{session_token}"/>
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

        buf = buf.format(action=url, session_token=self.session_token, xsrf=self.current_handler.xsrf_form_html())

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
        this_data['_Local'] = {}
        this_data['_Local']['ST'] = self.session_token

        if self.current_handler is not None:
            this_data['_Local']['xsrf_token'] = self.current_handler.xsrf_token.decode('ascii')
            this_data['_Local']['__handler_guid'] = self.current_handler.handler_guid

        this_data['_Local']['theasServerPrefix'] = G_program_options.server_prefix

        # this_data['_Local']['xsrf_formHTML'] = self.current_handler.xsrf_form_html()
        this_data['_Local']['theasParams'] = self.theas_page.get_controls()

        if self.current_resource is not None:
            this_data['_Local']['theasCurrentPage'] = self.current_resource.resource_code
        this_data['_Local']['theasIncludes'] = G_cached_resources.static_blocks_dict
        this_data['_Local']['theasJS'] = 'Theas.js'

        now_time = datetime.datetime.now().strftime("%I:%M%p")
        this_data['_Local']['Now'] = now_time

        return this_data

    def build_login_screen(self):
        global G_cached_resources

        self.log('Response', 'Building login screen')

        buf = '<html><body>No data in build_login_screen</body></html>'

        resource = None
        template_str = ''

        resource = G_cached_resources.get_resource(LOGIN_RESOURCE_CODE, self)

        if resource is None:
            # raise Exception ('Could not load login screen template from the database.  Empty template returned from call to theas.spgetSysWebResources.')
            buf = '<html><head><meta http-equiv="refresh" content="30"></meta><body>Could not load login screen template from the database server.  Empty template returned from call to theas.spgetSysWebResources.<br /><br />Will try again shortly... </body></html>'

        else:
            template_str = resource.data
            this_data = self.init_template_data()

            this_data['_Local']['errorMessage'] = self.theas_page.get_value('theas:ErrorMessage')

            buf = self.theas_page.render(template_str, data=this_data)

        return buf


# -------------------------------------------------
# ThHandler main request handler
# -------------------------------------------------
class ThHandler(tornado.web.RequestHandler):
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)
        self.session = None
        self.handler_guid = str(uuid.uuid4())
        self.set_header('Server', 'Theas/{}'.format('.081'))

    def __del__(self):
        self.session = None

    def write_error(self, status_code, **kwargs):
        global g_program_options
        buf = '<html><body>Unhandled error in ThHandler</body></html>'
        try:
            this_err_cls = None
            this_err = ''
            this_trackback = None
            lines = []

            if 'exc_info' in kwargs:
                this_err_cls, this_err, this_traceback = kwargs['exc_info']

            if status_code == 404:
                buf = '<html><body>Error 404:  File not found</body></html>'
            else:
                for line in traceback.format_exception(*kwargs["exc_info"]):
                    lines.append(line)

                buf = '<html><body><p>Sorry, but you encountered an error at {}.</p>' \
                      '<p>Click <a href="{}">here</a> to log in and try again.</p>' \
                      '<p>{}</p><p>{}</p></body></html>'
                buf = buf.format(
                    str(datetime.datetime.now()),
                    G_program_options.server_prefix,
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
                buf = '0x'.encode('ascii') + binascii.hexlify(file_obj['body']).decode('ascii')
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
            self.session.log('process_uploaded_files(', 'New connection')
            self.session.init_session()

        if self.request.headers.get('Content-Type') == 'application/octet-stream':
            self.session.log('Request', 'Delivering binary body to SQL')
            process_file(bindata=self.request.body,
                         filename=self.request.headers.get('X-File-Name'),
                         filetype=self.request.headers.get('X-File-Type')
                         )

        if len(self.request.files) > 0:
            self.session.log('Request', 'Delivering upload files to SQL')

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

        resource = G_cached_resources.get_resource(resource_code, self.session)

        if resource is None:
            if template_str is None:
                msg = 'Could not load {} from the database.  '.format(
                    'default template' if resource_code is None else 'template "{}"'.format(resource_code)
                ) + ' Probably this user is not configured to use this server.' + \
                      '<p>Click <a href="{}">here</a> to log in and try again.</p>'.format(
                          G_program_options.server_prefix)

                template_str = '<html><body>' + msg + '</body></html/>'

        else:
            template_str = resource.data

            self.session.current_resource = resource
            self.session.current_template_str = template_str

            if template_str is None or len(template_str) == 0:
                msg = 'Could not load {} from the database.  '.format(
                    'default template' if resource_code is None else 'template "{}"'.format(resource_code)
                ) + ' Empty template was returned.' + \
                      '<p>Click <a href="{}">here</a> to log in and try again.</p>'.format(
                          G_program_options.server_prefix)

                template_str = '<html><body>' + msg + '</body></html>'

        return template_str, resource

    def get_data(self, resource, suppress_resultsets=False):
        # Get actual quest data

        form_params = self.request.body_arguments
        # form_params_str = urlparse.urlencode(form_params, doseq=True)

        form_params_str = ''
        for key in form_params:
            this_val = form_params[key]

            if isinstance(this_val, list) and len(this_val) > 0:
                this_val = this_val[0]

            if isinstance(this_val, bytes):
                this_val = this_val.decode('utf-8')
            elif this_val:
                this_val = str(this_val)

            this_val = this_val.replace('=', '%3D').replace('&', '%26')
            form_params_str = form_params_str + '&' + key.replace('=', '%3D').replace('&', '%26') + '=' + this_val

        theas_params_str = self.session.theas_page.serialize()

        proc = None

        if resource and resource.api_stored_proc:
            proc = ThStoredProc(resource.api_stored_proc, self.session)

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

            if '@Document' in proc.parameter_list:
                proc.bind(self.request.path.rsplit('/', 1)[1], _mssql.SQLCHAR, '@Document')

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

                    this_val = this_val.replace('=', '%3D').replace('&', '%26')
                    headers_str = headers_str + '&' + key.replace('=', '%3D').replace('&', '%26') + '=' + this_val

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

        # if not suppress_resultsets:
        if True:
            #  The stored procedure may return one or more resultsets.
            #  Resultsets may return a single row--most appropariately stored in a dictionary, or may contain many rows--most
            #  appropriately stored in a list of dictionaries.
            #
            #  For a single-row resultset stored in a dictionary, values can be accessed as:
            #    this_data['general']['MO_Number']
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

            this_data = self.session.init_template_data()
            this_data['_resultsetMeta'] = {}

            redirect_to = None
            history_go_back = False

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
                    row_count = row_count + 1
                    if ((max_rows > 1) and (row_count > max_rows)):
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

                if this_resultset_info['name'] in ('header', 'general'):
                    if row is not None:
                        if 'TheasParams' in row:
                            theas_params_str = row['TheasParams']
                            if theas_params_str:
                                # Incorporate any Theas control changes from SQL, so these values can be used
                                # when rendering the template.
                                self.session.theas_page.process_client_request(buf=theas_params_str, accept_any=True)
                        if 'Cookies' in row:
                            cookies_str = row['Cookies']
                            # Cookies returns a string like name1=value1&name2=value2...

                            if cookies_str:
                                for this_pair in cookies_str.split('&'):
                                    self.clear_cookie(this_pair.split('=')[0])
                                    self.set_secure_cookie(this_pair.split('=')[0], this_pair.split('=')[1], path='/')

                        # Check to see if stored proc indicates we should go back in history
                        if 'RedirectTo' in row:
                            redirect_to = row['RedirectTo']

                        # Check to see if stored proc indicates we should go back in history
                        if 'DoHistoryGoBack' in row:
                            if str(row['DoHistoryGoBack']) == '1':
                                history_go_back = True

                have_next_resultset = self.session.sql_conn.nextresult()
                if not have_next_resultset:
                    break

                this_data['_Local']['theasParams'] = self.session.theas_page.get_controls()

            return this_data, redirect_to, history_go_back
        else:
            return None, None, None

    @run_on_executor
    def get_data_background(self, resource, suppress_resultsets=False):
        return self.get_data(resource, suppress_resultsets=suppress_resultsets)

    @run_on_executor
    def authenticate_user_background(self, u, pw):
        return self.session.authenticate(u, pw)

    @run_on_executor
    def build_login_screen_background(self):
        return self.session.build_login_screen()

    def do_render_jinja(self, this_resource=None):
        # Gets data and renders template.  Used by get only

        buf = None
        this_data = None
        redirect_to = None
        history_go_back = False

        if this_resource is not None:

            if this_resource.render_jinja_template:
                this_data, redirect_to, history_go_back = \
                    self.get_data(this_resource)

            buf = self.session.theas_page.render(this_resource.data, data=this_data)

        return buf, redirect_to, history_go_back

    @run_on_executor
    def do_render_jinja_background(self, this_resource=None):
        return self.do_render_jinja(this_resource=this_resource)

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

        self.session.theas_page.process_client_request(request_handler=self, accept_any=False)
        self.process_uploaded_files()

        # self.session.theas_page.controls['ctrlinputHelloWorld'].value = self.get_body_argument('theasParams', 'NONE')

        if self.get_arguments('doHistoryGoBack'):
            if self.get_argument('doHistoryGoBack') == '1':
                history_go_back = True

        cmd = None
        if self.get_arguments('cmd'):
            cmd = self.get_argument('cmd')
        if not cmd and self.get_body_arguments('cmd'):
            cmd = self.get_body_argument('cmd')

        next_page = None
        this_page = self.session.theas_page.get_value('th:CurrentPage')
        if this_page and self.session and self.session.current_resource and this_page != self.session.current_resource.resource_code:
            # Browser provided a different value for current_page.  Perhaps the user used the back button?
            # In any case, we want to use the right stored procedure for this request.  Getting the template
            # will set that from us.
            template_str, this_resource = self.get_template(this_page)

        need_redir = False

        if cmd is not None:
            pass
            buf = '<html><body>Hello world</body></html>'
        else:
            if self.session.theas_page.get_value('th:PerformUpdate') == '1':
                # Before we can process next_page, we need to submit to process this_page post
                self.session.log('Data', 'Performing update of posted data')

                if self.session and self.session.current_resource:
                    this_data, redirect_to, history_go_back = \
                        self.get_data(self.session.current_resource, suppress_resultsets=True)
                    self.session.theas_page.set_value('th:PerformUpdate', '0')
                    need_redir = True
                    # Perform redirect after processing the post (i.e. Post-Redirect-Get PRG) pattern

            if not next_page:
                next_page = self.session.theas_page.get_value('th:NextPage')
            if not next_page:
                next_page = self.request.path.rsplit('/', 1)[1]

            if next_page == '' or next_page == 'None' or next_page == "default":
                next_page = None

            if (
              next_page and
              (need_redir or
               (self.session and self.session.current_resource and
                next_page != self.session.current_resource.resource_code
                )
               )
              ):
                redirect_to = next_page

            else:
                template_str, this_resource = self.get_template(next_page)

                if this_resource.on_before:
                    this_function = getattr(TheasCustom, this_resource.on_before)
                    if this_function is not None:
                        handled = this_function(self, args, kwargs)

                if not handled and not history_go_back and self.session is not None:
                    # render output using template and data

                    if this_resource and this_resource.api_stored_proc:
                        self.session.log('Data', 'Calling get_data')
                        this_data, redirect_to, history_go_back = \
                            self.get_data(this_resource)

                    if this_resource and this_resource.render_jinja_template and redirect_to is None and not history_go_back:
                        self.session.log('Render', 'Calling theas_page.render')
                        buf = self.session.theas_page.render(template_str, data=this_data)
                        self.session.log('Render', 'Done with theas_page.render')
                    else:
                        # template_str does not need to be merged with data
                        buf = template_str

                    if this_resource.on_after:
                        this_function = getattr(TheasCustom, this_resource.on_after)
                        if this_function is not None:
                            handled = this_function(self, args, kwargs)

        return buf, redirect_to, history_go_back, handled

    @run_on_executor
    def do_post_background(self, *args, **kwargs):
        return self.do_post(args, kwargs)

    @tornado.gen.coroutine
    def wait_for_session(self, seconds_to_wait=10):
        this_sess = None

        orig_cookie_session_token = None
        orig_cookie = self.get_secure_cookie('theas:th:ST')
        if orig_cookie is not None:
            orig_cookie_session_token = orig_cookie.decode(encoding='UTF-8')
            # orig_cookie_session_token = orig_cookie

        if self.get_arguments('theas:th:ST'):
            # Look for session token in request
            this_session_token = self.get_argument('theas:th:ST')
        else:
            # fall back to cookie
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

            if USE_SESSION_COOKIE:
                if orig_cookie_session_token != this_sess.session_token:
                    self.clear_cookie('theas:th:ST', path='/')
                    self.set_secure_cookie('theas:th:ST', this_sess.session_token, path='/')
                next_url = '/'
            else:
                next_url = '/?theas:th:ST=' + this_sess.session_token
                self.clear_cookie('theas:th:ST', path='/')

        return this_sess

    @tornado.gen.coroutine
    def post(self, *args, **kwargs):
        # MAIN ENTRY POINT FOR HTTP POST REQUEST
        self.session = yield self.wait_for_session()

        this_finished = False
        handled = False

        buf = None
        redirect_to = None
        history_go_back = False

        if self.session is not None:
            # This is a post.  The next page may be specified in a form field theas:th:NextPage.
            if not self.session.logged_in and self.get_arguments('u') and self.get_arguments('pw'):
                if self.get_arguments('u') and self.get_arguments('pw'):
                    # The requested page is explicitly the login screen
                    error_message = ''
                    if USE_WORKER_THREADS:
                        success, error_message = yield self.authenticate_user_background(self.get_argument('u'),
                                                                                         self.get_argument('pw'))
                    else:
                        success, error_message = self.session.authenticate(self.get_argument('u'),
                                                                           self.get_argument('pw'))

                    # if not self.session.authenticate(self.get_argument('u'), self.get_argument('pw')):
                    if not success:
                        self.session.theas_page.set_value('theas:ErrorMessage', 'Error: {}.'.format(error_message))
                        buf = self.session.build_login_screen()
                        self.write(buf)

                    else:
                        self.session.theas_page.set_value('theas:ErrorMessage', '')
                        self.session.log('Response', 'Sending bounce')
                        self.write(self.session.bounce_back())
                        #                else:
                        #                    if not self.session.logged_in:
                        #                        self.session.log('Response', 'Sending login screen')
                        #                        self.session.bookmark_url = self.request.path.rsplit('/', 1)[1]
                        #                        if USE_WORKER_THREADS:
                        #                            buf = yield self.build_login_screen_background()
                        #                        else:
                        #                            buf = self.session.build_login_screen()
                        #                        self.write(buf)
            else:
                # Handle the actual form processing here. When done, we will persist session data and redirect.
                if USE_WORKER_THREADS:
                    buf, redirect_to, history_go_back, handled = yield self.do_post_background(args, kwargs)
                else:
                    buf, redirect_to, history_go_back, handled = self.do_post(args, kwargs)

                if not handled:
                    if redirect_to is not None:
                        this_finished = True
                        self.session.finished()
                        self.redirect(redirect_to)
                    else:
                        if history_go_back and self.session is not None:

                            if len(self.session.history) > 0:
                                self.session.history.pop()
                                this_history_entry = self.session.history[-1]

                                self.theas_page.set_value('theas:th:NextPage', this_history_entry['PageName'])

                                self.session.log('Response', 'Sending bounce')
                                this_finished = True
                                buf = self.session.bounce_back()

                        if buf is None:
                            buf = '<html><body>No content to send in ThHandler.post()</body></html>'
                        self.write(buf)
                        self.session.log('Response', 'Sending response')

        else:
            self.write('<mtml><body>Error: cannot process request without a valid session</body></html>')

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

        # do everything needed to process an HTTP GET request

        def write_log(sess, category, *args):
            if sess is not None:
                sess.log(category, *args)
            else:
                ThSession.cls_log(category, *args)

        # buf = '<html><body>No content returned when processing HTTP GET request in ThHandler.get'
        handled = False
        buf = None
        redirect_to = None
        history_go_back = False

        # try to find required resource
        resource_code = None
        resource = None

        if not resource_code:
            if len(args) > 0:
                resource_code = args[0]

        '''Desired flow:
        1) Receive request
        2) Handle cached public resources
        3) else get a sesssion
        4) (may not be logged in)
        5) Suppose
        '''

        # note: self.session is probably not yet assigned
        resource = G_cached_resources.get_resource(resource_code, self.session, none_if_not_found=True)

        # see if the resource is public (so that we can serve up without a session)
        if resource is not None and resource.exists and resource.is_public and \
                not resource.render_jinja_template and \
                not resource.on_before and not resource.on_after:
            # note:  resource.data will usually be str but might be bytes
            buf = resource.data

        else:
            # Retrieve or create a session.  We want everyone to have a session (even if they are not authenticated)
            self.session = yield self.wait_for_session()

            if self.session is None:
                self.write('<mtml><body>Error: cannot process request without a valid session</body></html>')
            else:
                # we have a session, but are not necessarily logged in

                # try to auto-login if there is a user cookie
                orig_cookie_user = self.get_secure_cookie('theas:th:UserToken')
                if orig_cookie_user:
                    orig_cookie_user = orig_cookie_user.decode(encoding='ascii')

                if orig_cookie_user:
                    self.session.authenticate(None, None, user_token=orig_cookie_user)

                if not resource_code and DEFAULT_RESOURCE_CODE and not self.session.logged_in:
                    # resource_code was not provided and user is not logged in:  use default resource
                    # If the user is logged in, we want get_resource to select the appropriate
                    # resource for the user.
                    resource_code = DEFAULT_RESOURCE_CODE

                # Call get_resources again, this time with a session
                resource = G_cached_resources.get_resource(resource_code, self.session, none_if_not_found=True)

                if resource is not None and resource.exists:

                    if resource.on_before:
                        this_function = getattr(TheasCustom, resource.on_before)
                        if this_function:
                            handled = this_function(self, args, kwargs)

                    if resource.requires_authentication and not self.session.logged_in:

                        if not self.session.logged_in:
                            # still not logged in:  present login screen
                            self.session.bookmark_url = self.request.path.rsplit('/', 1)[1]
                            buf = self.session.build_login_screen()

                            write_log(self.session, 'Response', 'Sending login screen')

                    if buf is None and (not resource.requires_authentication or self.session.logged_in):
                        if resource.render_jinja_template:
                            buf, redirect_to, history_go_back = self.do_render_jinja(this_resource=resource)
                        else:
                            # note:  resource.data will usually be str but might be bytes
                            buf = resource.data

                if resource is not None and resource.on_after:
                    this_function = getattr(TheasCustom, resource.on_after)
                    if this_function:
                        handled = this_function(self, args, kwargs)

        if not handled:
            if redirect_to is not None:
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

                if resource is not None and resource.filename:
                    self.set_header('Content-Type', theas.Theas.mimetype_for_extension(resource.filename))
                    self.set_header('Content-Disposition', 'filename=' + resource.filename)

                self.write(buf)
                self.finish()

            if self.session is not None:
                self.session.finished()




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
        self.session.log('Request', 'Retrieving quest attachment')

        attachment = None
        attachment_guid = None
        filename = None
        buf = None

        if self.get_arguments('guid'):
            attachment_guid = self.get_argument('guid')

        if attachment_guid is not None:
            # Get attachment data from database
            proc = ThStoredProc('theas.spgetAttachment', self.session)
            if proc.is_ok:
                proc.bind(attachment_guid, _mssql.SQLCHAR, '@AttachmentGUID')

                proc_result = proc.execute(fetch_rows=False)
                for row in proc._th_session.sql_conn:
                    filename = row['Filename']
                    buf = row['AttachmentData']

        if buf is not None:
            attachment = {}
            attachment['filename'] = filename
            attachment['data'] = buf

        return attachment

    @run_on_executor
    def retrieve_attachment_background(self):
        return self.retrieve_attachment(self)

    def retrieve_webresource(self):
        global G_cached_resources

        # Do everything that is needed to process a request for a sys web resource
        self.session.log('Request', 'Retrieving web resource')

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
        # MAIN ENTRY POINT FOR HTTP GET REQUEST

        self.session = yield self.wait_for_session()

        if self.session is not None:
            if self.get_arguments('rc'):
                if USE_WORKER_THREADS:
                    resource = yield self.retrieve_webresource_background()
                else:
                    resource = self.retrieve_webresource()

                self.session.log('Response', 'Sending SysWebResource')
                self.set_header('Content-Type', theas.Theas.mimetype_for_extension(resource.filename))
                self.set_header('Content-Disposition', 'filename=' + resource.filename)
                self.write(resource.data)
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
                    self.session.log('Response', 'Sending attachment response')
                    self.set_header('Content-Type', theas.Theas.mimetype_for_extension(attachment['filename']))
                    self.set_header('Content-Disposition', 'filename=' + attachment['filename'])
                    self.write(attachment['data'])
                    self.finish()
                else:
                    self.send_error(status_code=404)

            self.session.finished()
            self.session = None


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
        return '<html><body>Made it to TestThreadedHandler.backgroud_process_requesat!</body></html>'

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
        buf = None
        self.finish()


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

        if self.session is not None:
            self.session.logout()
            G_sessions.remove_session(self.session.session_token)

        self.clear_cookie('theas:th:ST')
        self.clear_cookie('theas:th:UserToken')

        self.redirect('/')

        self.session = None
        # no self.finish needed, due to redirect
        # self.finish()


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

        self.clear_cookie('theas:th:ST')
        self.clear_cookie('theas:th:UserToken')

        # self.redirect('/')
        # self.session = None
        ##no self.finish needed, due to redirect
        ##self.finish()

        self.session = yield self.wait_for_session()
        buf = self.session.build_login_screen()

        if self.session is not None:
            self.session.log('Response', 'Sending login screen')

        self.set_header('Content-Type', theas.Theas.mimetype_for_extension('login.html'))
        self.set_header('Content-Disposition', 'filename=' + 'login.html')

        self.write(buf)
        self.finish()

        if self.session is not None:
            self.session.finished()


# -------------------------------------------------
# ThHandler_Async async (AJAX) handler
# -------------------------------------------------
class ThHandler_Async(ThHandler):
    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)

    def __del__(self):
        self.session = None

    @tornado.gen.coroutine
    def post(self, resource_code=None, *args, **kwargs):
        G_cached_resources

        cmd = None
        if self.get_arguments('cmd'):
            cmd = self.get_argument('cmd')
        if not cmd and self.get_body_arguments('cmd'):
            cmd = self.get_body_argument('cmd')

        self.session = yield self.wait_for_session()

        if self.session is not None:
            # do_log=(not cmd == 'heartbeat'))

            if cmd == 'heartbeat':
                if self.session is not None and self.session.sql_conn is not None:
                    self.write('sessionOK')
                else:
                    self.write('invalidSession')

                if self.session is not None:
                    self.session.finished()

            else:
                async_proc_name = None;
                theas_params_str = ''

                if self.session is not None:
                    self.session.log('Async', str(self.request.body_arguments))

                    if self.session.current_resource is None:
                        self.session.current_resource = G_cached_resources.get_resource(resource_code, self.session)

                    if self.session.current_resource is not None:
                        async_proc_name = self.session.current_resource.api_async_stored_proc

                    self.session.theas_page.process_client_request(request_handler=self, accept_any=False)

                    buf = ''
                    row_count = 0

                    form_params = self.request.body_arguments
                    # form_params_str = urlparse.urlencode(form_params, doseq=True)


                    form_params_str = ''
                    for key in form_params:
                        this_val = form_params[key]

                        if isinstance(this_val, list) and len(this_val) > 0:
                            this_val = this_val[0]

                        if isinstance(this_val, bytes):
                            this_val = this_val.decode('utf-8')
                        elif this_val:
                            this_val = str(this_val)

                        this_val = this_val.replace('%', '%25').replace('&', '%26').replace('=', '%3D')
                        form_params_str = form_params_str + '&' + key.replace('%', '%25').replace('&', '%26').replace(
                            '=', '%3D') + '=' + this_val

                    theas_params_str = self.session.theas_page.serialize()

                    if async_proc_name is not None:
                        proc = ThStoredProc(async_proc_name, self.session)

                        if not proc.is_ok:
                            self.session.log('Async',
                                             'ERROR: AsyncProcName {} is not valid. in ThHandler_Async.Post'.format(
                                                 async_proc_name))
                        else:
                            try:
                                proc.refresh_parameter_list()

                                # if '@QuestGUID' in proc.parameter_list and self.session.theas_page.get_value('questGUID') is not None:
                                #    proc.bind(self.session.theas_page.get_value('questGUID'), _mssql.SQLCHAR, '@QuestGUID')

                                # if '@StepGUID' in proc.parameter_list and self.session.theas_page.get_value('stepGUID') is not None:
                                #    proc.bind(self.session.theas_page.get_value('stepGUID'), _mssql.SQLCHAR, '@StepGUID')

                                # if '@StepDefID' in proc.parameter_list and self.session.theas_page.get_value('stepDefID') is not None:
                                #    proc.bind(self.session.theas_page.get_value('stepDefID'), _mssql.SQLCHAR, '@StepDefID')

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
                                if proc._th_session.sql_conn is not None:
                                    theas_params_str = ''
                                    buf = ''

                                    for row in proc._th_session.sql_conn:
                                        row_count = row_count + 1

                                        if 'TheasParams' in row:
                                            if row['TheasParams'] is not None:
                                                theas_params_str = theas_params_str + row['TheasParams']

                                        if 'AsyncResponse' in row:
                                            if row['AsyncResponse'] is not None:
                                                buf = buf + row['AsyncResponse']

                                self.session.log('Async', '{row_count} rows returned by async stored proc'.format(
                                    row_count=row_count))

                                changed_controls = None

                                if theas_params_str:
                                    changed_controls = self.session.theas_page.process_client_request(
                                        buf=theas_params_str, accept_any=True)

                                    # let stored proc create any desired Theas controls, so these values can be used
                                    # when rendering the template.

                            except:
                                self.session.logout()
                                buf = 'invalidSession'

                    if len(buf) > 0:
                        # stored proc specified an explicit response
                        self.write(buf)
                    else:
                        # stored proc did not specify an explicit response:  send updated controls only
                        # if there are any, otherwise send all controls
                        # self.write(self.session.theas_page.serialize(control_list = changed_controls))

                        # send ALL Theas controls
                        self.write(self.session.theas_page.serialize())

                self.session.finished()
                self.session = None
                self.finish()

    @tornado.gen.coroutine
    def get(self, resource_code=None, *args, **kwargs):
        return self.post(resource_code, *args, **kwargs)


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

                self.theas_page.set_value('theas:th:NextPage', this_history_entry['PageName'])

            self.session.log('Response', 'Sending bounce')
            self.write(self.session.bounce_back())

            ##Handle the actual form processing here. When done, we will persist session data and redirect.
            # buf = yield self.background_process_post_authenticated()
            ##buf = self.background_process_post_authenticated()

            # self.write(buf)
            # self.session.log('Response', 'Sending response for back request')

            self.session.finished()
        else:
            self.session.finished()
            self.redirect('/')

        self.session = None
        self.finish()


# -------------------------------------------------
# ThHandler_PurgeCache purge cache handler
# -------------------------------------------------
class ThHandler_PurgeCache(ThHandler):
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


def run():
    global G_program_options
    global G_server_is_running
    global G_cached_resources
    global G_sessions
    global _cleanup_complete

    global LOGGING_LEVEL
    global SESSION_MAX_IDLE
    global REMOVE_EXPIRED_THREAD_SLEEP
    global LOGIN_RESOURCE_CODE
    global LOGIN_AUTO_USER_TOKEN
    global DEFAULT_RESOURCE_CODE
    global FULL_SQL_IS_OK_CHECK

    msg = 'Theas app getting ready...'
    if LOGGING_LEVEL:
        print(msg)
    write_winlog(msg)

    program_directory, program_filename = get_program_directory()

    msg = 'Theas app: Program directory is: {}'.format(program_directory)
    if LOGGING_LEVEL:
        print(msg)
    write_winlog(msg)

    msg = 'Theas app: program filename is {}'.format(program_filename)
    if LOGGING_LEVEL:
        print(msg)
    write_winlog(msg)

    msg = 'Theas app: program parammeters: {}'.format(str(sys.argv[1:]))
    if LOGGING_LEVEL:
        print(msg)
    write_winlog(msg)

    G_program_options = tornado.options.options

    G_program_options.define("settings_path",
                             default=program_directory,
                             help="The path to the folder with configuration files.", type=str)

    G_program_options.define("server_prefix",
                             default="http://192.168.7.83:8082",
                             help="The web server address prefix to prepend to URLs that need it.", type=str)

    G_program_options.define("port",
                             default=8082,
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

    G_program_options.define("default_resource_code",
                             default=DEFAULT_RESOURCE_CODE,
                             help="Resource code to use when a resource is not specified (i.e. like index.htm)",
                             type=str)

    G_program_options.define("full_sql_is_ok_check",
                             default=FULL_SQL_IS_OK_CHECK,
                             help="Resource code of the login screen template.",
                             type=bool)

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

    SESSION_MAX_IDLE = G_program_options.session_max_idle_minutes  # Max idle time (in minutes) before TheasServer session is terminated
    REMOVE_EXPIRED_THREAD_SLEEP = G_program_options.session_expired_poll_seconds  # Seconds to sleep in between polls in background thread to check for expired sessions
    LOGGING_LEVEL = int(
        G_program_options.logging_level)  # 0 to disable all, 1 to enable all, other value for threshold to exceed.
    LOGIN_RESOURCE_CODE = G_program_options.login_resource_code
    LOGIN_AUTO_USER_TOKEN = G_program_options.login_auto_user_token
    DEFAULT_RESOURCE_CODE = G_program_options.default_resource_code
    FULL_SQL_IS_OK_CHECK = G_program_options.full_sql_is_ok_check

    msg = 'Starting Theas server {} (in {}) on port {}.'.format(
        program_filename, program_directory, G_program_options.port)
    print(msg)
    write_winlog(msg)

    if not LOGGING_LEVEL:
        print("Note: Logging is disabled")

    G_cached_resources = ThCachedResources()  # Global list of cached resources
    G_sessions = ThSessions()  # Global list of sessions

    try:
        G_cached_resources.load_resource('Theas.js', None, from_filename=G_program_options.settings_path + 'Theas.js',
                                         is_public=True)
    except Exception as e:
        msg = 'Theas app: error loading in file Theas.js: {}'.format(e)
        print(msg)
        write_winlog(msg)
        sys.exit()

    try:
        G_cached_resources.load_resource(None, None, all_static_blocks=True, sessionless=True)
    except Exception as e:
        msg = 'Theas app: error loading in static resources from database: {}'.format(e)
        print(msg)
        write_winlog(msg)
        sys.exit()

    _mssql.set_max_connections(G_program_options.sql_max_connections)

    application = tornado.web.Application([
        (r'/attach', ThHandler_Attach),
        (r'/logout', ThHandler_Logout),
        (r'/login', ThHandler_Login),
        (r'/back', ThHandler_Back),
        (r'/purgecache', ThHandler_PurgeCache),
        (r'/test', TestThreadedHandler),
        (r'/async', ThHandler_Async),
        (r'/async/(.*)', ThHandler_Async),
        (r'/(.*)', ThHandler)
    ],
        debug=False,
        autoreload=False,
        xsrf_cookies=True,
        cookie_secret='tF7nGhE6nIcPMTvGPHlbAk5NIoCOrKnlHIfPQyej6Ay=')

    http_server = tornado.httpserver.HTTPServer(application)

    try:
        http_server.listen(G_program_options.port)
    except Exception as e:
        msg = 'Theas app:  Could not start HTTP server on port {}. Is something else already running on that port?'.format(
            G_program_options.port)
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


    tornado.ioloop.IOLoop.current().close()
    # tornado.ioloop.IOLoop.instance().close()
    # http_server.stop()

    ThHandler.executor.shutdown()
    ThSession.cls_log('Shutdown', 'Winding down #1')
    ThHandler_Attach.executor.shutdown()
    ThSession.cls_log('Shutdown', 'Winding down #2')
    TestThreadedHandler.executor.shutdown()
    ThSession.cls_log('Shutdown', 'Winding down #3')

    http_server = None
    del http_server

    G_cached_resources = None
    ThSession.cls_log('Shutdown', 'Winding down #4')

    G_sessions.stop()
    # ThSessions.remove_all_sessions()
    G_sessions = None

    ThSession.cls_log('Shutdown', 'Winding down #5')

    msg = 'Stopped Theas server {} (in {}) on port {}.'.format(
        program_filename, program_directory, G_program_options.port)

    print(msg)
    write_winlog(msg)


if __name__ == "__main__":

    # Trap break.
    G_break_handler = BreakHandler()
    G_break_handler.enable()

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
        G_break_handler.disable()

        # Clean up _mssql resources
        _mssql.exit_mssql()
