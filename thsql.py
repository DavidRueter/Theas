from thbase import *
from threading import RLock
from pymssql import _mssql
import asyncio
import functools
import threading

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
                 database='somedatabase', appname = 'someapp', max_conns=10, sql_timeout=120,
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
        self.name = ""
        self.is_public_authed = False
        self.is_user_authed = False

    def __del___(self):
        if self.sql_conn is not None and self.sql_conn.connected:
            self.sql_conn.cancel()
            self.sql_conn.close()
            self.sql_conn=None
            del self.sql_conn

    @property
    def connected(self):
        return self.sql_conn is not None and self.sql_conn.connected

class ConnectionPool:
    mutex = RLock()

    def __init__(self, sql_settings=SQLSettings()):
        self.sql_settings = sql_settings
        self.conns = []
        self.conns_inuse = []

        global _LOGIN_AUTO_USER_TOKEN
        _LOGIN_AUTO_USER_TOKEN = sql_settings.login_auto_user_token


    def __del__(self):
        with self.mutex:
            for conn in self.conns:
                self.conns = None

            for conn in self.conns_inuse:
                self.conns = None


    async def init_connection(self, conn):
        # Initialize theas session:  stored proc returns SQL statements we need to execute
        proc = ThStoredProc('theas.spgetInitSession', None, conn=conn)
        if await proc.is_ok(skip_init=True):
            if '@ServerPrefix' in proc.parameter_list:
                #proc.bind(G_program_options.server_prefix, _mssql.SQLCHAR, '@ServerPrefix')
                proc.bind(self.sql_settings.http_server_prefix, _mssql.SQLCHAR, '@ServerPrefix')

            await proc.execute()

            for row in proc.resultset:
                sql_str = row['SQLToExecute']
                conn.sql_conn.execute_non_query(sql_str)

        await call_auth_storedproc(conn=conn)

        log(None, 'SQL', 'Connection initialized.  FreeTDS version: ' + str(conn.sql_conn.tds_version))


    async def new_conn(self, skip_init=False, conn_name=""):
        if self.sql_settings is None:
            raise TheasServerSQLError('Error: must provide sql_settings)')

        log(None, 'SQL', 'Creating new SQL connection')
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
        conn.is_public_authed = False
        conn.is_user_authed = False
        conn.name = conn_name

        if not skip_init:
            await self.init_connection(conn)

        return conn

    async def add_conn(self, conn=None, skip_init=False, conn_name=''):
        with self.mutex:
            if conn is None:
                conn = await self.new_conn(skip_init=skip_init, conn_name=conn_name)
            self.conns.append(conn)
        return conn

    async def get_conn(self, force_new=False, conn_name='no name', skip_init=False):
        conn = None

        with self.mutex:
            if len(self.conns) > 0 and not force_new:
                conn = self.conns.pop()
                self.conns_inuse.append(conn)
                log(None, 'SQLConn', 'get_conn() is returning connection', conn_name, 'from pool. Remaining in pool: ', len(self.conns))
                conn.name = conn_name
            else:
                #await asyncio.get_running_loop().run_in_executor(None, functools.partial(self.add_conn, skip_init=skip_init, conn_name=conn_name))
                conn = await self.add_conn(skip_init=skip_init)
                log(None, 'SqlConn', 'get_conn() is returning new SQL connection', conn_name, '. Remaining in pool: ', len(self.conns))

        return conn

    def release_conn(self, conn):
        with self.mutex:
            for i, this_conn in enumerate(self.conns_inuse):
                if this_conn == conn:
                    self.conns_inuse.pop(i)

                    if conn.is_user_authed or not conn.is_public_authed:
                        #connection must be reset before going back into the pool
                        self.init_connection(conn)

                    self.conns.append(conn)
                    break

# for convenience: a wrapper function to call the authentication stored proc
async def call_auth_storedproc(th_session=None, conn=None, username=None, password=None, user_token=None,
                               retrieve_existing=False):
    # authenticate user into database app
    result = None

    if conn is None and (th_session is None or th_session.conn is None):
        raise TheasServerError('Error:  call_auth_storedproc was called without a SQL connection')

    if username is None and user_token is None:
        user_token = _LOGIN_AUTO_USER_TOKEN

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

        result = await proc.execute()

        if conn is not None:
            conn.is_public_authed = (user_token == _LOGIN_AUTO_USER_TOKEN)
            if conn.is_public_authed and conn.is_user_authed:
                conn.is_user_authed = False
            else:
                conn.is_user_authed = True
    else:
        if conn:
            conn.is_public_authed = False
            conn.is_user_authed = False

        if th_session is not None:
            th_session.logged_in = False
            log(th_session, 'Session', 'Could not access SQL database server to attempt Authentication. (call_auth_storedproc)')
            th_session.error_message = 'Could not access SQL database server|Sorry, the server is not available right now|1|Cannot Log In'
        else:
            log(None, 'Session', 'Could not access SQL database server to attempt Authentication.')

    return result

async def call_logout_storedproc(th_session=None, conn=None):
    try:
        proc = ThStoredProc('theas.spdoLogout', th_session, conn=conn)
        if await proc.is_ok():
            proc.bind(th_session.session_token, _mssql.SQLVARCHAR, '@SessionToken')
            await proc.execute()
            # async_as_sync(proc.execute)
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
            log(self.th_session, 'StoredProc', 'has_connection: ',
                'True' if self.th_session.conn is not None else 'False')

        result = (self.conn is not None and self.conn.connected)

        if result and self.full_ok_checks:
            try:
                sql_str = 'SELECT 1 AS IsOK'
                await asyncio.get_running_loop().run_in_executor(None, self.conn.sql_conn.execute_non_query, sql_str)
            except:
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
                await asyncio.get_running_loop().run_in_executor(None, self.conn.sql_conn.execute_query, sql_str)

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

    async def execute(self):
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
            await asyncio.get_running_loop().run_in_executor(None, self.conn.sql_conn.execute_query, sql_str)

            this_resultset = [row for row in self.conn.sql_conn]
            self.resultsets.append(this_resultset)

            if len(self.resultsets) == 1:
                self.resultset = this_resultset

            have_next_resultset = self.conn.sql_conn.nextresult()
            while have_next_resultset:
                this_resultset = [row for row in self.conn.sql_conn]
                self.resultsets.append(this_resultset)
                have_next_resultset = self.conn.sql_conn.nextresult()

            if self.have_session:
                self.th_session.do_on_sql_done(self)

        except Exception as e:
            if _LOGGING_LEVEL:
                print(e)
            raise e

        if self.have_session:
            self.th_session.comments = None

        return self.resultset

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
