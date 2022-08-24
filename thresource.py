from threading import RLock
import json
from thbase import log

from thsql import ThStoredProc
from thbase import log

from pymssql import _mssql

LOGIN_RESOURCE_CODE = 'login'

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
    mutex = RLock()

    def __init__(self, default_path='somepath', static_file_version_no=1,
                 max_cache_item_size = 1024 * 1024 * 100,  # Only cache SysWebResources that are less than 100 Meg in size
                 max_cache_size = 1024 * 1024 * 1024 * 2,   # Use a maximum of 2 GB of cache)
                 conn_pool=None
                 ):
        self.__resources = {}
        self.__static_blocks_dict = {}
        self.__resource_versions_dict = {}
        self.default_path = default_path
        self.cache_bytes_used = 0
        self.static_file_version_no = static_file_version_no
        self.max_cache_item_size = max_cache_item_size
        self.max_cache_size = max_cache_size
        self.conn_pool=conn_pool

    def __del__(self):
        with self.mutex:
            for resource_code in self.__resources:

                this_resource = self.__resources[resource_code]
                if this_resource.data is not None:
                    self.cache_bytes_used = self.cache_bytes_used - len(this_resource.data)
                this_resource = None

                self.__resources[resource_code] = None

            self.__resources = None
            del self.__resources

            for resource_code in self.__static_blocks_dict:
                this_resource = self.__static_blocks_dict[resource_code]
                if this_resource.data is not None:
                    self.cache_bytes_used = self.cache_bytes_used - len(this_resource.data)
                this_resource = None

                self.__static_blocks_dict[resource_code] = None

            self.__static_blocks_dict = None
            del self.__static_blocks_dict

            self.__resource_versions_dict = None
            del self.__resource_versions_dict


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

        if resource_dict.data is None or\
                (len(resource_dict.data) < self.max_cache_item_size and self.cache_bytes_used < self.max_cache_size):
            with self.mutex:
                self.__resources[resource_code] = resource_dict
                if resource_dict.data is not None:
                    self.cache_bytes_used = self.cache_bytes_used + len(resource_dict.data)

    async def load_resource(self, resource_code, all_static_blocks=False,
                      from_filename=None, is_public=False, is_static=False, get_default_resource=False,
                      sql_conn=None):
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
                this_resource.revision = self.static_file_version_no # use Theas version

                self.add_resource(resource_code, this_resource)

            else:
                raise TheasServerError(
                    'Error due to request of file {} from the file system.  Server is configured to server resources only from the database.'.format(
                        from_filename))
        else:
            # load resource from database

            if all_static_blocks:
                log(None, 'Resource', 'Will load all static resources from the database.')
            else:
                if resource_code is None or resource_code == '~':
                    resource_code = '~'
                    log(None, 'Resource',
                                   'No resource_code specified.  Will load default resource for this session.')
                    get_default_resource = 1
                else:
                    log(None, 'Resource', 'ThCachedResources.load_resource fetching from database',
                                   resource_code if resource_code is not None else 'None')

            proc = None

            # Get SysWebResourcesdata from database
            proc = ThStoredProc('theas.spgetSysWebResources', None, sql_conn=sql_conn)

            if await proc.is_ok():

                # Note:  we could check for existence of @GetDefaultResource down below to help with backwards
                # compatibility ... but that would mean having to call refresh_parameter_list, which is
                # unnecessary overhead.

                proc.bind(resource_code, _mssql.SQLCHAR, '@ResourceCode', null=(resource_code is None))
                proc.bind(str(int(all_static_blocks)), _mssql.SQLCHAR, '@AllStaticBlocks')

                # if '@GetDefaultResource' in proc.parameter_list:
                proc.bind(str(int(get_default_resource)), _mssql.SQLCHAR, '@GetDefaultResource')

                await proc.execute()

                row_count = 0

                this_static_blocks_dict = {}

                if proc.resultset is not None:
                    for row in proc.resultset:
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

                        if this_resource.resource_code and not this_resource.resource_code in('~', '/', ''):
                            # added 2/11/2019:  don't want to cache default resource
                            self.add_resource(row['ResourceCode'], this_resource)

                        if all_static_blocks:
                            this_static_blocks_dict['//thInclude_' + row['ResourceCode']] = buf
                            this_static_blocks_dict['thInclude_' + row['ResourceCode']] = buf

                if 1 == 0 and resource_code and not resource_code  in ('~', '/', '')  and row_count == 0:
                    # do negative cache
                    # Negative caching disabled 5/23/2022 due to causing some problems related to:
                    #  UseSysWebResource, timing and PurgeCache, etc.
                    this_resource = ThResource()
                    this_resource.exists = False
                    self.add_resource(resource_code, this_resource)

                if all_static_blocks:
                    ThCachedResources.static_blocks_dict = this_static_blocks_dict

                    for rs in proc.resultsets[1:]:
                        for row in rs:
                            # note:  should only be one row
                            row_count += 1
                            buf = row['JSON_CurResourceRevisions']

                            new_dict = dict((v["ResourceCode"], v) for v in json.loads(buf))
                            ThCachedResources.resource_versions_dict = new_dict
                proc = None
                del proc

        return this_resource

    def delete_resource(self, resource_code=None, delete_all=False):
        result = False

        if delete_all and len(self.__resources) > 0:
            with self.mutex:
                self.__resources.clear()
                result = True

            self.load_global_resources()

        elif resource_code is not None and resource_code in self.__resources:
            with self.mutex:
                self.__resources[resource_code] = None
                del self.__resources[resource_code]
                result = True

        return result


    async def get_resource(self, resource_code, th_session, all_static_blocks=False, from_filename=None,
                           is_public=False, is_static=False, get_default_resource=False,
                           sql_conn=None):
        global DEFAULT_RESOURCE_CODE

        this_resource = None

        if resource_code:
            resource_code = resource_code.strip()
        else:
            if th_session is not None:
                resource_code = th_session.bookmark_url

        if resource_code == '':
            resource_code = None
        elif resource_code in ('~', '__th', '/'):
                resource_code = '~'
                get_default_resource = True

        if resource_code is not None and resource_code in self.__resources:
            # Cached resource
            this_resource = self.__resources[resource_code]
            log(th_session, 'Resource', 'Serving from cache', resource_code)

        else:
            # Load resource

            created_sql_conn = False

            # Comment out the following to obtain a SQL connection for fetching the resource
            # ...even if the session alreadyhas a different connection.
            # (This lets us fetch multiple resources concurrently.)
            if th_session and th_session.sql_conn:
                sql_conn = th_session.sql_conn

            if not from_filename and not sql_conn:
                sql_conn = await self.conn_pool.get_conn(conn_name='get_resource()')

                created_sql_conn = True


            this_resource = await self.load_resource(resource_code,
                                               all_static_blocks=all_static_blocks,
                                               get_default_resource=get_default_resource,
                                               from_filename=from_filename,
                                               sql_conn=sql_conn)

            if created_sql_conn and sql_conn and sql_conn.connected:
                #sql_conn.close()
                self.conn_pool.release_conn(sql_conn)
                sql_conn = None


        # Careful:  we could be getting a cached resource in which case there may not yet be a session, in which
        # case we can't update current_resource here!  It is up to the caller to update current_resource
        if th_session is not None and this_resource is not None and this_resource.exists and \
                this_resource.resource_code != LOGIN_RESOURCE_CODE and \
                this_resource.render_jinja_template:
            # we are assuming that only a jinja template page will have a stored procedure / can serve
            # as the current resource for a session.  (We don't want javascript files and the like
            # to be recorded as the current resource.)
            th_session.current_resource = this_resource
            th_session.theas_page.set_value('th:CurrentPage', this_resource.resource_code)

        return this_resource

    async def load_global_resources(self, sql_conn=None):
        await self.get_resource('Theas.js', None, from_filename=self.default_path + 'Theas.js', is_public=True)
        await self.get_resource(None, None, all_static_blocks=True, sql_conn=sql_conn)
