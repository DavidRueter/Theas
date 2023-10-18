from thbase import *
from threading import RLock
from pymssql import _mssql
import asyncio
import concurrent.futures
import uuid


'''thsql.py is part of Theas.  This module declares the class ThStoredProc, as well as ConnectionPool and SQLSettings.
A helper function for convenience named call_auth_storedproc() is also defined.

thsql is currently set up to work with pymsql.  More specifically, only the low-level _msql module from pymsql is used.

Theas generally prefers associating a long-lived SQL connection with each active user session.  This allows us
to store user-specific state information in the connection itself...which among other things lets us make use 
of OpsStream's authentication and access control capabilities.

Theas envisions long-lived user sessions.  A user will likely be logged in for a significant length of time.
Giving each user their own connection also minimizes overhead associated with treating SQL connections as
on-demand, stateless resources.

thsql also implements a simple ConnectionPool.  This provides support for a best-of-both-worlds approach to
connection management:  requests from non-authenticated users that don't need connection-based state can
enjoy the benefits of reduced connection establishment overhead.  Also by having a connection pool it is
easier to have good control over connection utilization, and to safely share a connection with multiple threads
(though only one thread can access the connection at a time.)

Theas instantiates a single global ConnectionPool, and passes in database connection settings when the constructor
is called.


ThStoredProc allows the application to specify a stored procedure name.  The object then confirms database
connection health, and retrieves parameter information from SQL.  The caller then can call the .bind() method to
pass in values to parameters, and the .execute() method to execute the procedure.

The .execute() method runs the procedure, and then fetches all resultsets into python dictionaries.  In this way,
the caller can release the lock on the connection immediately after execution is complete, and can safely access
and process the resultset data after the connection lock has been released. 
 


'''

_LOGGING_LEVEL = 1
_LOGIN_AUTO_USER_TOKEN= None

class SQLSettings:
    def __init__(self, server='someserver', port=1433, user='someuser', password='somepassword',
                 database='somedatabase', appname='someapp', max_conns=10, sql_timeout=120,
                 full_ok_checks=True, http_server_prefix='https://someserver.com',
                 login_auto_user_token=_LOGIN_AUTO_USER_TOKEN,
                 logging_level=_LOGGING_LEVEL
                ):
        self.server = server
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.appname = appname
        self.max_conns = max_conns
        self.sql_timeout = sql_timeout
        self.full_ok_checks = full_ok_checks
        self.http_server_prefix = http_server_prefix
        self.login_auto_user_token = login_auto_user_token
        self.logging_level = logging_level


        _mssql.set_max_connections(max_conns)

class Conn():
    def __init__(self, sql_conn):
        self.sql_conn = sql_conn #pymssql._mssql.MSSQLConnection
        self.name = "new"
        self.id = str(uuid.uuid4())
        self.is_public_authed = False
        self.is_user_authed = False
        self.last_error = None


    def __del___(self):
        if self.sql_conn is not None and self.sql_conn.connected:
            self.sql_conn.cancel()
            self.sql_conn.close()
            self.sql_conn=None
            del self.sql_conn

    @property
    def connected(self):
        return self.sql_conn is not None and self.sql_conn.connected

G_thsql_executor = None

def set_executor(executor=None, max_workers=100):
    global G_thsql_executor

    if G_thsql_executor is None:
        if executor is not None:
            G_thsql_executor = executor
        else:
            G_thsql_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='thsql')

def thsql_executor():
    global G_thsql_executor

    if G_thsql_executor is None:
        set_executor()

    return G_thsql_executor



