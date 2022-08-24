#usr/bin/python

import sys
import os
import platform

import signal
import binascii
import traceback
import logging

import urllib.parse as urlparse

import tornado.httpserver
import tornado.websocket
import tornado.ioloop
import tornado.web
import tornado.options

from pymssql import _mssql

import thcore
from thsession import *
from thsql import *
from thresource import *

import TheasCustom

# We may be run directly, or we may be run via TheasServerSvc
if __name__ == "__main__":
    def write_winlog(*args):
        if len(args) >= 2:
            print(args[1])
        else:
            print(args[0])
else:
    if platform.system() == 'Windows':
        from TheasServerSvc import write_winlog
    else:
        def write_winlog(*args):
            if len(args) >= 2:
                print(args[1])
            else:
                print(args[0])


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

THEAS_VERSION = '0.90.1.255'  # from version.cfg
THEAS_VERSION_INT = '255'

SESSION_MAX_IDLE = 60  # Max idle time (in minutes) before TheasServer session is terminated
REMOVE_EXPIRED_THREAD_SLEEP = 60  # Seconds to sleep in between polls in background thread to check for expired sessions, 0 to disable
LOGGING_LEVEL = 1  # Enable all logging.  0 to disable all, other value to specify threshold.
LOGIN_RESOURCE_CODE = 'login'
LOGIN_AUTO_USER_TOKEN = None
DEFAULT_RESOURCE_CODE = None

FULL_SQL_IS_OK_CHECK = False
SQL_TIMEOUT = 60

USE_WORKER_THREADS = False
MAX_WORKERS = 30
USE_SESSION_COOKIE = True
REMEMBER_USER_TOKEN = False
FORCE_REDIR_AFTER_POST = True

USE_SECURE_COOKIES = True
SESSION_HEADER_NAME = 'X-Theas-Sesstoken'
SESSION_COOKIE_NAME = 'theas:th:ST'
USER_COOKIE_NAME = 'theas:th:UserToken'
SERVER_PREFIX = 'localhost:8881'

COOKIE_SECRET = 'tF7nGhE6nIcPMTvGPHlbAk5NIoCOrKnlHIfPQyej6Ay='

MAX_CACHE_ITEM_SIZE = 1024 * 1024 * 100      # Only cache SysWebResources that are less than 100 Meg in size
MAX_CACHE_SIZE = 1024 * 1024 * 1024 * 2      # Use a maximum of 2 GB of cache

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

G_conns = None

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

def StopServer():
    global G_server_is_running

    G_server_is_running = False

    msg = 'StopServer() called'
    log(None, 'Shutdown', msg)
    write_winlog(msg)

    # this_ioloop = tornado.ioloop.IOLoop.current()
    # this_ioloop.add_callback(this_ioloop.stop)

# def set_exit_handler(func):
#    signal.signal(signal.SIGTERM, func)


# def on_exit(signum, frame):
#    log(None, 'Shutdown', 'on_exit() called')
#    StopServer()

