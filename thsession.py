from threading import RLock
import datetime
import uuid
import time
import asyncio


from thbase import log, G_server

from thcore import Theas
from thsql import call_auth_storedproc, call_logout_storedproc

#module-level constances, set by config_thsession()
_LOGGING_LEVEL = 1
_REMEMBER_USER_TOKEN = True
_SESSION_MAX_IDLE = 60  # Max idle time (in minutes) before TheasServer session is terminated
_SQL_TIMEOUT = 120
_LOGIN_RESOURCE_CODE = 'login'
_SERVER_PREFIX = 'localhost:8881'
_LOGIN_AUTO_USER_TOKEN = None
_REMOVE_EXPIRED_THREAD_SLEEP = 60

#aliases to the globals lists in TheasServer.py
G_sessions = None
G_conns = None
G_cached_resources = None

def config_thsession(
        gsess=G_sessions, # reference to global session list
        gconns=G_conns, # reference t global connection list
        gresources=G_cached_resources,
        remember_user_token=_REMEMBER_USER_TOKEN,
        session_max_idle=_SESSION_MAX_IDLE,
        sql_timeout=_SQL_TIMEOUT,
        login_resource_code=_LOGIN_RESOURCE_CODE,
        server_prefix = _SERVER_PREFIX,
        login_auto_user_token=_LOGIN_AUTO_USER_TOKEN,
        logging_level=_LOGGING_LEVEL,
        remove_expired_thread_sleep=_REMOVE_EXPIRED_THREAD_SLEEP
    ):

    global G_sessions
    G_sessions = gsess

    global G_conns
    G_conns = gconns

    global G_cached_resources
    G_cached_resources = gresources

    global _LOGGING_LEVEL
    _LOGGING_LEVEL = logging_level

    global _REMEMBER_USER_TOKEN
    _REMEMBER_USER_TOKEN = remember_user_token

    global _SESSION_MAX_IDLE
    _SESSION_MAX_IDLE = session_max_idle

    global _SQL_TIMEOUT
    _SQL_TIMEOUT = sql_timeout

    global _LOGIN_RESOURCE_CODE
    _LOGIN_RESOURCE_CODE = login_resource_code

    global _SERVER_PREFIX
    _SERVER_PREFIX = server_prefix

    global _LOGIN_AUTO_USER_TOKEN
    _LOGIN_AUTO_USER_TOKEN = login_auto_user_token

    global _REMOVE_EXPIRED_THREAD_SLEEP
    _REMOVE_EXPIRED_THREAD_SLEEP = remove_expired_thread_sleep


# -------------------------------------------------
# Global session list
# -------------------------------------------------