class ConnectionPool:

    def __init__(self, sql_settings=SQLSettings()):
        self.lock = RLock()
        self.sql_settings = sql_settings
        self.conns = []
        self.conns_inuse = []
        self.conns_torelease = []

        global _LOGIN_AUTO_USER_TOKEN
        _LOGIN_AUTO_USER_TOKEN = sql_settings.login_auto_user_token


    def __del__(self):
        with self.lock:

            for conn in self.conns:
                self.conns = None

            for conn in self.conns_inuse:
                self.conns = None

    def kill_threads(self, reason=''):
        log(None, 'SQL', 'thsql.py Killing all executor threads in kill_threads() {}'.format(reason))
        executor = thsql_executor()
        if executor is not None:
            with self.lock:
                executor.shutdown(wait=False, cancel_futures=True)


    # NOTE: These methods operate on connections, but they exist in support of pool operations.
    # Some of them need to access resources in the pool connection list.
    # For these reasons they are methods of ConnectionPool instead of methods of Conn
    async def reset_conn(self, conn):
        proc = ThStoredProc('theas.spactLogout', None, conn=conn)
        exec_ok = await proc.execute()
        conn.name = "idle"
        conn.is_user_authed = False
        conn.is_public_authed = False

        log(None, 'SQL', 'Connection reset.', conn.id)

    async def init_conn(self, conn):
        # Initialize theas session:  stored proc returns SQL statements we need to execute
        proc = ThStoredProc('theas.spgetInitSession', None, conn=conn)
        if await proc.is_ok(skip_init=True):
            if '@ServerPrefix' in proc.parameter_list:
                with self.lock:
                    proc.bind(self.sql_settings.http_server_prefix, _mssql.SQLCHAR, '@ServerPrefix')

            exec_ok = await proc.execute()

            if exec_ok:
                for row in proc.resultset:
                    sql_str = row['SQLToExecute']
                    conn.sql_conn.execute_non_query(sql_str)

                await call_auth_storedproc(conn=conn)

                #log(None, 'SQL', 'Connection initialized.  FreeTDS version: ' + str(conn.sql_conn.tds_version))
                log(None, 'SQL', 'Connection initialized.', conn.id)
            else:
                log(None, 'SQL',
                    'Connection initialization failed.',
                    conn.id,
                    'Error calling theas.spgetInitSession: {}'.
                    format(conn.last_error))

    async def new_conn(self, skip_init=False, conn_name=""):
        if self.sql_settings is None:
            raise TheasServerSQLError('Error: must provide sql_settings)')

        # try:
        conn = Conn(
            sql_conn = _mssql.connect(
                server=self.sql_settings.server,
                port=self.sql_settings.port,
                user=self.sql_settings.user,
                password=self.sql_settings.password,
                database=self.sql_settings.database,
                appname=self.sql_settings.appname
            )
        )
        conn.sql_conn.query_timeout = self.sql_settings.sql_timeout

        if conn_name:
            conn.name = conn_name

        log(None, 'SQL', 'created_conn() Created new SQL connection name:', conn.name, 'id:', conn.id)

        if not skip_init:
            await self.init_conn(conn)

        return conn

    async def add_conn(self, conn=None, use_now=True, skip_init=False, conn_name=''):
        if conn is None:
            # Note: new_conn() does create a new SQL connection, and will block the main async IO loop
            # unless the caller uses an executor thread.  However we expect connections to be fast
            # to create, and generally there are a modest number of connections...so at this time
            # we are willing to accept blocking.  The caller can use an executor thread if needed.

            # We want to avoid working with the connection (_mssql object) across threads, and
            # we want to store the connection in the list...which requires a lock.  And we also
            # prefer not to have threads locking the global list.

            conn = await self.new_conn(skip_init=skip_init, conn_name=conn_name)

        conn.name = conn_name

        with self.lock:
            if use_now:
                self.conns_inuse.append(conn)
            else:
                self.conns.append(conn)

        return conn

    async def get_conn(self, force_new=False, skip_init=False, conn_name='no name'):
        conn = None

        with self.lock:
            if len(self.conns) > 0 and not force_new:
                conn = self.conns.pop()
                self.conns_inuse.append(conn)
                log(None, 'SQLConn', 'get_conn() is returning connection', conn_name, conn.id,
                    'from pool. Remaining in pool: ', len(self.conns))
                conn.name = conn_name
        if conn is None:
            #conn = await asyncio.get_running_loop().run_in_executor(None, functools.partial(self.add_conn, skip_init=skip_init, conn_name=conn_name))

            conn = await self.add_conn(skip_init=skip_init, conn_name=conn_name)
            log(None, 'SqlConn', 'get_conn() is returning new SQL connection', conn_name, conn.id,
                '. Remaining in pool: ', len(self.conns))

        return conn

    def release_conn_sync(self,conn):
        if conn is not None:
            with self.lock:
                self.conns_torelease.append(conn)
                log(None, 'Conn', 'SQL connection is scheduled to be released. Name:', conn.name, 'id:', id)

    async def process_release_conns(self):
        with self.lock:
            log(None, 'Conn', 'process_release_conns() about to process', len(self.conns_torelease), 'connections')

            this_conn = None
            for i, this_conn in enumerate(self.conns_torelease):
                self.conns_torelease.pop(i)

                if this_conn is not None:
                    await self.release_conn(this_conn)

    async def release_conn(self, conn):
        with self.lock:
            log(None, 'SQL', 'release_conn() called for conn name:', conn.name, 'id:', conn.id)

            for i, this_conn in enumerate(self.conns_inuse):
                if this_conn == conn:
                    self.conns_inuse.pop(i)

                    #if conn.is_user_authed or not conn.is_public_authed:
                        #connection must be reset before going back into the pool
                    await self.reset_conn(this_conn)
                    self.conns.append(conn)

                    log(None, 'SQL', 'Returned conn to pool:', conn.id)
                    break

            log(None, 'SQL', 'Avail connection count:', len(self.conns))
    def kill_conn(self, conn):
        with self.lock:
            for i, this_conn in enumerate(self.conns_inuse):
                if this_conn == conn:
                    self.conns_inuse.pop(i)
                    if this_conn.sql_conn.connected:
                        this_conn.sql_conn.close()

                    del this_conn

                    break