def do_periodic_callback():
    global G_server_is_running
    global G_break_handler

    # Called by Tornado once a second.
    # log(None, 'Periodic', 'do_periodic_callback() called')

    if G_break_handler and G_break_handler.trapped:
        # Ctrl-C pressed
        G_server_is_running = False

    # if msvcrt.kbhit():
    #    # Any key pressed
    #    G_server_is_running = False

    if not G_server_is_running:
        log(None, 'Periodic', 'Trying to stop IOLoop.instance()')

        this_ioloop = tornado.ioloop.IOLoop.current()
        this_ioloop.add_callback(this_ioloop.stop)

        # tornado.ioloop.IOLoop.current().stop()
        # tornado.ioloop.IOLoop.instance().stop()
        # tornado.ioloop.IOLoop.instance().add_callback(tornado.ioloop.IOLoop.instance().stop)

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
    #executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

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
            log(None, 'Cookies', 'Flag cookies_changed set to {}'.format(new_val))
            self.__cookies_changed = new_val

    async def get_response_info(self, resource_code, th_session):
        '''
        Determine response length and content type.  Used for HEAD requests.
        :param resource_code:
        :param th_session:
        :param all_static_blocks:
        :param from_filename:
        :param is_public:
        :param is_static:
        :param get_default_resource:
        :return:
        '''


        # load resource from database
        #if th_session is None:
        #    th_session = ThSession(None, new_id=True)

        # Get stored proc theas.spGetResponseInfo
        sql_conn = await ConnectionPool.get_conn(conn_name='get_response_info()')

        proc = ThStoredProc('theas.spgetResponseInfo', None, sql_conn=sql_conn)

        if await proc.is_ok():
            proc.bind(resource_code, _mssql.SQLCHAR, '@ResourceCode', null=(resource_code is None))

            await proc.execute()

            response_info = ThResponseInfo()

            row_count = 0

            self.set_header('Server', 'theas')

            th_session = None

            if proc.sql_conn is not None:
                for row in proc.sql_conn:
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

            proc = None
            del proc

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
            log(None, 'Cookies',
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
            log(None, 'xsrf', xsrf_message)
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
                    SERVER_PREFIX + '/logout',
                    str(this_err),
                    str(lines)
                )

        finally:
            self.write(buf)
            self.finish()

            if self.session is not None:
                self.session.finished()

    async def exec_stored_proc(self, stored_proc_name, cmd='', path_params=''):
        buf = None

        if stored_proc_name:

            row_count = 0

            log(self.session, 'Handler', 'Handler stored proc is: {}'.format(stored_proc_name))
            if self.session:
                if self.session.current_resource and self.session.current_resource.resource_code:
                    log(self.session, 'Handler', 'Resource code is: {}'.format(self.session.current_resource.resource_code))
                else:
                    log(self.session, 'Handler', 'No resource code is set on the session')
            else:
                log(None, 'Handler', 'No session set in ThHandler.exec_stored_proc', )

            proc = ThStoredProc(stored_proc_name, self.session)

            if not await proc.is_ok():
                self.session.log('Handler',
                                 'ERROR: stored_proc_name {} is not valid. in ThHandler.exec_stored_proc'.format(
                                     stored_proc_name))
            else:
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

                if '@Command' in proc.parameter_list and cmd:
                    proc.bind(cmd, _mssql.SQLCHAR, '@Command')

                if '@Document' in proc.parameter_list:
                    proc.bind(self.request.path.rsplit('/', 1)[1], _mssql.SQLCHAR, '@Document')

                if '@RawHTTPCommand' in proc.parameter_list:
                    proc.bind(self.request.uri, _mssql.SQLCHAR, '@RawHTTPCommand')

                if '@PathFull' in proc.parameter_list:
                    proc.bind(self.request.path, _mssql.SQLCHAR, '@PathFull')

                if '@PathParams' in proc.parameter_list and path_params:
                    proc.bind(path_params, _mssql.SQLCHAR, '@PathParams')

                if '@HTTPParams' in proc.parameter_list:
                    proc.bind(self.request.query, _mssql.SQLCHAR, '@HTTPParams')

                if '@FormParams' in proc.parameter_list:
                    proc.bind(form_params_str, _mssql.SQLCHAR, '@FormParams')

                if '@TheasParams' in proc.parameter_list:
                    proc.bind(theas_params_str, _mssql.SQLCHAR, '@TheasParams')

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

                if '@RemoteIP' in proc.parameter_list:
                    proc.bind(self.request.remote_ip, _mssql.SQLCHAR, '@RemoteIP')

                if '@UserAgent' in proc.parameter_list:
                    proc.bind(self.request, _mssql.SQLCHAR, '@UserAgent')

                # Execute stored procedure
                await proc.execute()

                redirect_to = None
                theas_params_str = ''
                new_cookies_str = ''
                header_str = ''

                buf = ''

                # For the async stored proc, we are expecting it to return only a single resultset, and in most
                # cases to return only a single row.

                # We watch for a few special column names:  TheasParams is a column the stored proc can use to
                # return name/value pairs that should update the theas_page.controls.  AsyncResponse is a column
                # that the stored proc can use to return raw data that will be passed on to the browser as the
                # response to the async request.

                # If the async stored proc does return multiple rows, these column values from each row are
                # concatenated together.

                if proc.resultset is not None:

                    for row in proc.resultset:
                        row_count += 1

                        if row_count > 1:
                            buf = buf + '&'

                        if 'ErrorMessage' in row:
                            if not row['ErrorMessage'] is None and row['ErrorMessage'] != '':
                                # self.session.theas_page.set_value('theas:th:ErrorMessage', row['ErrorMessage'])
                                # the stored proc can set th:ErrorMessage in TheasParams if it wants.
                                # If the stored proc returns an ErrorMessage column, we send that as the response
                                # without updating the TheasParam at the server
                                buf = 'theas:th:ErrorMessage=' + urlparse.quote(format_error(row['ErrorMessage'])) + '&'

                        if 'TheasParams' in row:
                            if row['TheasParams'] is not None:
                                theas_params_str = theas_params_str + row['TheasParams']

                        if 'Cookies' in row:
                            if row['Cookies'] is not None:
                                new_cookies_str = new_cookies_str + row['Cookies']

                        # Check to see if stored proc indicates we should redirect
                        if 'RedirectTo' in row:
                            redirect_to = row['RedirectTo']

                        if 'HTTPHeaders' in row:
                            header_str = row['HTTPHeaders']

                        if 'AsyncResponse' in row:
                            if row['AsyncResponse'] is not None:
                                buf = buf + row['AsyncResponse']

                self.session.log('Handler', '{row_count} rows returned by handler stored proc'.format(
                    row_count=row_count))

                changed_controls = None

                if theas_params_str:
                    changed_controls = self.session.theas_page.process_client_request(
                        buf=theas_params_str, accept_any=True, from_stored_proc=True)

                    # let stored proc create any desired Theas controls, so these values can be used
                    # when rendering the template.

                if new_cookies_str:
                    for this_pair in new_cookies_str.split('&'):
                        this_name, this_value = this_pair.split('=')
                        this_value = urlparse.unquote(this_value)

                        if this_name == SESSION_COOKIE_NAME:
                            self.cookie_st = this_value
                        elif this_name == USER_COOKIE_NAME:
                            self.cookie_usertoken = this_value
                        else:
                            self.clear_cookie(this_name, path='/')
                            self.set_cookie(this_name, this_value, path='/')

                    self.write_cookies()
                    self.session.log('Cookies', 'Updating cookies as per stored procedure')
                    self.cookies_changed = True

                if header_str:
                    # HTTPHeaders returns a string like name1=value1&name2=value2...
                    for this_pair in header_str.split('&'):
                        this_name, this_value = this_pair.split('=')
                        self.set_header(this_name, this_value)

                    self.session.log('Headers',
                                     'Updating HTTP headers as per stored procedure')

        return buf, changed_controls, redirect_to

    def request_has_files(self):
        return  (
                self.request.headers.get('Content-Type') == 'application/octet-stream' or
                len(self.request.files) > 0
        )

    async def process_uploaded_files(self):
        if not self.request_has_files():
            return

        async def process_file(bindata=None, filename=None, file_obj=None, fieldname=None, filetype=None):
            buf = None

            if bindata is not None:
                buf = '0x' + binascii.hexlify(bindata).decode('ascii')
            elif file_obj is not None:
                buf = '0x' + binascii.hexlify(file_obj['body']).decode('ascii')
                filename = file_obj['filename']
                filetype = file_obj['content_type']

                # fileProc = ThStoredProc('theas.spinsHTTPFiles', self.session)
                # if fileawait proc.is_ok():
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


            await self.session.init_session()

            sql_str = "exec theas.spinsHTTPFiles @FieldName={this_fieldname}, @FileName={this_filename}, @FileType={this_filetype}, @FileData={this_filedata}".format(
                this_fieldname='\'' + fieldname + '\'' if fieldname else 'NULL',
                this_filename='\'' + filename + '\'' if filename else 'NULL',
                this_filetype='\'' + filetype + '\'' if filename else 'NULL',
                this_filedata=buf if buf else 'NULL'
            )

            #self.session.sql_conn.execute_non_query(sql_str)
            await asyncio.get_running_loop().run_in_executor(None, self.session.sql_conn.execute_non_query, sql_str)

        if self.session is not None and (self.session.sql_conn is None or not self.session.sql_conn.connected()):
            self.session.log('POST Files', 'Process_uploaded_files(', 'New connection')
            await self.session.init_session()

        if self.request.headers.get('Content-Type') == 'application/octet-stream':
            self.session.log('POST Files', 'Delivering binary body to SQL')
            await process_file(bindata=self.request.body,
                         filename=self.request.headers.get('X-File-Name'),
                         filetype=self.request.headers.get('X-File-Type')
                               )

        if len(self.request.files) > 0:
            self.session.log('POST Files', 'Delivering upload files to SQL')

            # pass upload files to SQL
            for this_file_field in list(self.request.files.keys()):
                for this_file in self.request.files[this_file_field]:
                    await process_file(file_obj=this_file, fieldname=this_file_field)

    async def get_template(self, resource_code):
        global G_cached_resources
        global G_program_options

        # Get template
        template_str = None

        resultset_str = None

        resource = None

        self.session.log('Resource', 'Fetching resource ', resource_code)
        resource = await G_cached_resources.get_resource(resource_code, self.session)

        if resource is None:
            if template_str is None:
                msg = 'Could not load {} from the database.  '.format(
                    'default template' if resource_code is None else 'template "{}"'.format(resource_code)
                ) + ' Probably this user is not configured to use this server.' + \
                      '<p>Click <a href="{}">here</a> to log in and try again.</p>'.format(
                          SERVER_PREFIX + '/logout')

                template_str = '<html><body>' + msg + '</body></html/>'

        else:
            template_str = resource.data

            if resource is not None and resource.exists and \
                    resource.resource_code != LOGIN_RESOURCE_CODE and \
                    resource.render_jinja_template and \
                    self.session.current_resource != resource:
                # We may have retrieved a cached resource.  Set current_resource.
                self.session.current_resource = resource

            self.session.current_template_str = template_str

            if template_str is None or len(template_str) == 0:
                msg = 'Could not load {} from the database.  '.format(
                    'default template' if resource_code is None else 'template "{}"'.format(resource_code)
                ) + ' Empty template was returned.' + \
                      '<p>Click <a href="{}">here</a> to log in and try again.</p>'.format(
                          SERVER_PREFIX + '/logout')

                template_str = '<html><body>' + msg + '</body></html>'

        return template_str, resource

    async def get_data(self, resource, suppress_resultsets=False):
        # Get actual quest data

        had_error = False

        self.session.comments = 'ThHandler.get_data'

        # Always initialize data--even if there is no APIStoredProc to call.
        # This way a Jinja template can always access data._Theas
        this_data = self.session.init_template_data()

        # serialize form parameters (excluding theas: parameters) to pass into the stored procedure
        form_params = self.request.body_arguments

        cookies_str = ''

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

        # serialize theas parameters to pass into the stored procedure
        theas_params_str = self.session.theas_page.serialize()

        proc = None

        if resource and resource.api_stored_proc:

            proc = ThStoredProc(resource.api_stored_proc, self.session)

            try:
                if not await proc.is_ok():
                    #await self.session.logout()
                    raise TheasServerError('Stored proc {} is not OK in get_data()'.format(self.resource.api_stored_proc))

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

                    if len(this_document) == 0:
                        this_document = None

                    if this_document is not None:
                        if this_document[0] == '/':
                            this_document = this_document[1:]
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

                if '@RemoteIP' in proc.parameter_list:
                    proc.bind(self.request.remote_ip, _mssql.SQLCHAR, '@RemoteIP')

                if '@Cookies' in proc.parameter_list:
                    cookies_str = ''
                    for key in self.cookies.keys():
                        cookies_str += key + '=' + urlparse.quote(self.cookies.get(key).value) + '&'

                    proc.bind(cookies_str, _mssql.SQLCHAR, '@Cookies')

                if '@TheasParams' in proc.parameter_list:
                    # proc.bind(theas_params_str, _mssql.SQLCHAR, '@TheasParams', output=proc.parameter_list['@TheasParams']['is_output'])
                    # Would prefer to use output parameter, but this seems not to be supported by FreeTDS.  So
                    # we look to the resultest(s) returned by the stored proc instead.
                    proc.bind(theas_params_str, _mssql.SQLCHAR, '@TheasParams')

                if '@SuppressResultsets' in proc.parameter_list:
                    proc.bind(str(int(suppress_resultsets)), _mssql.SQLCHAR, '@SuppressResultsets')

                # Execute stored procedure
                await proc.execute()

            except Exception as e:
                had_error = True

                # err_msg = self.format_error(e)
                #err_msg = e.text.decode('ascii')
                err_msg = str(e)

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
            resultset_index = 0
            if proc is not None:
                resultset = proc.resultsets[resultset_index]
            else:
                resultset = []

            for this_resultset_info in resultset_list:
                max_rows = this_resultset_info['max_rows']
                if max_rows is None:
                    max_rows = 0

                if max_rows == 1:
                    this_data[this_resultset_info['name']] = {}
                else:
                    this_data[this_resultset_info['name']] = []

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
                            new_cookies_str = row['Cookies']
                            # Cookies returns a string like name1=value1&name2=value2...

                            if new_cookies_str and cookies_str != new_cookies_str:
                                for this_pair in new_cookies_str.split('&'):
                                    this_name, this_value = this_pair.split('=')
                                    this_value = urlparse.unquote(this_value)

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

                resultset_index = resultset_index + 1
                if resultset_index < len(proc.resultsets):
                    resultset = proc.resultsets[resultset_index]
                else:
                    break

                    # stored proc may have updated Theas controls, so update the copy in data._Theas
                    # this_data['_Theas']['theasParams'] = self.session.theas_page.get_controls()

            # One of our stored procedure resultsets indicated that authentication had been performed.
            # Have the session retrieve existing authentication from the database.
            if perform_authenticate_existing:
                self.session.log('Auth', 'Authenticating due to resource stored proc th:LoggedIn')
                await self.session.authenticate(retrieve_existing=True)

            self.session.comments = None
            return this_data, redirect_to, history_go_back
        else:
            self.session.comments = None
            return None, None, None

    #@run_on_executor
    #def get_data_background(self, resource, suppress_resultsets=False):
    #    return self.get_data(resource, suppress_resultsets=suppress_resultsets)

    #@run_on_executor
    #def authenticate_user_background(self, u, pw):
    #    return self.session.authenticate(username=u, password=pw)

    #@run_on_executor
    #def build_login_screen_background(self):
    #    return self.session.build_login_screen()

    async def do_render_response(self, this_resource=None):
        # Gets data and renders template.  Used by GET only.
        # Note that this method will be called whenever the resource indicates that there is an APIStoredProc,
        # even if a Jinja template is not actually used.
        # Normally run in a thread, and accesses session object

        buf = None
        this_data = None
        redirect_to = None
        history_go_back = False

        if this_resource is not None:

            if this_resource.api_stored_proc or this_resource.render_jinja_template:
                this_data, redirect_to, history_go_back = await self.get_data(this_resource)

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


    async def do_post(self, *args, **kwargs):

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
        if self.request_has_files():
            await self.process_uploaded_files()

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
        template_str, this_resource = await self.get_template(this_page)

        if self.deferred_xsrf:
            self.session.theas_page.set_value('th:PerformUpdate', '1')

        if cmd is not None:
            pass
            #buf = '<html><body>Parameter cmd provided, but not implemented.</body></html>'
        else:
            if self.session.theas_page.get_value('th:PerformUpdate') == '1':
                # Before we can process next_page, we need to submit to process this_page post
                self.session.log('Data', 'Performing update of posted data')

                if self.session and self.session.current_resource:
                    this_data, redirect_to, history_go_back = \
                        await self.get_data(self.session.current_resource, suppress_resultsets=True)
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
                    template_str, this_resource = await self.get_template(next_page)
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
                    log(None, 'xsrf', xsrf_message)
                    self.send_error(status_code=403, message=xsrf_message)
                    handled = True
                else:
                    if this_resource is not None:
                        if this_resource.requires_authentication and not self.session.logged_in:
                            self.session.log('Auth', 'Resource requires auth and user not logged in')
                            # still not logged in:  present login screen
                            self.session.bookmark_url = this_resource.resource_code
                            buf = await self.session.build_login_screen()
                            self.session.log('Auth', 'Sending login screen')

                        else:
                            if this_resource.on_before:
                                this_function = getattr(TheasCustom, this_resource.on_before)
                                if this_function is not None:
                                    handled = this_function(self, args, kwargs)

                            if not handled and not history_go_back and self.session is not None:
                                # render output using template and data

                                buf, redirect_to, history_go_back = await self.do_render_response(this_resource=this_resource)

                                #buf, redirect_to, history_go_back = await asyncio.get_running_loop().run_in_executor(
                                #    None, functools.partial(self.do_render_response, this_resource=this_resource))

                                '''
                                if this_resource and this_resource.api_stored_proc:
                                    self.session.log('Data', 'Calling get_data')
                                    this_data, redirect_to, history_go_back = self.get_data(this_resource)
                                    #xyz

                                if this_resource and this_resource.render_jinja_template and\
                                        redirect_to is None and not history_go_back:
                                    self.session.log('Render', 'Calling theas_page.render')
                                    buf = self.session.theas_page.render(template_str, data=this_data)
                                    self.session.log('Render', 'Done with theas_page.render')
                                else:
                                    # template_str does not need to be merged with data
                                    buf = template_str
                                '''

                                if this_resource and this_resource.on_after:
                                    this_function = getattr(TheasCustom, this_resource.on_after)
                                    if this_function is not None:
                                        handled = this_function(self, args, kwargs)

        return buf, redirect_to, history_go_back, handled

    #@run_on_executor
    #def do_post_background(self, *args, **kwargs):
    #    return self.do_post(args, kwargs)

    #@tornado.gen.coroutine
    async def wait_for_session(self, seconds_to_wait=30, write_to_cookie=True):
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

        log(None, 'Session', 'wait_for_session() found this session token in a cookie: ', this_session_token)

        give_up = False
        failed_to_lock = False
        start_waiting = time.time()
        while this_sess is None and not give_up:
            this_sess, failed_to_lock = await ThSession.get_session(session_token=this_session_token,
                                                              handler_guid=self.handler_guid,
                                                              comments='ThHandler.wait_for_session')
            if this_sess is None:
                await asyncio.sleep(0.5)
                give_up = time.time() - start_waiting > seconds_to_wait


        if this_sess:
            this_sess.current_handler = self
            this_sess.current_xsrf_form_html = self.xsrf_form_html()

            if USE_SESSION_COOKIE and write_to_cookie:
                # next_url = '/'
                if orig_cookie_session_token != this_sess.session_token:
                    self.cookie_st = this_sess.session_token
                    log(None, 'Cookies',
                                      'Updating cookie {} wait_for_session() gave different token ({} vs {})'.format(
                                          SESSION_COOKIE_NAME, orig_cookie_session_token, this_sess.session_token))

            # silently re-authenticate if needed and there is a user cookie
            if not this_sess.logged_in and REMEMBER_USER_TOKEN:
                # try to auto-login if there is a user cookie
                if self.cookie_usertoken:
                    log(None, 'Sessions', 'Reauthenticating user from usertoken cookie')
                    await this_sess.authenticate(user_token=self.cookie_usertoken)
                    if not this_sess.logged_in:
                        log(None, 'Sessions', 'FAILED to reauthenticate user from usertoken cookie')
                        self.cookie_usertokeon = None
                        log(None, 'Cookies',
                                          'Updating cookie {} wait_for_session() could not authenticate original usertoken'.format(
                                              USER_COOKIE_NAME))

            else:
                self.cookie_st = None

            self.write_cookies()

        else:
            log(None, 'Sessions', 'Failed to obtain session in wait_for_session()')


        return this_sess

    #@tornado.gen.coroutine
    async def head(self, *args, **kwargs):
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
        response_info = await self.get_response_info(resource_code, th_session)

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

    #@tornado.gen.coroutine
    async def post(self, *args, **kwargs):
        # MAIN ENTRY POINT FOR HTTP POST REQUEST

        log(None, 'POST', '*******************************')

        #self.session = yield self.wait_for_session()
        self.session = await self.wait_for_session()

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
                #if USE_WORKER_THREADS:
                success, error_message = await self.session.authenticate(username=self.get_argument('u'),
                                                                         password=self.get_argument('pw'))

                # if not self.session.authenticate(username=self.get_argument('u'), password=self.get_argument('pw')):
                if not success:
                    # authentication failed, so send the login screen
                    self.session.theas_page.set_value('theas:th:ErrorMessage', 'Error: {}.'.format(error_message))
                    buf = await self.session.build_login_screen()
                    self.write(buf)

                else:
                    # Authentication succeeded, so continue with redirect
                    # self.session.theas_page.set_value('theas:th:ErrorMessage', '')

                    if self.session.bookmark_url:
                        self.session.log('Proceeding with bookmarked page', self.session.bookmark_url)
                        await self.get_template(self.session.bookmark_url)
                        self.session.bookmark_url = None

                    else:
                        self.session.log('Response', 'Sending clientside redir after login page success')
                        self.write(self.session.clientside_redir())

            if not handled:

                # Handle the actual form processing here. When done, we will persist session data and redirect.
                #if USE_WORKER_THREADS:
                buf, redirect_to, history_go_back, handled = await self.do_post(args, kwargs)

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

    #@tornado.gen.coroutine
    async def get(self, *args, **kwargs):
        ##########################################################
        # MAIN ENTRY POINT FOR HTTP GET REQUEST
        ##########################################################
        global G_cached_resources

        if self.session:
            self.session.comments = 'ThHandler.get'

        # do everything needed to process an HTTP GET request

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

        #self.session = yield self.wait_for_session()
        if self.session is None:
            log(None, 'SessionRetrive', 'At start session is None')
        else:
            log(None, 'SessionRetrieve', 'At start session is:', self.session.session_token)

        self.session = await self.wait_for_session()

        if self.session is None:
            log(None, 'SessionRetrive', 'After wait_for_session() session is None')
        else:
            log(None, 'SessionRetrieve', 'After wait_for_session() session is:', self.session.session_token)



        # A request for a cached public resource does not need a database connection.
        # We can serve up such requests without even checking the session.
        # If we do not check the session, multiple simultaneous requests can be processed,
        if resource_code or self.session:
            resource = await G_cached_resources.get_resource(resource_code, self.session)

        # see if the resource is public (so that we can serve up without a session)
        if resource is not None and resource.exists and resource.is_public and \
                not resource.render_jinja_template and \
                not resource.on_before and not resource.on_after:
            # note:  resource.data will usually be str but might be bytes
            log(None, 'CachedGET', 'Serving up cached resource', resource_code)
            buf = resource.data

        else:
            # Retrieve or create a session.  We want everyone to have a session (even if they are not authenticated)
            # We need to use the session's SQL connection to retrieve the resource

            log(None, 'GET', '*******************************')
            log(None, 'GET', args[0])

            if self.session is None:
                log(None, 'GET Error', 'No session.  Cannot continue to process request.')
                self.write('<html><body>Error: cannot process request without a valid session</body></html>')
            else:
                # we have a session, but are not necessarily logged in
                self.session.log('GET', 'Have session', self.session.session_token)
                self.session.log('GET', 'Received request for: {}'.format(self.request.path))

                self.session.log('Auth' 'User is logged in' if self.session.logged_in else 'User is NOT logged in')

                # Take logged-in users back to where they were
                if not resource_code and self.session.logged_in:
                    resource = self.session.current_resource

                if not resource_code and DEFAULT_RESOURCE_CODE and not self.session.logged_in:
                    # resource_code was not provided and user is not logged in:  use default resource
                    # If the user is logged in, we want get_resource to select the appropriate
                    # resource for the user.
                    resource_code = DEFAULT_RESOURCE_CODE

                if resource is None or not resource.exists:
                    # Call get_resources again, this time with a session
                    resource = await G_cached_resources.get_resource(resource_code, self.session)

                    if resource is None or not resource.exists:
                        # If the user is logged in, but resource_code is not specified, we explicitly set get_default_resource
                        # so that the stored proc can look up the correct resource for us.
                        # This change was made 9/21/2017 to correct a problem that led to 404 errors resulting in serving
                        # up the default resource.
                        self.session.log('Get Resource', 'Logged in?', self.session.logged_in)
                        self.session.log('Get Resource', 'resource_code', resource_code if resource_code is not None else 'None')
                        resource = await G_cached_resources.get_resource(resource_code, self.session,
                                                                   get_default_resource=self.session.logged_in)

                if resource is not None and resource.exists and\
                        resource.resource_code != LOGIN_RESOURCE_CODE and \
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
                            buf = await self.session.build_login_screen()

                            log(self.session, 'Response', 'Sending login screen')

                    if buf is None and (not resource.requires_authentication or self.session.logged_in):
                        if resource.api_stored_proc or resource.render_jinja_template:
                            #buf, redirect_to, history_go_back = self.do_render_response(this_resource=resource)