class ThSessions:
    """Class ThSessions is to manage a thread-safe global dictionary of active user sessions.

    It uses a mutex, and methods for locking and unlocking the global dictionary, as well as methods for
    creating, retrieving, and deleting sessions.

    It also provides support for a background thread that is responsible for automatically purging expired
    sessions.

    See class ThSession.  (ThSessions manages a dictionary of ThSession objects.)
    """

    def __init__(self):
        self.__sessions = {}
        self.waiting_for_busy = {}
        self.background_thread_running = False
        self.lock = RLock()

    def __del__(self):
        with self.lock:
            for this_session_token in self.__sessions:
                self.__sessions[this_session_token] = None
            self.__sessions.clear()

    def stop(self):
        self.background_thread_running = False

    def __len__(self):
        return len(self.__sessions)

    def remove_session(self, session_token):
        this_session = None
        with self.lock:
            if session_token in self.__sessions:
                this_session = self.__sessions[session_token]
                del self.__sessions[session_token]

        return this_session

    def remove_all_sessions(self):
        with self.lock:
            for session_token, this_sess in self.__sessions.items():
                if this_sess is not None and\
                        this_sess.conn is not None and\
                        this_sess.conn.sql_conn is not None and\
                        this_sess.conn.sql_conn.connected:
                    this_sess.sql_conn.close()

    async def remove_expired(self, remove_all=False):
        global G_program_options
        with self.lock:
            expireds = {}

            log(None, 'ExpiredSess', 'Checking for expired sessions.' 'Total sessions at start:', len(self.__sessions))

            for session_token in self.__sessions:
                this_session = self.__sessions[session_token]
                if (
                    remove_all or
                    this_session is None or
                    this_session.date_expire is None or
                    this_session.date_expire < datetime.datetime.now() or

                        (
                        _SQL_TIMEOUT > 0 and
                        this_session.date_sql_timeout is not None and
                        this_session.date_sql_timeout < datetime.datetime.now()
                        )
                            ):
                        expireds[session_token] = this_session

            for session_token in expireds:
                this_session = expireds[session_token]
                this_session.conn = None
                self.__sessions[session_token] = None
                del self.__sessions[session_token]
                if this_session is not None:
                    del this_session

            del expireds

            log(None, 'ExpiredSess', 'Done with expired sessions.' 'Total sessions at end:', len(self.__sessions))


    #@staticmethod
    #def log(category, *args, severity=10000):
    #    if _LOGGING_LEVEL == 1 or 0 > severity >= _LOGGING_LEVEL:
    #        print(datetime.datetime.now(), 'ThSessions [{}]'.format(category), *args)

    def retrieve_session(self, session_token=None, comments=''):
        this_sess = None
        with self.lock:
            if session_token and session_token in self.__sessions:
                # have existing session
                this_sess = self.__sessions[session_token]
                log(this_sess, 'Sessions', 'retrieve_session() retrieving existing session', this_sess.session_token, comments)

            else:
                this_sess = ThSession()
                self.__sessions[this_sess.session_token] = this_sess
                log(this_sess, 'Sessions', 'retrieve_session() creating new session', this_sess.session_token, comments)

        return this_sess

    def _poll_remove_expired(self):
        last_poll = datetime.datetime.now()

        while self.background_thread_running and G_server.is_running:
            # self.log('PollRemoveExpired', 'Running background_thread_running')
            if (datetime.datetime.now() - last_poll).total_seconds() > _REMOVE_EXPIRED_THREAD_SLEEP:
                last_poll = datetime.datetime.now()
                self.log('PollRemoveExpired', 'Sessions at start', len(self.__sessions))
                self.remove_expired()
                self.log('PollRemoveExpired', 'Sessions at end', len(self.__sessions))
            time.sleep(3)  # sleep only for 3 seconds so the application can shutdown cleanly when needed

    def start_cleanup_thread(self):
        pass
        #if _REMOVE_EXPIRED_THREAD_SLEEP:
        #    self.background_thread_running = True
        #    expire_thread = Thread(target=self._poll_remove_expired, name='ThSessions Cleanup')
        #    expire_thread.start()