# for convenience: a wrapper function to call the authentication stored proc
async def call_auth_storedproc(th_session=None, conn=None, username=None, password=None, user_token=None,
                               retrieve_existing=False, is_recurse=False):
    # authenticate user into database app
    # returns None if authentication failed, else a resultset with details of the user and session

    result = None

    this_conn = conn
    if this_conn is None and th_session is not None:
        this_conn = th_session.conn

    if this_conn is None:
        raise TheasServerError('Error:  call_auth_storedproc was called without a SQL connection')

    if username is None and user_token is None:
        user_token = _LOGIN_AUTO_USER_TOKEN

    this_conn.is_user_authed = False
    this_conn.is_public_authed = False

    if th_session is not None:
        th_session.logged_in = False

    try:

        proc = ThStoredProc('theas.spdoAuthenticateUser', th_session, conn=conn)
        if await proc.is_ok(skip_init=True):
            await proc.refresh_parameter_list()

            if retrieve_existing:
                proc.bind(retrieve_existing, _mssql.SQLVARCHAR, '@RetrieveExisting')
            else:
                if username is not None:
                    proc.bind(username, _mssql.SQLVARCHAR, '@UserName')
                if password is not None:
                    proc.bind(password, _mssql.SQLVARCHAR, '@Password')
                if user_token is not None:
                    proc.bind(user_token, _mssql.SQLVARCHAR, '@UserToken')
                if th_session is not None and th_session.session_token is not None:
                    # @SessionToken is informational only:  allows the web session to be logged in the database
                    proc.bind(th_session.session_token, _mssql.SQLVARCHAR, '@SessionToken')

            exec_result = await proc.execute()

            if not exec_result:
                log(th_session, 'Session', 'Error in call_auth_storedproc()', this_conn.last_error)

                err_msg = this_conn.last_error

                if 1 == 0:
                    # We failed ot log in with the provided credentials.  We would like this connection to fall back
                    # to being logged in as the public web user.
                    if this_conn is not None and not is_recurse:
                        log(th_session, 'Session', 'Recursively calling call_auth_storedproc() with _LOGIN_AUTO_USER_TOKEN')
                        await call_auth_storedproc(is_recurse=True, user_token=_LOGIN_AUTO_USER_TOKEN, conn=this_conn)

                if th_session is not None:
                    th_session.error_message = err_msg

            else:
                # May not necessary: if stored proc completes successfully then authentication should have succeeded
                if len(proc.resultset) > 0 and 'SessionGUID' in proc.resultset[0] and\
                        proc.resultset[0]['SessionGUID'] is not None:
                    this_conn.is_public_authed = (user_token == _LOGIN_AUTO_USER_TOKEN)
                    if not this_conn.is_public_authed:
                        this_conn.is_user_authed = True

                    #result = this_conn.is_public_authed or this_conn.is_user_authed
                    result = proc.resultset

                if th_session is not None:
                    th_session.logged_in = this_conn.is_user_authed
                    th_session.conn.name = username if username else user_token[:5] + '...' #for logging / debugging

        else:
            log(th_session, 'Session', 'Authentication stored proc not is_ok in call_auth_storedproc()')
            if th_session is not None:
                th_session.error_message = 'Could not access SQL database server|Sorry, the server is not available right now|1|Cannot Log In'

    except Exception as e:
        this_conn.last_error = repr(e)
        log(th_session, 'Session', 'Unexpected exception in call_auth_storedproc(). ', str(e))
        if th_session is not None:
            th_session.error_message = 'Could not access SQL database server. ' + str(e) + '|Sorry, the server is not available right now|1|Cannot Log In'

    return result