#                            buf, redirect_to, history_go_back = yield tornado.gen.multi(tornado.ioloop.IOLoop.current().run_in_executor(None, functools.partial(self.do_render_response, this_resource=resource)))

#                            buf, redirect_to, history_go_back = await asyncio.get_running_loop().run_in_executor(None, functools.partial(self.do_render_response, this_resource=resource))
                            buf, redirect_to, history_go_back = await self.do_render_response(this_resource=resource)

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
                        log(self.session, 'Response',
                                  'Sending 404 error in response to HTTP GET request for {}'.format(resource_code))
                        self.send_error(status_code=404)

            if buf is not None:
                log(self.session, 'Response', 'Sending response to HTTP GET request for {}'.format(resource_code))

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
                    self.set_header('Content-Type', thcore.Theas.mimetype_for_extension(self.filename))
                    self.set_header('Content-Disposition', 'inline; filename=' + self.filename)

                elif resource is not None:
                    if resource.filename:
                        if resource.filetype:
                            self.set_header('Content-Type', resource.filetype)
                        else:
                            self.set_header('Content-Type', thcore.Theas.mimetype_for_extension(resource.filename))
                    self.set_header('Content-Disposition', 'inline; filename=' + resource.filename)
                else:
                    self.set_header('Content-Type', thcore.Theas.mimetype_for_extension(resource.resource_code))

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

    #@tornado.gen.coroutine
    async def options(self, resource_code=None, *args, **kwargs):
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
        #executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

        super().__init__(application, request, **kwargs)

    def __del__(self):
        self.session = None

    async def retrieve_attachment(self):
        # Do everything that is needed to process a request for a quest attachment
        # Normally run in a thread, and accesses session object
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
            if await proc.is_ok():
                proc.bind(attachment_guid, _mssql.SQLCHAR, '@AttachmentGUID')

                await proc.execute()
                for row in proc.resultset:
                    filename = row['Filename']
                    buf = row['AttachmentData']
                    if 'Filetype' in row:
                        filetype = row['Filetype']

        attachment = None

        if buf is not None:
            attachment = {}
            attachment['filename'] = filename
            attachment['data'] = buf
            attachment['filetype'] = filetype

        return attachment

    #@run_on_executor
    #def retrieve_attachment_background(self):
    #    return self.retrieve_attachment()

    async def retrieve_webresource(self):
        global G_cached_resources

        # Do everything that is needed to process a request for a sys web resource
        self.session.log('Attach', 'Retrieving web resource')

        resource_code = None
        resource = None

        if self.get_arguments('rc'):
            resource_code = self.get_argument('rc')

            resource = await G_cached_resources.get_resource(resource_code, self.session, for_public_use=True)

        return resource

    #@run_on_executor
    #def retrieve_webresource_background(self):
    #    return self.retrieve_webresource_background(self)

    #@tornado.gen.coroutine
    async def get(self, *args, **kwargs):
        # MAIN ENTRY POINT FOR ATTACH HTTP GET REQUEST

        # retrieve or create session
        log(None, 'Attach', '*******************************')
        log(None, 'Attach', args[0])

        #self.session = yield self.wait_for_session(write_to_cookie=False)
        self.session = await self.wait_for_session()

        if self.session is not None:
            self.session.log('Attach', 'Have session')

            self.session.log('Attach',
                             'Current Resource is {}'.format(
                                 self.session.current_resource.resource_code
                                 if self.session.current_resource
                                 else 'Not Assigned!'
                             ))

            if self.get_arguments('rc'):
                #if USE_WORKER_THREADS:
                resource = await self.retrieve_webresource()

                self.session.log('Attach', 'Sending SysWebResource')
                self.write(resource.data)

                if resource.filetype:
                    self.set_header('Content-Type', resource.filetype)
                else:
                    self.set_header('Content-Type', thcore.Theas.mimetype_for_extension(resource.filename))

                self.set_header('Content-Disposition', 'inline; filename=' + resource.filename)

            else:
                # if not self.session.logged_in:
                #    self.send_error(status_code=404)
                #    self.session.log('Response', 'Sending 404 for attachment request due to no login')
                # else:
                #if USE_WORKER_THREADS:
                attachment = await self.retrieve_attachment()
                #attachment = await asyncio.get_running_loop().run_in_executor(None, self.retrieve_attachment)

                if attachment is not None:
                    self.session.log('Attach', 'Sending attachment response')
                    self.write(attachment['data'])
                    self.set_header('Content-Type', thcore.Theas.mimetype_for_extension(attachment['filename']))

                    if attachment['filetype']:
                        self.set_header('Content-Type', attachment['filetype'])
                    else:
                        if attachment['filename']:
                            self.set_header('Content-Type', thcore.Theas.mimetype_for_extension(attachment['filename']))
                            self.set_header('Content-Disposition', 'inline; filename=' + attachment['filename'])
                    self.finish()
                else:
                    self.send_error(status_code=404)

            self.session.finished()
            self.session = None

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

    #@tornado.gen.coroutine
    async def get(self, *args, **kwargs):
        global G_sessions

        if self.session is None:
            #self.session = yield self.wait_for_session()
            self.session = await self.wait_for_session()

        nextURL = '/'

        if self.session is not None:
            # after logout, try to navigate to the same page
            #if self.session.current_resource:
                #nextURL = self.session.current_resource.resource_code

            await self.session.logout()
            G_sessions.remove_session(self.session.session_token)

        self.cookie_st = None
        self.cookie_usertoken = None
        self.write_cookies()
        log(None, 'Cookies',
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

    #@tornado.gen.coroutine
    async def get(self, *args, **kwargs):
        global G_sessions

        if self.session is None:
            #self.session = yield self.wait_for_session()
            self.session = await self.wait_for_session()

        if self.session is not None:
            await self.session.logout()
            G_sessions.remove_session(self.session.session_token)

        self.cookie_st = None
        self.cookie_usertoken = None
        self.write_cookies()
        log(None, 'Cookies',
                          'Clearing cookies {} and {} due to login'.format(SESSION_COOKIE_NAME, USER_COOKIE_NAME))

        # self.redirect('/')
        # self.session = None
        ##no self.finish needed, due to redirect
        ##self.finish()

        #self.session = yield self.wait_for_session()
        self.session = await self.wait_for_session()
        buf = await self.session.build_login_screen()

        if self.session is not None:
            self.session.log('Response', 'Sending login screen')

        self.set_header('Content-Type', thcore.Theas.mimetype_for_extension('login.html'))
        self.set_header('Content-Disposition', 'inline; filename=' + 'login.html')

        self.write_cookies()

        self.write(buf)
        self.finish()

        if self.session is not None:
            self.session.finished()

    #@tornado.gen.coroutine
    async def post(self, *args, **kwargs):
        # Note:  As of 1/7/2021 the preferred way of performing authentication is via Async (cmd='login')
        # Posting to special login URL is deprecated.

        global G_sessions

        if self.session is None:
            #self.session = yield self.wait_for_session()
            self.session = await self.wait_for_session()

        success = False
        error_message = ''

        success, error_message = await self.session.authenticate()
        self.session.theas_page.set_value('theas:th:ErrorMessage', '{}'.format(error_message))

        resource = await G_cached_resources.get_resource(None, self.session,
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
    #executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)

    def __del__(self):
        self.session = None

    #@tornado.gen.coroutine
    async def post(self, *args, **kwargs):

        global G_cached_resources

        log(None, 'Async', '*******************************')

        # Note:  The async request is to a generic url of /async
        # To determine what type of async request is being made, we look to the session's current_resource
        # If current_resource is not set (such as due to a new session), we look to the Theas param
        # th:CurrentPage

        buf = ''
        changed_controls = None
        redirect_to = None

        resource_code = None
        this_resource = None

        this_document = None
        path_params = None

        first_path_elem = self.request.path.split('/')[1]

        if first_path_elem == 'async':
            # this_document = self.request.path.split('/')[2]
            # resource_code = "/".join(self.request.path.split('/')[3:])

            # The rest of the path (after async) is taken to be the resource code.
            # The resource code may contain /'s
            # Therefore it is not possible to pass in path params on a request to async
            this_document = "/".join(self.request.path.split('/')[2:])
        else:
            this_document = self.request.path

        cmd = None
        if self.get_arguments('cmd'):
            cmd = self.get_argument('cmd')
        if not cmd and self.get_body_arguments('cmd'):
            cmd = self.get_body_argument('cmd')

        #self.session = yield self.wait_for_session()
        self.session = await self.wait_for_session()

        if self.session is not None:

            # update theas parameters based on this post...even if there is not an async stored proc
            th_params = None

            # If parameter th is present, use this as Theas param data, else look to the request_handler
            if self.get_arguments('th'):
                th_params = self.get_argument('th')

            self.session.theas_page.process_client_request(request_handler=self, buf=th_params, accept_any=False)

            # Resource code is determined by:
            #   1) Specific resource that pertains to cmd, i.e. resetPassword -> login
            #   2) path parameters, i.e. this_document
            #   3) Theas param th:CurrentPage
            #   4) Session's current_resource, i.e. last resource requested

            if cmd == 'resetPassword':
                resource_code = 'login'
            elif this_document:
                resource_code = this_document
            elif self.session.current_resource is not None:
                resource_code = self.session.current_resource.resource_code
            else:
                resource_code = self.session.theas_page.get_value('th:CurrentPage').strip()
                # Request may have provided Theas param 'th:CurrentPage'
                # If session does not have current_resource set, trust 'th:CurrentPage'
                # This allows us to process the async request in situations where the session went away due
                # to timeout or server restart (assuming "remember me" / user token in cookie is enabled)

            if self.session.current_resource is None or resource_code != self.session.current_resource.resource_code:
                # Note that an async request will NOT change the session's current_resource
                this_resource = await G_cached_resources.get_resource(resource_code, self.session)
            else:
                this_resource = self.session.current_resource

            self.session.log('Async:',
                             'Resource Code',
                             resource_code
                             if resource_code
                             else 'No current resource for this session!')

            if self.request_has_files():
                await self.process_uploaded_files()
            # process uploaded files, even if there is no async proc


            if cmd == 'heartbeat':
                if self.session is not None and self.session.sql_conn is not None:
                    buf = None
                    changed_controls = None
                    redirect_to = None

                    buf, changed_controls, redirect_to = self.exec_stored_proc('theas.spapiHeartbeat', cmd='')

                    if changed_controls:
                        buf = buf + '&' + self.session.theas_page.serialize(control_list=changed_controls)

                    if buf:
                        self.write(buf)
                    else:
                        self.write('sessionOK')
                else:
                    self.write('invalidSession')

                if self.session is not None:
                    self.session.finished()

            if cmd == 'clearError':
                if self.session is not None and self.session.theas_page is not None and self.session.sql_conn is not None:
                    self.session.theas_page.set_value('th:ErrorMessage', '')

                self.write('clearError')

                self.session.finished()

            if cmd == 'theasParams':
                if self.session is not None:
                    # send ALL Theas controls
                    self.write(self.session.theas_page.serialize())
                    self.session.finished()


            if cmd == 'login':

                success = False
                error_message = ''
                redirect_to = ''

                success, error_message = await self.session.authenticate()
                self.session.theas_page.set_value('theas:th:ErrorMessage', '{}'.format(error_message))

                resource = await G_cached_resources.get_resource(None, self.session,
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

                if self.session is not None:
                    self.session.finished()

                buf = 'theas:th:LoggedIn={}&theas:th:ErrorMessage={}&theas:th:NextPage={}'.format(
                    '1' if self.session.logged_in else '0',
                    error_message,
                    next_page)

                self.write(buf)
                self.finish()

            else:
                async_proc_name = None

                if self.session is not None:
                    # self.session.log('Async', str(self.request.body_arguments))

                    try:

                        if this_resource is None:
                            # Something is wrong.  Perhaps the async request came in before a resource had been served?
                            # This could happen if the TheasServer was restarted after a page was sent to the browser,
                            # Javascript on the page could submit an async requests...which we can't handle, because
                            # the original session no longer exists.

                            raise TheasServerError(
                                'There is a problem with your session. Click the "reload" button in your browser.' +
                                '|Invalid Session|Async request was received before a SysWebResource was served.  Perhaps ' +
                                'your session expired, or the server was restarted after this page was loaded.')
                        else:

                            async_proc_name = this_resource.api_async_stored_proc

                        if async_proc_name:
                            buf, changed_controls, redirect_to = await self.exec_stored_proc(async_proc_name, cmd=cmd, path_params=path_params)

                    except TheasServerError as e:
                        # e = sys.exc_info()[0]
                        err_msg = e.value if hasattr(e, 'value') else e

                        buf = 'theas:th:ErrorMessage=' + urlparse.quote(format_error(err_msg))

                    except Exception as e:
                        # We would like to catch specific MSSQL exceptions, but these are declared with cdef
                        # in _mssql.pyx ... so they are not exported to python.  Should these be declared
                        # with cpdef?

                        err_msg = None

                        err_msg = str(e)

                        buf = 'theas:th:ErrorMessage=' + urlparse.quote(format_error(err_msg))
                        self.session.log('Async',
                                         'ERROR when executing stored proc {}: {}'.format(
                                             async_proc_name, err_msg))

                if redirect_to:
                    # redirect as the stored procedure told us to
                    self.session.finished()
                    self.session = None
                    self.redirect(redirect_to)
                else:
                    if len(buf) > 0:
                        # stored proc specified an explicit response
                        try:
                            json_buf = json.loads(buf)
                            # buf looks like it contains JSON.  Add an element containing TheasParams
                            json_buf['theasParams'] = self.session.theas_page.serialize(control_list=changed_controls)
                            self.write(json.dumps(json_buf))
                        except ValueError as e:
                            # buf does not look like it contains JSON.  Just send the string.
                            self.write(buf)

                    else:
                        # Stored proc did not specify an explicit response, but may have updated TheasParams.
                        # Send updated TheasParams only.
                        self.write(self.session.theas_page.serialize(control_list=changed_controls))

                        # send ALL TheasParams
                        #self.write(self.session.theas_page.serialize())

                    # CORS
                    self.set_header('Access-Control-Allow-Origin', '*')  # allow CORS from any domain
                    self.set_header('Access-Control-Max-Age', '0')  # disable CORS preflight caching

                    self.session.finished()
                    self.session = None
                    self.finish()

    #@tornado.gen.coroutine
    async def get(self, *args, **kwargs):

        return self.post(*args, **kwargs)

    def data_received(self, chunk):
        pass


# -------------------------------------------------
# ThHandler_REST handler
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

'''

class ThHandler_REST(ThHandler):
    def __init__(self, application, request, **kwargs):
        super().__init__(application, request, **kwargs)

    def __del__(self):
        self.session = None

    #@tornado.gen.coroutine
    async def post(self, *args, **kwargs):
        global G_cached_resources

        buf = ''
        bufbin = b''

        rest_proc_name = 'theas.spdoRESTRequest'

        try:
            # spin up a new session
            #self.session = yield self.wait_for_session()
            self.session = await self.wait_for_session()

            if self.session is None:
                raise TheasServerError('Session could not be established for REST request.')

            requesttype_guid_str = None
            requesttype_code = None
            buf = None
            bufbin = b''


            request_path = None
            if len(args) > 0:
                request_path = args[0]

            if request_path is not None and request_path.split('/')[0] == 'r':
                # Special case:  an "r" as the first segment of the path, such as:
                # r/resourcecode/aaa/bbb
                # indicates that the second segment is to be the resource code.
                # This allows URLs such as /r/img/myimg.jpg to be handled dynamically:  the resource img is
                # loaded, and then myimg.jpg is passed in.  (Otherwise the resource would be taken to be
                # img/myimg.jpg
                requesttype_code = request_path.split('/')[1]
            else:
                requesttype_code = request_path

            if requesttype_code:
                requesttype_code = requesttype_code.strip()

            if requesttype_code == '':
                resource_code = None

            requesttype_guid_str = self.request.query_arguments.get('rg')

            # allow REST to receive file uploads
            if self.request_has_files():
                await self.process_uploaded_files()

            # serialize form parameters (excluding theas: parameters) to pass into the stored procedure
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

            cookies_str = ''
            for key in self.cookies.keys():
                cookies_str += key + '=' + urlparse.quote(self.cookies.get(key).value) + '&'

            # serialize theas parameters to pass into the stored procedure
            theas_params_str = self.session.theas_page.serialize()

            # Execute spDoRestRequest in the database
            proc = ThStoredProc(rest_proc_name, self.session)


            self.session.log('REST', 'REST stored proc is: {}'.format(rest_proc_name))

            if not await proc.is_ok():
                self.session.log('REST',
                                 'ERROR: REST proc name {} is not valid. in ThHandler_Async.Post'.format(
                                     rest_proc_name))
            else:

                if '@RequestTypeGUIDStr' in proc.parameter_list:
                    proc.bind(requesttype_guid_str, _mssql.SQLCHAR, '@RequestTypeGUIDStr', null=(requesttype_guid_str is None))

                if '@RequestTypeCode' in proc.parameter_list:
                    proc.bind(requesttype_code, _mssql.SQLCHAR, '@RequestTypeCode', null=(requesttype_code is None))

                if '@HTTPParams' in proc.parameter_list:
                    proc.bind(self.request.query, _mssql.SQLCHAR, '@HTTPParams')

                if '@FormParams' in proc.parameter_list:
                    proc.bind(form_params_str, _mssql.SQLCHAR, '@FormParams')
                    # proc.bind(urlparse.urlencode(self.request.body_arguments, doseq=True), _mssql.SQLCHAR, '@FormParams')

                if '@TheasParams' in proc.parameter_list:
                    proc.bind(theas_params_str, _mssql.SQLCHAR, '@TheasParams')

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

                if '@Cookies' in proc.parameter_list:
                    proc.bind(cookies_str, _mssql.SQLCHAR, '@Cookies')

                if '@RemoteIP' in proc.parameter_list:
                    proc.bind(self.request.remote_ip, _mssql.SQLCHAR, '@RemoteIP')

                if '@InhibitResultset' in proc.parameter_list:
                    proc.bind('0', _mssql.SQLCHAR, '@InhibitResultset')

                await proc.execute()

                this_response_no = None
                this_redir_url = None
                new_cookies_str = None

                row_count = 0

                # For the REST stored proc, we are expecting it to return only a single resultset that
                # contains only a single row.

                # We watch for a few special column names: RESTResponse is a column
                # that the stored proc can use to return raw data that will be passed on to the browser as the
                # response to the REST request. Similarly, RESTResponseBin can contain binary data
                # to send to the browser.  (If present and not null, RESTResponseBin will be served
                # instead of RestResponse.)


                try:
                    if proc.resultset is not None:
                        for row in proc.resultset:
                            # note:  should only be one row
                            row_count += 1

                            if 'ResponseNo' in row:
                                this_response_no = row['ResponseNo']

                            if 'RedirURL' in row:
                                this_redir_url = row['RedirURL']

                            if 'Cookies' in row:
                                new_cookies_str = row['Cookies']
                                if new_cookies_str and cookies_str != new_cookies_str:
                                    for this_pair in new_cookies_str.split('&'):
                                        this_name, this_value = this_pair.split('=')
                                        this_value = urlparse.unquote(this_value)

                                        if this_name == SESSION_COOKIE_NAME:
                                            self.cookie_st = this_value
                                        elif this_name == USER_COOKIE_NAME:
                                            self.cookie_usertoken = this_value
                                        else:
                                            self.clear_cookie(this_name, path='/')
                                            self.set_cookie(this_name, this_value, path='/')

                                    self.write_cookies()
                                    self.session.log('Cookies', 'Updating cookies as per stored procedure F')
                                    self.cookies_changed = True

                            if 'Filename' in row:
                                this_filename = row['Filename']
                                if this_filename:
                                    self.set_header('Content-Type', thcore.Theas.mimetype_for_extension(this_filename))
                                    self.set_header('Content-Disposition', 'inline; filename=' + this_filename)

                            if 'ErrorMessage' in row:
                                if not row['ErrorMessage'] is None and row['ErrorMessage'] != '':
                                    buf = 'Stored procedure returned an error:' + \
                                          urlparse.quote(format_error(row['ErrorMessage']))

                            if ('ContentBin' in row):
                                if not row['ContentBin'] is None and row['ContentBin'] != '':
                                    bufbin = row['ContentBin']
                                    if bufbin:
                                        bufbin = bytes(bufbin)

                            if not bufbin and ('RESTResponse' in row):
                                if row['RESTResponse'] is not None:
                                    buf = row['RESTResponse']

                            if not bufbin and not buf and ('Content' in row):
                                if not row['Content'] is None and row['Content'] != '':
                                    buf = row['Content']

                            if 'TheasParams' in row:
                                theas_params_str = row['TheasParams']
                                if theas_params_str:
                                    # Incorporate any Theas control changes from SQL, so these values can be used
                                    # when rendering the template.
                                    self.session.theas_page.process_client_request(buf=theas_params_str,
                                                                                   accept_any=True,
                                                                                   from_stored_proc=True)

                                    if theas_params_str.find('th:LoggedIn=') >= 0:
                                        # Stored procedure is indicating authentication status changed.  Retrieve
                                        # current session info.
                                        perform_authenticate_existing = True

                            # self.set_header('Date', response_info.current_date)
                            # self.set_header('Expires', response_info.content_expires)

                            # self.set_header('accept-ranges', 'bytes')  # but not really...
                            # self.set_header('Content-Type', response_info.content_type)

                            # self.set_header('Cache-Control', response_info.cache_control)
                            # self.set_header('Last-Modified', response_info.date_updated)

                            # CORS
                            # self.set_header('Access-Control-Allow-Origin', '*')  # allow CORS from any domain
                            # self.set_header('Access-Control-Max-Age', '0')  # disable CORS preflight caching
                        assert row_count > 0, 'No result row returned by REST stored proc.'

                    else:
                        buf = None
                        bufbin = None

                except:
                    buf = None
                    bufbin = None

                if this_redir_url:
                    self.redirect(this_redir_url)
                elif this_response_no >= 400:
                    self.send_error(this_response_no)
                elif buf is None and bufbin is None:
                    self.send_error(status_code=500)
                else:
                    if this_response_no:
                        self.set_status(this_response_no)
                    if len(bufbin) > 0:
                        self.set_header('Content-Length', len(bufbin))
                        self.write(bufbin)
                    elif buf:
                        self.set_header('Content-Length', len(buf.encode('utf-8')))
                        self.write(buf)

                    # CORS
                    self.set_header('Access-Control-Allow-Origin', '*')  # allow CORS from any domain
                    self.set_header('Access-Control-Max-Age', '0')  # disable CORS preflight caching

                    self.finish()

                if 1 == 0:
                    #if the async request came in on an existng session we don't want to close it!
                    proc.sql_conn.close()
                    proc.sql_conn = None
                    proc.th_session.sql_conn = None

                    proc = None

                self.session.finished()
                #note:  since sql_conn is None, finished() will destroy the session

                self.session = None



        except Exception as e:
            # We would like to catch specific MSSQL exceptions, but these are declared with cdef
            # in _mssql.pyx ... so they are not exported to python.  Should these be declared
            # with cpdef?

            err_msg = str(e)
            self.session.log('REST',
                             'ERROR when executing REST stored proc {}: {}'.format(
                                 rest_proc_name, err_msg))




    #@tornado.gen.coroutine
    async def get(self, *args, **kwargs):

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

    #@tornado.gen.coroutine
    async def get(self, *args, **kwargs):

        if self.session is None:
            # try to get the session, but do not wait for it
            #self.session = yield self.wait_for_session(seconds_to_wait=0)
            self.session = await self.wait_for_session()

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
            if self.cookies_changed:
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

    #@tornado.gen.coroutine
    async def get(self, *args, **kwargs):
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

        log(None, 'Cache', message)

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
    # Note:  Client receives 403 error without the following check_origin
    # https://stackoverflow.com/questions/24851207/tornado-403-get-warning-when-opening-websocket
    # http://www.tornadoweb.org/en/stable/websocket.html#configuration
    def check_origin(self, origin):
        # This method is called when a new connection request is received
        # but before the connection has been established.
        # origin contains the value of the HTTP Origin header.
        # This function can return True if we want to accept the new connection
        # or False if we want to reject the connection (sends 403)
        return True

    def open(self):
        log(None, 'WebSocket', 'New client connected')
        self.write_message("You are connected")

    # the client sent the message
    def on_message(self, message):
        self.write_message('DoFetchData2')

    # client disconnected
    def on_close(self):
        log(None, 'WebSocket', 'Client disconnected')


def get_program_settings():
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
    global SQL_TIMEOUT

    global FORCE_REDIR_AFTER_POST

    global USE_SECURE_COOKIES
    global SESSION_HEADER_NAME
    global SESSION_COOKIE_NAME
    global USER_COOKIE_NAME
    global SERVER_PREFIX

    global USE_WORKER_THREADS
    global MAX_WORKERS

    global MAX_CACHE_ITEM_SIZE
    global MAX_CACHE_SIZE

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
                             default=SERVER_PREFIX,
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
                             default=SQL_TIMEOUT,
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

    G_program_options.define("max_cache_item_size",
                             default=MAX_CACHE_ITEM_SIZE,
                             help="Maximum size in bytes of item that is allowed to be stored in cache.",
                             type=int)

    G_program_options.define("max_cache_size",
                             default=MAX_CACHE_SIZE,
                             help="Maximum total amount of bytes to use for cache storage.",
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
    SERVER_PREFIX = G_program_options.server_prefix
    USER_COOKIE_NAME = G_program_options.user_cookie_name
    USE_WORKER_THREADS = G_program_options.use_worker_threads
    MAX_WORKERS = G_program_options.max_worker_threads
    SQL_TIMEOUT = G_program_options.sql_timeout


def run(run_as_svc=False):
    global G_program_options
    global G_server_is_running
    global G_cached_resources
    global G_sessions
    global G_conns
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

    global MAX_CACHE_ITEM_SIZE
    global MAX_CACHE_SIZE

    program_directory, program_filename = get_program_directory()

    get_program_settings()

    if LOGGING_LEVEL:
        msg = 'Theas app getting ready...'
        write_winlog(msg)
        print(msg)

    if not run_as_svc:
        # Trap breaks.
        G_break_handler = BreakHandler()

    if G_break_handler:
        G_break_handler.enable()

    msg = 'Starting Theas server {} (in {}) on port {}.'.format(
        program_filename, program_directory, G_program_options.port)
    print(msg)
    write_winlog(msg)

    if not LOGGING_LEVEL:
        print("Note: Logging is disabled")


    G_sessions = ThSessions()  # Global list of sessions

    G_conns = ConnectionPool(
        SQLSettings(
            server=G_program_options.sql_server,
            port=G_program_options.sql_port,
            user=G_program_options.sql_user,
            password=G_program_options.sql_password,
            database=G_program_options.sql_database,
            appname=G_program_options.sql_appname,
            max_conns=G_program_options.sql_max_connections,
            sql_timeout=G_program_options.sql_timeout,
            full_ok_checks=FULL_SQL_IS_OK_CHECK,
            http_server_prefix=G_program_options.server_prefix,
            login_auto_user_token=LOGIN_AUTO_USER_TOKEN
        )

    )

    G_cached_resources = ThCachedResources(
        G_program_options.settings_path,
        static_file_version_no=THEAS_VERSION_INT,
        max_cache_item_size=MAX_CACHE_ITEM_SIZE,
        max_cache_size=MAX_CACHE_SIZE,
        conn_pool=G_conns
    )  # Global list of cached resources

    config_thsession(
        gsess=G_sessions,
        gconns=G_conns,
        gresources=G_cached_resources,
        remember_user_token=REMEMBER_USER_TOKEN,
        session_max_idle=SESSION_MAX_IDLE,
        sql_timeout=SQL_TIMEOUT,
        login_resource_code=LOGIN_RESOURCE_CODE,
        server_prefix=SERVER_PREFIX,
        login_auto_user_token=LOGIN_AUTO_USER_TOKEN
    )



    if run_as_svc:
        # make sure there is an ioloop in this thread (needed for Windows service)
        #io_loop = tornado.ioloop.IOLoop()
        #io_loop.make_current()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    http_server = None

    async def main():

        max_init_connections = 0
        for i in range(max_init_connections):
            await G_conns.get_conn(force_new=True, conn_name='pre-load')

        try:
            await G_cached_resources.load_global_resources()

        except Exception as e:
            msg = 'Theas app: error global cached resources when calling G_cached_resources.load_global_resources(): {}'.format(
                e)
            print(msg)
            traceback.print_exc()

            write_winlog(msg)
            sys.exit()

        application = tornado.web.Application([
            (r'/attach', ThHandler_Attach),
            (r'/attach/(.*)', ThHandler_Attach),
            (r'/logout', ThHandler_Logout),
            (r'/login', ThHandler_Login),
            (r'/back', ThHandler_Back),
            (r'/purgecache', ThHandler_PurgeCache),
            #(r'/test', TestThreadedHandler),
            (r'/ws', ThWSHandler_Test),
            (r'/rest', ThHandler_REST),
            (r'/rest/(.*)', ThHandler_REST),
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

        http_server = tornado.httpserver.HTTPServer(application, xheaders=True)

        try:
            http_server.listen(G_program_options.port)
            await asyncio.Event().wait()
        except Exception as e:
            msg = 'Theas app:  Could not start HTTP server on port {}. Is something else already running on that port? {}'.format(
                G_program_options.port, e)
            print(msg)
            write_winlog(msg)
            sys.exit()

        G_server_is_running = True

    if __name__ == "__main__":
        asyncio.run(main())

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
    log(None, 'Shutdown', msg)
    write_winlog(msg)

    # ioloop = tornado.ioloop.IOLoop.current()
    # ioloop.add_callback(ioloop.stop)
    http_server.stop()

    # ThHandler.executor.shutdown()
    # log(None, 'Shutdown', 'Winding down #1')
    # ThHandler_Attach.executor.shutdown()
    # log(None, 'Shutdown', 'Winding down #2')
    # TestThreadedHandler.executor.shutdown()
    # log(None, 'Shutdown', 'Winding down #3')

    http_server = None
    del http_server

    G_cached_resources = None
    log(None, 'Shutdown', 'Winding down #4')

    G_sessions.stop()
    # ThSessions.remove_all_sessions()
    G_sessions = None

    log(None, 'Shutdown', 'Winding down #5')

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
        log(None, 'Shutdown', 'Application has ended')

        # all_objects = muppy.get_objects()
        # sum1 = summary.summarize(all_objects)
        # summary.print_(sum1)

        # os.kill(0, signal.CTRL_BREAK_EVENT)
    finally:
        pass

        # Clean up _mssql resources
        # _mssql.exit_mssql()