# -------------------------------------------------
# ThSession
# -------------------------------------------------
class ThSession():
    """Class ThSession manages all aspects of an individual user session.

     Each session has a unique session_token, and is stored in a ThSessions object.

     Each session also has its own dedicated SQL connection, manages authentication (including rendering the
     login screen as needed), tracks elapsed time of individual requests, performs logging, provides locking
     to prevent multiple simultaneous requests for the same session, and provides methods for initializing
     a new session and for retrieving a session from the global ThSessions object.

     ThSession.get_session() currently tries to retrieve a session from the global ThSessions object.  In
     he future it might make sense to move this retrieval to a method of ThSessions()
    """

    def __init__(self):
        self.theas_page = None
        self.conn = None

        self.log_current_request = True
        self.current_handler = None
        self.comments = None

        self.session_token = None

        self.session_token = str(uuid.uuid4())
        self.__error_message = ''

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

        self.current_xsrf_form_html = None
        self.current_request = {}

        # username holds the username of the currently authenticated user, and will be updated by authenticate()
        self.username = None
        self.user_token = None

        # if set to true, upon successful authenticate the user's token will be saved to a cookie
        # for automatic login on future visits
        global _REMEMBER_USER_TOKEN
        self.remember_user_token = _REMEMBER_USER_TOKEN

        self.theas_page = Theas(theas_session=self)

    def __del__(self):
        if self.theas_page is not None:
            self.theas_page = None
            del self.theas_page

        if self.conn is not None:
            this_conn = self.conn
            self.conn = None

            global G_conns
            G_conns.release_conn_sync(this_conn)
    @property
    def current_resource(self):
        return self.__current_resource

    @property
    def resource_versions(self):
        # Return master resource_versions_dict from ThCachedResources to make this available in Theas filters
        return G_cached_resources.resource_versions_dict

    @current_resource.setter
    def current_resource(self, value):
        if value is not None and value.render_jinja_template:

            if self.__current_resource is None or \
                    (value.resource_code != self.__current_resource.resource_code and
                     not value.resource_code.endswith('.vue') and \
                     not value.resource_code.endswith('.js')):
                self.log('Resource', 'Current_resource changed to: {}  Was: {}'.format(value.resource_code,
                                                                                       self.__current_resource.resource_code if self.__current_resource else 'not set'))
                self.__current_resource = value
    @property
    def error_message(self):
        return self.__error_message

    @error_message.setter
    def error_message(self, value):
        self.__error_message = value
        self.theas_page.set_value('theas:th:ErrorMessage', self.__error_message)

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

    async def get_lock(self, handler=None, handler_guid=None):
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
                log(self, 'Session', 'LOCK obtained by handler ({})'.format(self.__locked_by))
        return result


    #@classmethod
    #def cls_log(cls, category, *args, severity=10000):
    #    if _LOGGING_LEVEL == 1 or 0 > severity >= _LOGGING_LEVEL:
    #        print(datetime.datetime.now(), 'ThSessions [' + category + ']:', *args)

    @classmethod
    async def get_session(cls, retrieve_from_db=False, inhibit_create=False,
                    comments=None, session_token=None, handler_guid=None):

        global G_sessions
        # Retrieve or create a session as needed.
        # See if requestor provided a session token (in cookie, URI, or form field).  If so, look up in global
        # list of sessions.  If no session token or session is not in list, create a new session.
        date_start = datetime.datetime.now()
        this_sess = None
        lock_succeeded = False  # indicates we received a lock
        failed_to_lock = False  # indicates we attempted a lock, but failed

        log(None, 'Session', 'get_session() was asked for session_token', session_token)

        # try to retrieve the session from the global list, or else create a new one
        this_sess = G_sessions.retrieve_session(session_token, comments=comments)

        if this_sess is None:
            cls.cls_log('Sessions', 'Failed to obtain a session with call to retrieve_session()')
        elif session_token != this_sess.session_token:
            log(this_sess, 'Session', 'Obtained NEW session', this_sess.session_token)
        else:
            log(this_sess, 'Session', 'Obtained EXISTING session', this_sess.session_token)

        if this_sess is not None:
            log(this_sess, 'Session', 'Attempting lock.', this_sess.session_token)

            give_up = False
            lock_succeeded = await this_sess.get_lock(handler_guid=handler_guid)

            give_up = False
            retry_count = 0

            start_waiting = time.time()
            seconds_to_wait = 30 #wakt up to 30 seconds for a lock

            while not lock_succeeded and not give_up:
                await asyncio.sleep(0.5)  #wait .5 seconds between retrries
                retry_count = retry_count + 1
                log(this_sess, 'Session', 'Session lock retry', retry_count)
                lock_succeeded = await this_sess.get_lock(handler_guid=handler_guid)

                if not lock_succeeded:
                    give_up = time.time() - start_waiting > seconds_to_wait

            if lock_succeeded:
                if this_sess.conn is None:
                    this_sess.conn = await G_conns.get_conn(conn_name=this_sess.session_token)
                    log(this_sess, 'Session', 'get_session obtained connection name:', this_sess.conn.name, 'id:', this_sess.conn.id)
            else:
                log(this_sess, 'Session', 'Could not lock session.', this_sess.session_token)
                failed_to_lock = True
                this_sess = None

        # we should now always have a session unless inhibit_create==True
        # assert this_sess is not None and this_sess.get_lock(handler=handler, no_log=True), 'Could not obtain session in ThSession.get_session'

        if this_sess is not None:
            this_sess.comments = comments
            this_sess.date_request_start = date_start
            this_sess.date_expire = datetime.datetime.now() + datetime.timedelta(minutes=_SESSION_MAX_IDLE)


        return this_sess, failed_to_lock

    def log(self, category, *args, severity=10000):
        if _LOGGING_LEVEL == 1 or 0 > severity >= _LOGGING_LEVEL:
            if self.log_current_request:
                # print(datetime.datetime.now(), 'ThSession [{}:{}] ({}) - {} ({})'.format(
                print(datetime.datetime.now(), 'ThSession [{}:{}] - {} ({})'.format(
                    self.session_token,
                    self.request_count,
                    # self.__locked_by,
                    category,
                    self.comments if self.comments is not None else '',
                ), *args)


    async def init_session(self, force_init=False):
        global G_program_options
        global G_sessions

        if force_init:
            self.conn = None

        if force_init or self.conn is None or\
                (self.conn is not None and self.conn.sql_conn is not None and not self.sql_conn.connected):
            self.initialized = False

        if (self.conn  is None or\
                self.conn.sql_conn is None or\
                not self.initialized):

            # Establish SQL connection, initialize
                self.conn = await G_conns.get_conn()

                log(None, 'Session', 'init_session obtained connection name:', self.conn.name, 'id:', self.conn.id)
                self.conn.name = 'initializing'
                log(None, 'Session', 'init_session set connection name to:', self.conn.name, 'id:', self.conn_id)

                self.initialized = False

                if self.conn is not None:
                    # Note:  we have created a new user session, but the user still needs to be authenticated
                       # make sure session has been initialized (to handle uploaded files, etc.)

                    if _LOGIN_AUTO_USER_TOKEN and not self.logged_in and not self.autologged_in:
                        self.log('Auth', 'Authenticating as AUTO user (i.e. public)')
                        try:
                            await self.authenticate(user_token=_LOGIN_AUTO_USER_TOKEN)
                            #async_as_sync(self.authenticate, user_token=_LOGIN_AUTO_USER_TOKEN)
                        except:
                            self.autologged_in = False

                        if not self.autologged_in:
                            self.log('Auth',
                                     'Error: Authentication as AUTO user (i.e. public) FAILED.  Is your config file wrong?')
                            self.log('Auth', 'Bad AUTO user token: {}'.format(_LOGIN_AUTO_USER_TOKEN))

                    self.initialized = True

        return self

    def finished_sync(self):
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

            if self.conn is None:
                self.log('Session', 'Destroying session')
                G_sessions.remove_session(self.session_token)
            else:
                self.log('Session', 'Will time out at', self.date_expire)

            self.log_current_request = True
            self.current_handler.cookies_changed = False

            if not self.logged_in and self.conn is not None:

                this_conn = self.conn
                self.conn = None

                global G_conns
                G_conns.release_conn_sync(this_conn)

            self.release_lock(handler=self.current_handler)

    async def finished(self):
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

            if self.conn is None:
                self.log('Session', 'Destroying session')
                G_sessions.remove_session(self.session_token)
            else:
                self.log('Session', 'Will time out at', self.date_expire)

            self.log_current_request = True
            self.current_handler.cookies_changed = False

            if not self.logged_in and self.conn is not None:
                this_conn = self.conn
                self.conn = None

                global G_conns
                await G_conns.release_conn(this_conn)

            self.release_lock(handler=self.current_handler)
    async def authenticate(self, username=None, password=None, user_token=None, retrieve_existing=False, conn=None):
        """
        :param username: Username of user.  If provided, provide password as well
        :param password: Password of user.  Provide if username is provided
        :param user_token: Token for user authentication.  May be provided INSTEAD of username and password
        :param retrieve_existing: Boolean flag.  If set, does not authenticate, but does retrieve existing session
        :return: logged_in (boolean), error_message (string)
        """

        self.logged_in = False
        result = False

        if self.current_handler is not None:
            if username is None and password is None and user_token is None and not retrieve_existing:
                # caller didn't specify username/password or user-token, so check for a form
                # post from the login page
                if 'u' in self.current_handler.request.arguments:
                    username = self.current_handler.get_argument('u')
                elif 'theas:Login:UserName' in self.current_handler.request.arguments:
                    username = self.current_handler.get_argument('theas:Login:UserName')

                if 'pw' in self.current_handler.request.arguments:
                    password = self.current_handler.get_argument('pw')
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

        resultset = await call_auth_storedproc(th_session=self, conn=conn,
                                               username=username, password=password,
                                               user_token=user_token, retrieve_existing=retrieve_existing
                                               )

        try:
            session_guid = None

            if resultset is None:
                self.logged_in = False
                self.user_token = None


                self.log('Session', 'Authentication failed:', self.error_message)
                self.error_message =  self.error_message + '|' + 'Invalid username or password.|1|Could Not Log In'
                #self.theas_page.set_value('theas:th:ErrorMessage', error_message)

            else:
                for row in resultset:
                    session_guid = row['SessionGUID']
                    user_token = row['UserToken']
                    username = row['UserName']

                if session_guid is not None:
                    if user_token == _LOGIN_AUTO_USER_TOKEN:
                        self.logged_in = False
                        self.autologged_in = True
                        self.log('Auth', 'Authenticated as AUTO (public)... not a real login')

                    else:
                        self.logged_in = True

                        # Store some user information (so the information can be accessed in templates)
                        self.username = username
                        self.user_token = user_token

                        self.theas_page.set_value('th:UserName', self.username)
                        self.theas_page.set_value('th:ST', self.session_token)


                        if self.current_data:
                            # update data for template (in case Authenticate() was called at the request
                            # of a resource's stored procedure just before rendering the page)
                            self.current_data['_Theas']['UserName'] = self.username
                            self.current_data['_Theas']['LoggedIn'] = '1' if self.logged_in else '0'

                        self.log('Auth', 'Authenticated as actual user {}'.format(self.username))

            proc = None
            del proc

        except Exception as e:
            self.logged_in = False
            self.user_token = None
            self.log('Session', 'Authentication failed:', e)
            self.error_message = repr(e) + '|' + 'Invalid username or password.|1|Could Not Log In'

        if self.current_handler:
            # If authentication was successful, we want to make sure the UserToken
            # cookie is set properly.  (If authentication was not successful,
            # we make no changes to the UserToken cookie.)
            self.current_handler.cookie_usertoken = None

            if self.logged_in and self.remember_user_token:
                self.current_handler.cookie_usertoken = self.user_token

            # always write the cookie...even if authentication failed (in which case we need to clear it)
            self.current_handler.write_cookies()

        return self.logged_in, self.error_message

    async def logout(self):
        self.log('Session', 'Logged out.')

        self.release_lock(handler=self.current_handler)

        self.logged_in = False

        if self.conn is not None and self.conn.sql_conn is not None and self.conn.sql_conn.connected:
            self.log('SQL', 'Closing SQL connection in ThSession.logout')
            await call_logout_storedproc(th_session=self, conn=self.conn)


    def clientside_redir(self, url=None, action='get'):
        # returns tiny html document to send to browser to cause the browser to post back to us
        if not url:
            if self.bookmark_url:
                url = self.bookmark_url
                self.bookmark_url = None
            elif self.current_resource and self.current_resource.resource_code:
                url = self.current_resource.resource_code
            else:
                url = '~'

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
        self.date_sql_timeout = datetime.datetime.now() + datetime.timedelta(seconds=_SQL_TIMEOUT)
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
        this_data['_Theas']['SessionToken'] = self.session_token

        this_data['_Theas']['UserName'] = self.username
        this_data['_Theas']['LoggedIn'] = '1' if self.logged_in else 0
        this_data['_Theas']['ErrorMessage'] = self.error_message

        if self.conn:
            this_data['_Theas']['ConnName'] = self.conn.name
            this_data['_Theas']['ConnID'] = self.conn.id

        if self.current_handler is not None:
            this_data['_Theas']['xsrf_token'] = self.current_handler.xsrf_token.decode('ascii')
            this_data['_Theas']['__handler_guid'] = self.current_handler.handler_guid

        this_data['_Theas']['theasServerPrefix'] = _SERVER_PREFIX

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

    async def build_login_screen(self):
        global G_cached_resources

        self.log('Response', 'Building login screen')

        buf = '<html><body>No data in build_login_screen</body></html>'

        resource = None
        template_str = ''

        self.log('Resource', 'Fetching login page resource')
        resource = await G_cached_resources.get_resource(_LOGIN_RESOURCE_CODE, self)

        if resource is None:
            # raise Exception ('Could not load login screen template from the database.  Empty template returned from call to theas.spgetSysWebResources.')
            buf = '<html><head><meta http-equiv="refresh" content="30"></meta><body>Could not load login screen template from the database server.  Empty template returned from call to theas.spgetSysWebResources.<br /><br />Will try again shortly... </body></html>'

        else:
            template_str = resource.data
            this_data = self.init_template_data()

            buf = self.theas_page.render(template_str, data=this_data)

        return buf