async def call_logout_storedproc(th_session=None, conn=None):
    this_conn = conn
    if this_conn is None and th_session is not None:
        this_conn = th_session.conn

    try:
        if th_session is not None:
            th_session.logged_in = False

        if this_conn is not None:
            this_conn.is_user_authed = False
            this_conn.is_public_authed = False

            proc = ThStoredProc('theas.spdoLogout', th_session, conn=conn)
            if await proc.is_ok():
                proc.bind(th_session.session_token, _mssql.SQLVARCHAR, '@SessionToken')
                await proc.execute()

            if this_conn is not None:
                this_conn.init_conn()


    except Exception as e:
        log(th_session, 'SQL', 'In ThSession.logout, exception calling theas.spdoLogout (call_logout_storedproc). {}'.format(e))


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
    def have_session(self):
        return self.th_session is not None

    def __init__(self, this_stored_proc_name, this_th_session, conn=None):
        self.conn = conn
        self._storedproc = None  # to hold _mssql stored proc, which has problems
        self.th_session = None
        self.stored_proc_name = None
        self.parameter_list = {}  # sniffed parameters.  See parameters for bound parameters.
        self.resultset = []
        self.resultsets = []
        self.full_ok_checks = True

        self.stored_proc_name = this_stored_proc_name

        # Use provided session.
        self.th_session = this_th_session
        if self.conn is None and self.have_session:
            if self.th_session.conn is not None:
                self.conn = this_th_session.conn

        # Note: Sessions have lazy-created conn:  when a session is created, the conn may not exist.
        # Subsequently, we check for (and establish if necessary) a connection in is_ok()

    def __del__(self):
        self._storedproc = None
        del self._storedproc

        for rs in self.resultsets:
            for row in rs:
                del row

        del self.resultsets
        del self.resultset

        self.th_session = None
        del self.th_session

    async def is_ok(self, skip_init=False):
        # Obtain a new sql connection for the session if needed

        # By default, this function will initialize the connection (i.e. authenticate, create temporary
        # tables, etc.) if needed
        if not skip_init and self.have_session and (
                self.th_session.conn is None or not self.th_session.conn.connected
        ):
            log(self.th_session, 'StoredProc', 'Calling init_session', self.stored_proc_name)
            await self.th_session.init_session()
            self.conn = self.th_session.conn

        if self.have_session:
            log(self.th_session, 'StoredProc', 'Checking is_ok:', self.stored_proc_name)
            log(self.th_session, 'StoredProc', 'session_token:', self.th_session.session_token)

            if self.th_session.conn is None:
                log(self.th_session, 'StoredProc', 'Session has no connection')
            else:
                log(self.th_session, 'StoredProc', 'Session conn name:', self.th_session.conn.name,
                    'id:', self.th_session.conn.id)

        result = (self.conn is not None and self.conn.connected)

        if result and self.full_ok_checks:
            try:
                sql_str = 'SELECT 1 AS IsOK'
                await asyncio.get_running_loop().run_in_executor(thsql_executor(), self.conn.sql_conn.execute_non_query, sql_str)
            except Exception as e:
                log(self.th_session, 'StoredProc', 'Connection in is_ok is NOT OK:', e)
                result = False

        if not result and self.have_session:
            self.th_session.logged_in = False
            self.th_session.conn = None

        if result:
            await self.refresh_parameter_list()

        return result

    async def refresh_parameter_list(self):
        log(self.th_session, 'StoredProc', 'Refreshing parameter list:', self.stored_proc_name)

        if self.parameter_list is not None:
            self.parameter_list = {}
        if self.stored_proc_name is not None and self.conn is not None and self.conn.connected:
            try:
                sql_str = 'EXEC theas.sputilGetParamNames @ObjectName = \'{}\''.format(self.stored_proc_name)
                await asyncio.get_running_loop().run_in_executor(thsql_executor(), self.conn.sql_conn.execute_query, sql_str)

                resultset = [row for row in self.conn.sql_conn]
                for row in resultset:
                    this_param_info = {}
                    this_param_info['is_output'] = row['is_output']
                    this_param_info['value'] = None
                    this_param_info['is_null'] = True

                    self.parameter_list[row['ParameterName']] = this_param_info
                    # self.parameter_list.append(row['ParameterName'])

            except Exception as e:
                if self.have_session:
                    log(self.th_session, 'Sessions', '***Error accessing SQL connection', e)
                    self.th_session.conn = None
                    self.parameter_list = None
                raise
    def do_exec(self, sql_str):
        result = False
        self.conn.last_error = None

        try:
            self.conn.sql_conn.execute_query(sql_str)

            this_resultset = [row for row in self.conn.sql_conn]
            self.resultsets.append(this_resultset)

            if len(self.resultsets) == 1:
                self.resultset = this_resultset

            have_next_resultset = self.conn.sql_conn.nextresult()
            while have_next_resultset:
                this_resultset = [row for row in self.conn.sql_conn]
                self.resultsets.append(this_resultset)
                have_next_resultset = self.conn.sql_conn.nextresult()

            result = True

        except Exception as e:
            self.conn.last_error =repr(e)

        return result



    async def execute(self):
        result = False

        if self.have_session:
            self.th_session.comments = 'ThStoredProc.execute'

        log(self.th_session, 'StoredProc', 'Executing:', self.stored_proc_name)

        _mssql.min_error_severity = 1
        this_result = False

        if self.have_session:
            self.th_session.do_on_sql_start(self)

        try:
            # pymssql and/or FreeTDS have a number of limitations.
            # a) They do not seem to support output parameters
            # b) They truncate input parameters at 8000 characters

            # To work around b), we must not use _storedproc.execute, and must instead build our own
            # SQL query to execute.

            # this_result = self._storedproc.execute(*args, **kwargs)

            this_sql = 'EXEC ' + self.stored_proc_name

            # NOTE:  We don't want a SQL injection risk.  (We'd prefer to let the _mssql library
            # execute the stored procedure and be responsible for escaping parameter values.)
            # But given the limitations mentioned above, this is not an option at this time.
            # We must build our own string that performs the EXEC myproc @Param1='abc'.
            # Our parameter values are already split into separate dictionary items
            # in self.parameters.  Now we need to turn each parameter into a string like
            # @Param1='abc' and concatenate these together.
            # As long as any single quotes embedded in the parameter values are replaced with
            # 2 single quotes, and that there are no single quotes at all in parameter names,
            # we should be safe.

            this_params_str = ''

            for name, item in self.parameter_list.items():

                if name.startswith('@'):
                    # Strip out single quotes from parameter name.  (Shouldn't be any, but we don't
                    # want someone to try to use this as a SQL injection vector.)
                    this_params_str += ' ' + name.replace('\'', '') + '='

                    # Replace each single quote with two single quotes.  If param value is None
                    # output NULL (with no quotes)
                    this_params_str += '\'' + str(item['value']).replace('\'', '\'\'') + '\'' \
                        if item['value'] is not None else 'NULL'

                    this_params_str += ','

            if this_params_str.endswith(','):
                this_params_str = this_params_str[:-1]

            # Note that we could instead have built the string as '@Param1=%s, @Param2=%s, @Param3=%s'
            # Then theoretically we could then pass in list(self.parameters.values())
            # This way _mssql could do the quoting of param values for us, and dwe wouldn't need
            # to concatenate all the values.  But null values would be a problem
            # self.th_session.conn.sql_conn.execute_query(
            #   this_sql + '@Param1=%s, @Param2=%s', list(self.parameters.values()))

            sql_str = this_sql + ' ' + this_params_str
            result = await asyncio.get_running_loop().run_in_executor(thsql_executor(), self.do_exec, sql_str)

            if result:
                if self.have_session:
                    self.th_session.do_on_sql_done(self)
                    result = True

        except Exception as e:
            if _LOGGING_LEVEL:
                print(e)
            raise e

        if self.have_session:
            self.th_session.comments = None

        return result


    # def bind(self, *args, **kwargs):
    def bind(self, value, dbtype, param_name, output=False, null=False, max_length=-1):
        # def bind(self, object value, int dbtype, str param_name=None, int output=False, int null=False, int max_length=-1):
        this_result = None

        if value is None:
            null = True
        elif dbtype in (_mssql.SQLCHAR, _mssql.SQLVARCHAR, _mssql.SQLUUID):
            value = str(value)

        if param_name in self.parameter_list:
            this_param = self.parameter_list[param_name]
            this_param['value'] = value
            this_param['is_null'] = null

            if self._storedproc is not None:
                this_result = self._storedproc.bind(value, dbtype, param_name=param_name, output=output, null=null,
                                                    max_length=max_length)
        else:
            raise TheasServerError(
                'Error binding stored procedure param:  {} has no parameter named {}'.format(self.stored_proc_name,
                                                                                             param_name))

        return this_result

    @property
    def sql_conn(self):
        #return self._storedproc.connection
        return self.sql_conn

    @property
    def name(self):
        #return self._storedproc.name
        return self.stored_proc_name

    @property
    def parameters(self):
        #return self._storedproc.parameters
        return self.parameter_list
