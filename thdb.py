"""thdb.py -- database-platform-agnostic base classes for the Theas SQL layer.

Theas talks to its database through a small, well-defined surface (see
docs/thsql_call_sites.md): a ConnectionPool that hands out / recycles Conn
objects, and ThStoredProc for executing stored procedures.

The pool's bookkeeping -- the RLock-guarded juggling of the three connection
lists (available / in-use / to-release) -- is identical regardless of the
database driver.  That logic lives here, in the abstract _ConnectionPool and
_Conn base classes.  The driver-specific primitives (connect, init, reset, and
the low-level connection operations) are declared abstract here and implemented
in the platform modules:
    thsql.py    -- pymssql / MSSQL
    thpgsql.py  -- psycopg / PostgreSQL  (future)

At startup the chosen platform module is selected based on configuration; only
one is loaded.  Each platform module subclasses _ConnectionPool and _Conn and
provides the driver-specific overrides.

This module imports only thbase and the standard library -- it must NOT import a
platform module (thsql / thpgsql), to avoid an import cycle.
"""

import uuid
from threading import RLock

from thbase import log


class _Conn:
    """Driver-agnostic connection wrapper.

    Holds the pool bookkeeping state shared by every backend.  The three
    driver-specific primitives -- connected, cancel(), close() -- are declared
    here and implemented by the platform subclass (e.g. thsql.Conn).
    """

    def __init__(self, sql_conn, sql_settings, pool=None):
        self.sql_conn = sql_conn  # raw driver connection (pymssql / psycopg)
        self.name = "new"
        self.id = str(uuid.uuid4())
        self.is_public_authed = False
        self.is_user_authed = False
        self.last_error = None
        self.sql_settings = sql_settings
        self.pool = pool  # back-reference to the _ConnectionPool that owns this Conn

    def __del__(self):
        # Uniform teardown built on the driver-specific primitives below.
        try:
            if self.connected:
                self.cancel()
                self.close()
        except Exception:
            pass
        self.sql_conn = None

    # --- driver-specific primitives (implemented by the platform subclass) ---

    @property
    def connected(self):
        raise NotImplementedError

    def cancel(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


class _ConnectionPool:
    """Driver-agnostic connection pool.

    Implements the RLock-guarded bookkeeping over three lists:
        conns           -- available, reset, ready-to-use connections
        conns_inuse     -- connections currently checked out
        conns_torelease -- connections awaiting deferred reset (process_release_conns)

    The driver-specific steps -- new_conn(), init_conn(), reset_conn() and the
    _executor() accessor -- are abstract and supplied by the platform subclass.
    """

    def __init__(self, sql_settings):
        self.lock = RLock()
        self.sql_settings = sql_settings
        self.conns = []           # available connections
        self.conns_inuse = []
        self.conns_torelease = []

    def __del__(self):
        with self.lock:
            self.conns_torelease.clear()
            self.conns_inuse.clear()
            self.conns.clear()

    # --- abstract: driver-specific (implemented by the platform subclass) ---

    def _executor(self):
        # Return the module-level ThreadPoolExecutor used to run blocking driver
        # calls off the asyncio loop.
        raise NotImplementedError

    async def new_conn(self, skip_init=False, conn_name=""):
        raise NotImplementedError

    async def init_conn(self, conn):
        raise NotImplementedError

    async def reset_conn(self, conn):
        raise NotImplementedError

    # --- concrete: driver-agnostic pool bookkeeping ---

    def kill_threads(self, reason=''):
        log(None, 'SQL', 'Killing all executor threads in kill_threads() {}'.format(reason))
        executor = self._executor()
        if executor is not None:
            with self.lock:
                executor.shutdown(wait=False, cancel_futures=True)

    def snapshot(self, include_details=True):
        # Snapshot the pool's three lists under the lock, then release before
        # reading per-conn fields so we don't block other pool operations.
        with self.lock:
            conns_avail = list(self.conns)
            conns_inuse = list(self.conns_inuse)
            conns_torelease = list(self.conns_torelease)

        if not include_details:
            return {
                'conns': len(conns_avail),
                'conns_inuse': len(conns_inuse),
                'conns_torelease': len(conns_torelease),
            }

        def detail(conn, status):
            if conn is None:
                return {'id': None, 'status': status}
            try:
                return {
                    'id': conn.id,
                    'name': conn.name,
                    'status': status,
                    'connected': conn.connected,
                    'is_user_authed': conn.is_user_authed,
                    'is_public_authed': conn.is_public_authed,
                    'last_error': conn.last_error,
                }
            except Exception as e:
                return {'id': getattr(conn, 'id', None), 'status': status, 'error': str(e)}

        return {
            'conns': [detail(c, 'available') for c in conns_avail],
            'conns_inuse': [detail(c, 'inuse') for c in conns_inuse],
            'conns_torelease': [detail(c, 'torelease') for c in conns_torelease],
        }

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

        if conn is not None:
            conn.name = conn_name
            with self.lock:
                log(None, 'SQL',
                    'New connection added to the pool. len(conns)={}; len(conns_inuse={}; len(conns_torelease={})'.format(
                        len(self.conns), len(self.conns_inuse), len(self.conns_torelease)))
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
                conn.name = conn_name
                log(None, 'SQLConn', 'get_conn() is returning connection', conn_name, conn.id,
                    'len(conns)={}; len(conns_inuse={}; len(conns_torelease={})'.format(
                    len(self.conns), len(self.conns_inuse), len(self.conns_torelease)))
        if conn is None:

            if len(self.conns) + len(self.conns_inuse) >= self.sql_settings.max_conns:
                log(None, 'SQLConn', 'TOO MANY SQL CONNECTIONS per configured sql_max_connections ({})'.format(self.sql_settings.max_conns))

            else:
                conn = await self.add_conn(skip_init=skip_init, conn_name=conn_name)
                if conn is not None:
                    log(None, 'SqlConn', 'get_conn() is returning new SQL connection', conn_name, conn.id,
                        '. Remaining in pool: ', len(self.conns))

        return conn

    async def release_conn(self, conn):
        # Fast bookkeeping: move conn from conns_inuse to conns_torelease.
        # The actual SQL reset (theas.spactResetConnection) is deferred to
        # process_release_conns(), which runs from the async event loop's
        # periodic each_period() at a more convenient time.
        # async signature retained for caller compatibility; the body is sync.
        self.release_conn_sync(conn)

    def release_conn_sync(self, conn):
        # Sync variant of release_conn() with the same semantics.  Safe to
        # call from destructors, finished_sync, and other non-async paths.
        if conn is None:
            return

        with self.lock:
            for i, this_conn in enumerate(self.conns_inuse):
                if this_conn is conn:
                    self.conns_inuse.pop(i)
                    self.conns_torelease.append(conn)
                    log(None, 'SQL', 'release_conn: queued for deferred reset:', conn.id,
                        'len(conns)={}; len(conns_inuse)={}; len(conns_torelease)={}'.format(
                            len(self.conns), len(self.conns_inuse), len(self.conns_torelease)))
                    return
            log(None, 'SQL', 'release_conn: conn not found in conns_inuse:', conn.id)

    async def process_release_conns(self):
        # Drain conns_torelease.  For each conn, attempt reset_conn() and
        # re-pool on success.  Pop one at a time under the lock; release the
        # lock for the await on reset_conn so other pool operations can proceed
        # during the SQL round-trip.
        while True:
            with self.lock:
                if not self.conns_torelease:
                    return
                conn = self.conns_torelease.pop(0)
                log(None, 'Conn', 'process_release_conns: processing conn:', conn.id,
                    'remaining in queue:', len(self.conns_torelease))

            if conn is None:
                continue

            if await self.reset_conn(conn):
                with self.lock:
                    self.conns.append(conn)
                    log(None, 'SQL', 'process_release_conns: re-pooled conn:', conn.id)
            else:
                log(None, 'SQL', 'process_release_conns: dropped conn (reset failed):', conn.id)
                # reset_conn() invoked kill_conn() on failure paths, but kill_conn
                # only acts on conns_inuse membership.  At this point the conn was
                # already popped from conns_torelease, so close the underlying SQL
                # connection here to be sure.
                try:
                    if conn.connected:
                        conn.close()
                except Exception as e:
                    log(None, 'SQL', 'process_release_conns: error closing dead conn:', conn.id, repr(e))

    def kill_conn(self, conn):
        if conn is None:
            return

        with self.lock:
            for i, this_conn in enumerate(self.conns_inuse):
                if this_conn == conn:
                    self.conns_inuse.pop(i)
                    if this_conn.connected:
                        this_conn.close()

                    del this_conn

                    break