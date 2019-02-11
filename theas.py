#!usr/bin/python
# Filename: theas.py
# -*- coding: utf-8 -*-
version = '0.78'

'''Theas complements the Jinja2 template engine by providing persistent server-side state management,
data binding, and event handling.

The name "Jinja" was selected because it is the name for a Japanese temple.  "Theas" is the Greek word for
goddess...as in "the goddess of the temple".  Theas controls the way a template can be rendered from, and bound
to, server-side "components'.

Often times a Jinja2 template may be used to render an HTML form.  Theas provides multiple means of
creating and updating a server-side "control" (TheasControl) that stores the value and other attributes of
HTML form fields.

These "controls" can then be automatically updated by Theas on an HTTP POST.  Also, the "controls" can
be updated in server-side code, and the updated controls will automatically be rendered the next time the
template is rendered.

The main class provided is Theas.  This is provided to represent a "page" that is sent to a browser.  It
manages a list of TheasControls, provides a number of custom Jinja filters, a wrapper around Jinja to provide a
.render method (optional, for convenience), a method to parse HTTP post arguments and update the TheasControls,
and several helper methods.

TheasControls may be added in any of the following ways:

1) Passively, with no explicit declaration.  In this mode, TheasControls will only be created AFTER an HTTP post,
but can then be used on future renders of the template.  Benefits:  no explicit definition of controls--only
standard HTTP form element in the template in the normal way.  Creates server-side in-memory copy of the control
values that were posted, allows server-side modification of the values, and will automatically render the updated
controls the next time the template is rendered.

2) Explicitly in Python code.  In this mode, you may which to declare a class to handle server-side processing
of a page.  Benefits:  no need for explicit form field definition in the Jinja template at all.  Controls can
be automatically injected into the template via a custom filter.

3) Explicitly in Jinja template.  In this mode, like #1, controls are defined in the template.  However instead
of explicitly including standard HTML form field tags, you can use custom filters to more succinctly define
the controls.  This also results in the controls being defined in server-side memory PRIOR to the response
being sent to the client--which is useful in certain situations.


Similarly, Theas supports a number of different ways of implementing server-side event handlers:

1) Statically, in Python code.
2) Dynamically, within the Jinja2 template.
3) Dynamically, within some other data repository.

NOTE:  dynamic definition of event handlers can be very handy, but does pose a potential security risk if the
event handler source comes from "untrusted" outside sources.  By default, a constant ALLOW_UNSAFE_FUNCTIONS = False
is defined to disable this capability.

If dynamic source (such as from Jinja templates) is properly managed and comes from only trusted sources,
ALLOW_UNSAFE_FUNCTIONS may be safely enabled.

If enabled, you can do things like:

a) Define custom Jinja filters via Python source embedded within a Jinja filter itself.  Note that really the
filter definition source is placed within a SEPARATE template file that is rendered first.  Then filters defined
in that template file can be used directly from a template file that is used for the actual rendering of the
HTML output.

b) Define custom Jinja filters in a database that loads them at run-time.

c) Define custom Theas methods that can be called when a template is rendered, when an HTTP form post is
received, or when an HTTP AJAX request is received.  These can be defined in static Python source, or dynamically--
either embedded in a Jinja template, or in a string stored in a database or some other location.

See https://github.com/mitsuhiko/jinja2 and http://jinja.pocoo.org for more information on Jinja2


this_data['_Theas']['theasParams'

Within your template, you can access data from the resultsets like this:
        {{ data.Employer.JobTitle }}
        {{ data.Employer.Company }}
        {{ data.Employer.WorkplaceLocation }}


You can add form fields containing Theas control values to your page by including filters like this in your template:
        {{ data.EmployerJob.JobTitle|theasHidden(name="EmployerJob:JobTitle") }}
        {{ data.EmployerJob.Company|theasHidden(name="EmployerJob:Company") }}
        {{ data.EmployerJob.WorkplaceLocation|theasHidden(name="EmployerJob:WorkplaceLocation") }}

Within your template, you can also access access the values of Theas controls like this:
    {{ data._Theas.theasParams["theas:nextPage"] }}

    You could also use theasParams to access data values, such as:

    {{ data._Theas.theasParams["EmployerJob:JobTitle"] }}

    ...but this would return the value of the session.theas_page.control, which a) would not exist unless the control
    had been previously created by a filter or by Python code prior to the start of template rendering.


Originally Theas was created to support server-side rendering.  The above examples show how theas control values
can be embedded in an HTML form and/or outputted in HTML.

Vue.js is an exciting client-side javascript framework.  When using Vue.js the need for server-side rendering is
diminished.  For example, when outputting a list in HTML based on contents of a dataset, instead of rendering
the data into HTML at the server, it is preferable to send the data down to the browser as JSON and let Vue.js
render the list at the client.

Similarly for form fields, with Vue.js it is preferable to bind HTML form fields to Javascript variables (that
Vue will automatically manage), and then have Vue submit JSON via an Async call instead of performing an
HTTP POST of the actual Theas form.

Theas is set to support both server-side rendering, and client-side rendering.  Which you use is up to you.
'''
import types
import string
from collections import OrderedDict
import ast
import uuid
import urllib.parse as urlparse
import html
import json
import base64

from time import struct_time, strptime, strftime
import datetime

from jinja2 import Template, Undefined, environmentfilter  # , Markup, escape
from jinja2.environment import Environment

ALLOW_UNSAFE_FUNCTIONS = False


def format_str_if(this_str, fmt_str):
    buf = ''

    if this_str:
        this_str = this_str
        buf = fmt_str.format(this_str)
    return buf


# -----Jinja2 undefined variable class-----
class SilentUndefined(Undefined):
    def _fail_with_undefined_error(self, *args, **kwargs):
        return ''


class TheasException(Exception):
    pass


class TheasControl:
    def __init__(self):
        # self.control_nv = None
        self.id = None
        # self.authenticator = None
        self.checked = ''
        self.value = ''
        self.caption = ''
        self.attribs = OrderedDict()

        # def __del__(self):
        # self.control_nv = None


noneTheasControl = TheasControl()


class TheasControlNV:
    def __init__(self, name='', control_type=None):
        self.name = name
        # Name of the name/value pair (i.e. the HTML "name" attribute of an input control, etc.)
        self.controls = OrderedDict()
        # List of controls that share this name.  Radio buttons and checkboxes can have multiple
        # controls.  Inputs and TextAreas can have only one control.  Selects are a special case
        # because we use self.control to store the list of HTML <Options> (name/value pairs of
        # every option in the dropdown)...but these aren't real controls because they do not accept
        # HTML attributes such as class, style, etc.
        self.control = None
        # Needed for select
        self.__datavalue = ''
        # For internal use (to aid in setting the value of this name-value pair when the value must
        # correspond to a child control, such as radio, checkbox, or select
        self.value = ''
        # The current value, i.e. what jquery .val() would return for this name
        self.control_type = control_type
        # Type of control:  'hidden', 'text', 'password', 'radio', 'checkbox', 'select', 'textarea', etc.
        self.include_in_json = False
        # if set, this control will be included in the filter TheasValues
        self.name_prefix = 'theas:'
        # may contain 'Theas:' for controls saved in Theas Params, or may be empty for ad-hoc controls

    def __del__(self):
        # for ctrl_value in self.controls:
        #    self.controls[ctrl_value] = None

        self.controls = None
        del self.controls

    @property
    def datavalue(self):
        return self.__datavalue

    @datavalue.setter
    def datavalue(self, datavalue):
        self.__datavalue = datavalue

        if self.control_type in ('radio', 'checkbox', 'select'):
            for temp_ctrlvalue, temp_ctrl in self.controls.items():
                if temp_ctrl is not None:
                    temp_ctrl.checked = (str(self.__datavalue) == str(temp_ctrl.value))
                    if temp_ctrl.checked:
                        self.value = temp_ctrl.value
        else:
            self.value = self.datavalue


class Theas:
    def __init__(self, theas_session=None, jinja_environment=None):

        # if not isinstance(jinja_environment, Environment):
        if True:
            # set up new jinja environment
            self.jinja_env = Environment()

            self.jinja_env.theas_page = self

            self.jinja_env.undefined = SilentUndefined

            self.jinja_env.filters['theasSessionToken'] = self.theas_sessiontoken
            # Ouputs the current session token as a hidden form field.  This is required for normal
            # operation of Theas.  Also outputs other commonly-used Theas hidden form fields:
            # th:ErrorMessage and th:PerformUpdate

            self.jinja_env.filters['theasXSRF'] = self.theas_xsrf
            # Outputs the current XSRF token (used for security purposes).  This is required for
            # normal operation of Theas.  (Form posts to Theas that do not have a valid XSRF
            # token will be rejected.)

            # The following output an HTML form field for the specified control.
            # This is useful when producing HTML pages using server-side rendering (SSR).
            # Note that when using Vue.js these may not be needed, as theas data is communicated
            # via JSON instead. (See theasValuesjSON)
            self.jinja_env.filters['theasHidden'] = self.theas_hidden
            self.jinja_env.filters['theasInput'] = self.theas_input
            self.jinja_env.filters['theasRadio'] = self.theas_radio
            self.jinja_env.filters['theasSelect'] = self.theas_select
            self.jinja_env.filters['theasTextarea'] = self.theas_textarea
            self.jinja_env.filters['theasCheckbox'] = self.theas_checkbox

            self.jinja_env.filters['theasValuesJSON'] = self.theas_values_json
            # Output a JSON string that includes all Theas controls that have the flag include_in_json set.
            # The : character that is used as a delimiter in control names will be replaced with a $
            # so that the resulting JSON contains legal javascript variable names.

            self.jinja_env.filters['theasBase64'] = self.theas_base64

            self.jinja_env.filters['theasInclude'] = self.theas_include
            # Set the internal include_in_json flag for the specified control so that it will be
            # included in the output of the filter |theasValueJSON

            # By default this filter will not output anything (but instead merely affects the output of
            # }theasValueJSON

            # Optionally, you can pass in (output=True) to have this filter output the javascript-friendly
            # version of the control name as a string as well, in which embedded : characters are translated
            # to $ characters, such as:
            # {{ "theas:Ping:AudioRecording"|theasInclude(output=True) }} would result in
            # the string theas$Ping$AudioRecording being outputted.

            self.jinja_env.filters['theasResource'] = self.theas_resource
            # Lets you specify {{ SomeResource|theasResource }} instead of "SomeResource", and
            # thereby modifies the actual resource URL that is rendered to bust the browser
            # cache if needed.

            self.jinja_env.filters['theasEcho'] = self.theas_echo
            # Conditionally echos the specified string.  For example:
            # {{'active' | theasEcho(if_curpage='mypage')}} would output the string 'active' if
            # the value of the Theas control named curpage was equal to 'mypage'

            self.jinja_env.filters['friendlydate'] = self.format_friendlydate
            # General date formatting routine.

            # self.jinja_env.filters['button'] = self.theas_button

            self.jinja_env.filters['theasDefineFunctions'] = self.theas_define_functions
            self.jinja_env.filters['theasDefineFilter'] = self.theas_define_filter

        else:
            # reuses existing jinja environment
            self.jinja_env = jinja_environment

        self.th_session = theas_session
        self.control_names = {}

        self.set_value('th:SessionToken', str(self.th_session.session_token), include_in_json=False)
        self.set_value('th:ErrorMessage', '')
        self.set_value('th:CurrentPage',
                       self.th_session.current_resource.resource_code if self.th_session.current_resource is not None else '')
        self.set_value('th:PerformUpdate', '0')

        self.functions = {}
        self.authenicator = None

        self.doOnInit = []

        self.doOnBeforeProcessRequest = []
        self.doOnAfterProcessRequest = []

        self.doOnBeforeRender = []
        self.doOnAfterRender = []

        self.doOnFilter = []
        self.doOnAsync = []
        self.doOnError = []

    def __del__(self):
        for control_name in self.control_names:
            self.control_names[control_name] = None

        self.control_names = None
        del self.control_names

        self.jinja_env.theas_page = None

        self.jinja_env = None
        del self.jinja_env

    @classmethod
    def mimetype_for_extension(cls, filename):
        '''
        :param filename:
        :return: string:

        Note:  at present this simply looks up the filename extension.
        This function could be expanded to look at file contents to determine the type from data signatures
        if desired.  See:  https://en.wikipedia.org/wiki/List_of_file_signatures
        '''
        result = 'text/html'

        fn = None

        if filename:
            fn, sep, extension = filename.rpartition('.')
        if fn:
            global MIME_TYPE_EXTENSIONS
            if extension:
                extension = '.' + extension.lower()

                if extension in MIME_TYPE_EXTENSIONS:
                    result = MIME_TYPE_EXTENSIONS[extension]

                    # We don't really know what the character encoding is, so in general we
                    # don't specify.  But for certain types, we want to set the encoding type here.
                    if result in ('application/javascript', 'text/html'):
                        result += '; charset=utf-8'

        return result

    # ------------Jinja filter functions-------------
    def format_friendlydate(self, value, pre='', post='', formatstr='%a %m/%d/%y', stripleading='', informatstr=None):
        '''

        :param value: value passed by Jina--which will probably be a datetime.datetime or datetime.time...but
                      may be a str
        :param pre: characters to prepend to the result
        :param post: characters to append to the result
        :param formatstr: format string to use when formatting output
        ;param stripleading: format string to use when parsing input to a datetime or time type
        :return: a formatted string

        Default format is DOW MM/DD/YY as per strftime('%a %m/%d/%y')

        See:  http://strftime.org and https://www.craig-wright.com/2016/03/18/admin
        for cheatsheet

        It seems Jinja tries to present the contents of value as type datetime or time.
        But for some reason, probably due to locale-specific settings, sometimes the same code and format string
        will be presented as time in one environment, and as str in another.

        (Specifically, running on Windows 10 under the Python 3.4 interpreter in the PyCharm debugger, something
        like 18:00:00.00000000 is presented as time, but the same code and data running under the Python 3.4
        interpreter packaged with Py2Exe and running on Windows Server 2012 is presented as str.)

        Whatever the reasons and details, we need to go to some extra lengths here to determine if value is of
        type str, and if so, try to parse it out ourselves.  Our parsing efforts are not exhaustive, but
        does handle the simple case of trying to convert a value like '18:00:00.0000000'--which is how MSSQL
        returns a time column.

        The caller is free to provide parameter informatstr which is to contain a format string to be used
        for parsing the string.  If not provided, we default to making some hard-coded assumptions (based on
        what MSSQL will generally return).

        '''

        s = ''

        if isinstance(value, str):
            # Hmm...Jinja wasn't able to present value as a datetime or time type.  See if we can parse it
            try:
                if informatstr is None and value.index(':'):
                    # guess that this is a time
                    informatstr = '%H:%M:%S'

                    if value.index('.'):
                        # guess that this supposed time value has a decimal portion of seconds that must be discarded
                        value = value.split('.')[0]

                elif value.index('/'):
                    # guess that this is a date
                    informatstr = '%m/%d/%y'

                value = strptime(value, informatstr)  # value is now of type struct_time
                s = strftime(formatstr, value)
            except:
                # No, we were unable to parse the string.
                # The original value will simply be cast to string and returned
                s = ''

        if isinstance(value, datetime.datetime) or isinstance(value, datetime.time):
            s = value.strftime(formatstr)

        if s:
            if stripleading:
                # strip specified leading characters to partially work around lack of support for %-m and &-I
                s = s.strip(stripleading)

            if value and pre:
                s = pre + s
            if value and post:
                s += post

        else:
            s = str(value)

        return s

    def get_control(self, ctrl_name, control_type=None, id=None, auto_create=True, include_in_json=True, **kwargs):
        # NOTE:  pass in datavalue='xxx' to set the value of the control.
        # Does NOT need to be URL-encoded:  we do that here.

        # If control is a radio, checkbox or select, you can pass in value='yyy' which does
        # not set the value of the control but merely defines the value that will be used if the
        # control element ins checked.

        # For other types, it is an error to pass in value='yyy'

        # HTML id attributes are unique.  HTML name attributes are not necessarily unique.
        # For example, in the case of radio buttons, multiple elements (for each individual button)
        # may share the same name (where the name pertains to the group of radio buttons)

        # Theas both renders HTML and binds data values to HTML elements.  In other words, Theas
        # needs an entry in controls[] for each element (such as each individual radio button), and
        # so  Theas needs to store elements by id (not by name).  However, if for some reason an element
        # does not have an ID, the element may be stored by name.

        # Additionally, at times Theas needs to support retrieving a value by name--even if the controls
        # are stored by id.  For example, two radio buttons may share the same name.  We may need to
        # ask Theas what the value of the name is.  In this case we need to find all the elements
        # for the name, but then look only for the element that is checked.  To do this, call
        # get_control() and provide value_for_name=True.

        this_ctrl = None
        this_ctrl_nv = None
        value_changed = False

        save_param = True
        if 'persist' in kwargs:
            if kwargs['persist'] in ('0', 'false', 'False', 'no'):
                save_param = False
        # Note: in the future we may implement other values for persit (session, user, page, pageview, etc.)

        if ctrl_name:
            # The HTML <input> names begin with theas:, but in Python and elsewhere
            # we omit this prefix.
            if ctrl_name.startswith('theas:'):
                ctrl_name = ctrl_name[6:]

            if ctrl_name in (self.control_names):
                # look up exisitng control by name
                this_ctrl_nv = self.control_names[ctrl_name]

            if this_ctrl_nv is None:
                if not control_type:
                    control_type = 'hidden'

                if auto_create:
                    this_ctrl_nv = TheasControlNV(name=ctrl_name, control_type=control_type)
                    if save_param:
                        self.control_names[ctrl_name] = this_ctrl_nv
                    else:
                        this_ctrl_nv.name_prefix = ''
            else:
                # existing control, but may have been auto-created as a hidden...but now we have a more specific
                # type
                if control_type != 'hidden':
                    this_ctrl_nv.control_type = control_type

            if this_ctrl_nv is not None and include_in_json:
                this_ctrl_nv.include_in_json = True

            value_param = None
            if 'value' in kwargs:
                if this_ctrl_nv.control_type in ('radio', 'checkbox'):
                    value_param = kwargs['value']

                    if value_param in this_ctrl_nv.controls:
                        this_ctrl = this_ctrl_nv.controls[value_param]
                else:
                    raise Exception(
                        'Error in jinja_theas.py Theas.get_control:  Did you mean datavalue=xxx? You may not pass in parameter value=xxx unless the control type is a radio or a checkbox.  (control_type={})'.format(
                            this_ctrl_nv.control_type))

            have_datavalue_param = False
            datavalue_param = ''
            if 'datavalue' in kwargs:
                have_datavalue_param = True
                datavalue_param = kwargs['datavalue']
                if isinstance(datavalue_param, (str, bytes, bytearray)):
                    urlparse.quote(datavalue_param)

                if ctrl_name == 'th:ErrorMessage':
                    self.th_session.log('Theas', 'th:ErrorMessage value set={}'.format(datavalue_param))

            if this_ctrl_nv is not None:
                this_ctrl = this_ctrl_nv.control

                if this_ctrl is None and this_ctrl_nv.control_type not in ('radio', 'select') and len(
                        this_ctrl_nv.controls) == 1:
                    this_ctrl = this_ctrl_nv.controls[list(this_ctrl_nv.controls.keys())[0]]
                    if this_ctrl is not None and value_param != this_ctrl.value:
                        this_ctrl = None

                if this_ctrl is None:
                    if this_ctrl_nv.control_type != 'select' and (value_param or len(this_ctrl_nv.controls) == 0):
                        # We must create a new control.  Value and other attributes will be set below.
                        this_ctrl = TheasControl()
                        # this_ctrl.control_nv = this_ctrl_nv

                        this_ctrl.value = value_param
                        this_ctrl_nv.controls[value_param] = this_ctrl
                        value_changed = True
                    elif this_ctrl_nv.control_type == 'select':
                        # Special case:  we can't use .controls because that will contain a list
                        # of option name/values.  So we use self.control instead--because we still
                        # need a place to store the <select> control's attributes.
                        # Arguably could be used for other singleton controls, such as hidden and input,
                        # but these work fine using .controls[0]
                        # In other words, as of 9/14/2016, .control is used only for select
                        this_ctrl = TheasControl()

                if this_ctrl_nv.control_type == 'select' and ('options_dict' in kwargs or 'source_list' in kwargs):
                    this_ctrl_nv.controls.clear()

                this_options_dict = this_ctrl_nv.controls

                this_attribs = {}

                for this_key, this_paramvalue in kwargs.items():

                    if this_key == 'options_dict':
                        this_options_dict = kwargs[this_key]

                    elif this_key in ('name', 'value', 'datavalue', 'source_list', 'source_value', 'source_label',
                                      'escaping', 'persist'):
                        # this kwarg does not apply or has already been handled
                        pass

                    else:
                        # HTML attributes that have a - are a problem, because this is not a valid character for a
                        # Python identifier.  In particular, the HTML5 data-xxx="yyy" tag is a problem.
                        # It is up to the user to replace - with _ in attribute names, however Theas does
                        # treat data_ as data- internally
                        if this_key.startswith('data_'):
                            this_key = this_key.replace('data_', 'data-')
                        elif this_key.lower() == 'class' and this_key != 'class':
                            # force class key to lowercase
                            this_key = 'class'
                        elif this_key.lower() == 'style' and this_key != 'style':
                            # force class key to lowercase
                            this_key = 'style'
                        this_attribs[this_key] = this_paramvalue

                if this_ctrl_nv.control_type != 'hidden':
                    # add in _thControl CSS class
                    class_str = this_attribs.get('class', '')
                    if class_str:
                        class_str += ' '
                    class_str += '_thControl'
                    this_attribs['class'] = class_str

                    # add in visibility:hidden
                    # theas.js will take care of $('._thControl').css('visibility', 'visible') when the page is ready
                    style_str = this_attribs.get('style', '')

                    if style_str.find('visibility:') == -1:
                        if style_str:
                            style_str += ' '
                        style_str += 'visibility:hidden'
                        this_attribs['style'] = style_str

                if this_ctrl_nv.control_type == 'select' and this_options_dict is not None:
                    # create pseudo control for each select option
                    for this_opt, this_caption in this_options_dict.items():
                        if this_opt not in this_ctrl_nv.controls:
                            temp_ctrl = TheasControl()
                            # temp_ctrl.control_nv = this_ctrl_nv
                            temp_ctrl.value = this_opt
                            temp_ctrl.caption = this_caption

                            this_ctrl_nv.controls[this_opt] = temp_ctrl

                if have_datavalue_param and datavalue_param != '__th':
                    if this_ctrl_nv.datavalue != datavalue_param:
                        value_changed = True
                    # We want to go ahead and assign this value even if we don't think it has changed, because the
                    # datavalue property setter will set .checked which will not yet be set in the case of a new
                    # auto-created control.
                    this_ctrl_nv.datavalue = datavalue_param

                if this_ctrl is None:
                    this_ctrl = noneTheasControl
                    # else:
                    # this_ctrl.authenticator = self.authenicator  # record which authenticator created the control

                if this_ctrl is not None:
                    this_ctrl.attribs = this_attribs
                    if id:
                        this_ctrl.id = id

                if control_type is not None and control_type != this_ctrl_nv.control_type and (
                    not this_ctrl_nv.control_type or this_ctrl_nv.control_type == 'hidden'):
                    # Even on an existing control we want to update control_type if it is provided, because
                    # the control could have been created from TheasParams from a stored procedure and defaulted
                    # to hidden...but now a filter or something else is specifying the "real" type.
                    this_ctrl_nv.control_type = control_type

        return this_ctrl_nv, this_ctrl, value_changed

    def get_controls(self, include_in_json_only=False):
        # return dictionary of control name-value pairs
        this_result = {}
        for this_ctrl_name, this_nv in self.control_names.items():
            if include_in_json_only:
                if this_nv.include_in_json:
                    this_ctrl_name = this_ctrl_name.replace(':', '$')
                    this_result[this_ctrl_name] = str(this_nv.value)
            else:
                this_result[this_ctrl_name] = str(this_nv.value)

        return this_result

    def get_value(self, ctrl_name, auto_create=False):
        this_result = None

        this_ctrl_nv, this_ctrl, value_changed = self.get_control(ctrl_name, auto_create=auto_create)

        if this_ctrl_nv is not None:
            this_result = this_ctrl_nv.value

        return this_result

    def set_value(self, ctrl_name, new_value, include_in_json=True):
        this_ctrl_nv, this_ctrl, value_changed = self.get_control(ctrl_name, datavalue=new_value, include_in_json=include_in_json)

        this_result = None
        if this_ctrl_nv is not None:
            this_result = this_ctrl_nv.value

        return this_result, value_changed

    def process_client_request(self, request_handler=None, accept_any=False, buf=None, escaping='default',
                               from_stored_proc=False, *args, **kwargs):
        # handle updating theas_page controls
        # ('Theas: process_client_request starting')

        perform_processing = True

        self.th_session.log('Theas', 'Updating Theas controls')

        if len(self.doOnBeforeProcessRequest):
            for this_func in self.doOnBeforeProcessRequest:
                perform_processing = this_func(self, request_handler=None, accept_any=accept_any)

        changed_controls = []

        if buf and buf.index('=') > 0:
            # process from a string buf (typically returned by a stored procedure)

            while buf.endswith('&'):
                buf = buf[:-1]
            for this_nv in buf.split('&'):
                if this_nv and this_nv.index('=') > 0:
                    this_name, v = this_nv.split('=')
                    theas_name = this_name

                    # NOTE:  default for processing TheasParams from SQL stored procedures
                    if v:
                        if escaping == 'default' or escaping == 'urlencode':
                            v = urlparse.unquote(v)
                        elif escaping == 'htmlentities':
                            v = html.unescape(v)

                    # The HTML form input names begin with theas:, but in Python and elsewhere
                    # we omit this prefix.
                    if theas_name.startswith('theas:'):
                        theas_name = theas_name[6:]

                    if theas_name != 'th:LoggedIn' or from_stored_proc:
                        this_ctrl_nv, this_ctrl, value_changed = self.get_control(theas_name,
                                                                                  datavalue=v,
                                                                                  auto_create=True)
                        if value_changed:
                            changed_controls.append(this_ctrl_nv)

        else:
            if perform_processing and request_handler and request_handler.request.arguments:
                # process arguments from HTTP request

                for this_name, this_value in request_handler.request.arguments.items():
                    theas_name = this_name

                    if theas_name.startswith('theas:'):
                        theas_name = theas_name[6:]

                        this_value_str = this_value[0].decode('utf-8')

                        # NOTE:  default for processing HTTML forms
                        if this_value_str:
                            if escaping == 'default' or escaping == 'urlencode':
                                this_value_str = urlparse.unquote(this_value_str)
                            elif escaping == 'htmlentities':
                                this_value_str = html.unescape(this_value_str)

                        this_ctrl_nv, this_ctrl, value_changed = self.get_control(theas_name,
                                                                                  datavalue=this_value_str,
                                                                                  auto_create=True)

                        if value_changed:
                            changed_controls.append(this_ctrl_nv)

                self.authenicator = str(uuid.uuid4())  # set a new authenicator GUID

        if len(self.doOnAfterProcessRequest):
            for this_func in self.doOnAfterProcessRequest:
                this_func(self, request_handler=request_handler, accept_any=accept_any)

        return changed_controls


        # @environmentfilter
        # def theas_hidden(self, this_env, ctrl_name, *args, **kwargs):
        # This filter is called like:
        #         {{ "theas:HelloWorld"|hidden(my_param="abc"}}
        # As of Jinja 2.8 this requires a fix to nodes.py (see: https://github.com/pallets/jinja/issues/548)
        # Without the fix, the arguments do NOT behave as documented at:
        # http://jinja.pocoo.org/docs/dev/api/#custom-filters
        # The static string value that the filter was called on is passed as the first argument (ctrl_name).
        # The environment is passed as the second argument (this_env).
        # With the fix, the order of the arguments is correct (with the Jinja environment first, and the
        # static string second.
        # Additional arguments inside the parenthesis are passed in args[] or kwargs[] as expected.
        #   this_page = this_env.theas_page

        #   assert ctrl_name, 'Filter theas_hidden requires the id or name be provided as the first argument.'

        #   this_ctrl = this_page.get_control(ctrl_name, **kwargs)

        #   buf = '<input name="{}" type="hidden" value="{}"/>'.format(
        #       this_ctrl.name,
        #       this_ctrl.value
        #   )

        #   return buf

    @environmentfilter
    def theas_values_json(self, this_env, this_value, as_string=False, *args, **kwargs):
        this_th = self.get_controls(include_in_json_only=True)

        result = json.dumps(this_th)

        if as_string:
            if len(result) > 2:
                result = '{' + result[1:-1] + '}'
            else:
                result = ''
        return result

    @environmentfilter
    def theas_base64(self, this_env, this_value, *args, **kwargs):
        buf = base64.b64encode(this_value.encode(encoding='utf-8', errors='strict')).decode(encoding='ascii',
                                                                                            errors='strict')
        return "'{}'".format(buf)

    @environmentfilter
    def theas_resource(self, this_env, this_value, quotes=True, *args, **kwargs):

        this_value = this_value.lstrip('/')

        busted_filename = this_value

        # The idea is that this_value contains a resource code that may have been cached by the browser.
        # If the resource has subsequently been updated on the server, we want the browser to request the
        # resource...even though the old version is in cache.

        # So we append a version number to the filename, such as my.css becomes my.23.css (if version #23
        # were the current version of my.css)

        # A versioned filename will have at least two . characters in it: the ultimate preceding the file
        # extension, and the penultimate preceding the version number.

        # A versioned filename will be returned ONLY if the resource has been updated / has a non-null
        # Revision field value.  This version number will be stripped out by TheasServer when a
        # versioned request is made.  See:  TheasServer.py ThHandler.get(), about line 2509

        # We do need to retrieve the current version number of the resource.

        # Can pass in an optional parameter quotes=False to say No Quotes, which will strip out leading
        # and trailing quotes from the result.

        if this_value in this_env.theas_page.th_session.resource_versions:
            this_version = str(this_env.theas_page.th_session.resource_versions[this_value]['Revision'])

            segments = this_value.split('.')
            busted_filename = '.'.join(segments[:-1]) + '.ver.' + this_version + '.' + '.'.join(segments[-1:])

        busted_filename = '/' + busted_filename

        result = json.dumps(busted_filename)

        if not quotes:
            result = result[1:-1]

        return result

    @environmentfilter
    def theas_include(self, this_env, this_value, output=False, delims=('[[', ']]'), *args, **kwargs):
        this_control_nv = self.get_control(this_value)[0]
        this_control_nv.include_in_json = True

        buf = ''

        if output:
            buf = '{}{}{}'.format(
                delims[0],
                this_control_nv.name.replace(':', '$'),
                delims[1]
            )

        return buf

    @environmentfilter
    def theas_xsrf(self, this_env, this_value, *args, **kwargs):
        # This filter is called like:
        #         {{ "_th"|theasXSRF }}
        # No arguments are required.  The "_th" can be any value (i.e. the value is ignored)
        # This filter is just for convenience and consistency:  The user could directly use
        # {{ data._Theas.xsrf_formHTML }} instead.

        # buf = this_env.theas_page.th_session.current_data['_Theas']['xsrf_formHTML']
        buf = this_env.theas_page.th_session.current_xsrf_form_html

        return buf

    @environmentfilter
    def theas_sessiontoken(self, this_env, this_value, vuejs=False, *args, **kwargs):
        # This filter is called like:
        #         {{ "_th"|theasST }}
        # No arguments are required.  The "_th" can be any value (i.e. the value is ignored)

        buf = ''

        if not vuejs:
            buf = '<input name="{}" type="hidden" {}value="{}"/>'.format(
                'theas:th:ST',
                ':' if vuejs else '',  # bound attribute in vuejs
                'theasParams.th$ST' if vuejs else str(this_env.theas_page.th_session.session_token)
            )

        # sneak in hidden field to pass ErrorMessage
        buf += '<input name="{}" type="hidden" {}value="{}"/>'.format(
            'theas:th:ErrorMessage',
            ':' if vuejs else '',  # bound attribute in vuejs
            'theasParams.th$ErrorMessage' if vuejs else self.get_value('theas:th:ErrorMessage')  # bind to json in vuejs
        )

        # sneak in hidden field to pass CurrentPage
        buf += '<input name="{}" type="hidden" {}value="{}"/>'.format(
            'theas:th:CurrentPage',
            ':' if vuejs else '',  # bound attribute in vuejs
            'theasParams.th$CurrentPage' if vuejs else self.get_value('theas:th:CurrentPage')  # bind to json in vuejs
        )

        # sneak in hidden field to pass PerformUpdate
        buf += '<input name="{}" type="hidden" {}value="{}"/>'.format(
            'theas:th:PerformUpdate',
            ':' if vuejs else '',  # bound attribute in vuejs
            'theasParams.th$PerformUpdate' if vuejs else '0'  # bind to json in vuejs
        )

        return buf

    @environmentfilter
    def theas_hidden(self, this_env, this_value, escaping='urlencode', vuejs=False, *args, **kwargs):
        # This filter is called like:
        #         {{ data._Theas.osST|hidden(name="theas:HelloWorld") }}
        # The arguments  behave as documented at: http://jinja.pocoo.org/docs/dev/api/#custom-filters
        # The environment is passed as the first argument.  The value that the fitler was called on is
        # passed as the second argument (this_value).  Additional arguments inside the parenthesis are
        # passed in args[] or kwargs[]

        this_page = this_env.theas_page

        ctrl_name = None
        if 'name' in kwargs:
            ctrl_name = kwargs['name']

        assert ctrl_name, 'Filter theas_hidden requires either id or name be provided.'

        if ctrl_name.startswith('theas:'):
            ctrl_name = ctrl_name[6:]

        # id is not used for hidden
        this_ctrl_nv, this_ctrl, value_changed = this_page.get_control(ctrl_name, datavalue=this_value,
                                                                       control_type='hidden', **kwargs)

        value_str = ''
        if this_ctrl_nv.value is not None and not isinstance(this_ctrl_nv.value, SilentUndefined):
            if escaping == 'urlencode':
                value_str = urlparse.quote(str(this_ctrl_nv.value))
            elif escaping == 'htmlentities':
                value_str = html.escape(str(this_ctrl_nv.value), quote=True)
            else:
                value_str = str(this_ctrl_nv.value)

        buf = '<input name="{}" type="hidden" {}value="{}"/>'.format(
            this_ctrl_nv.name_prefix + this_ctrl_nv.name,
            ':' if vuejs else '',
            (this_ctrl_nv.name_prefix + this_ctrl_nv.name).replace(':', '$') if vuejs else value_str
        )

        this_ctrl_nv.include_in_json = True

        return buf

    @environmentfilter
    def theas_input(self, this_env, this_value, escaping="urlencode", vuejs=False, *args, **kwargs):
        # This filter is called like:
        #   {{data.EmployerJob.Company | theasInput(id="company", name="ejCompany", placeholder="", class ="form control input-md", required="")}}

        this_page = this_env.theas_page

        ctrl_name = None
        if 'name' in kwargs:
            ctrl_name = kwargs['name']

        type = 'text'
        if 'type' in kwargs:
            if kwargs['type'].lower() == 'password':
                type = 'password'

        assert ctrl_name, 'Filter theas_input requires either id or name be provided.'

        if ctrl_name.startswith('theas:'):
            ctrl_name = ctrl_name[6:]

        # id not used for looking up (but might be provided and might need to be rendered)
        this_ctrl_nv, this_ctrl, value_changed = this_page.get_control(ctrl_name, datavalue=this_value,
                                                                       control_type=type, **kwargs)

        this_attribs_str = ''
        for k, v in this_ctrl.attribs.items():
            this_attribs_str += ' {}="{}"'.format(k, v)

        buf = '<input name="{}"{} type="{}"{}'.format(
            this_ctrl_nv.name_prefix + this_ctrl_nv.name,
            format_str_if(this_ctrl.id, ' id="{}"'),
            this_ctrl_nv.control_type,
            this_attribs_str
        )

        # include value="" attribute, but only if we have a value

        value_str = ''
        if this_ctrl_nv.value is not None and not isinstance(this_ctrl_nv.value, SilentUndefined):
            if escaping == 'urlencode':
                value_str = urlparse.quote(str(this_ctrl_nv.value))
            elif escaping == 'htmlentities':
                value_str = html.escape(str(this_ctrl_nv.value), quote=True)
            else:
                value_str = str(this_ctrl_nv.value)

        if this_ctrl_nv.value:
            buf += ' {}value="{}">'.format(
                ':' if vuejs else '',
                (this_ctrl_nv.name_prefix + this_ctrl_nv.name).replace(':', '$') if vuejs else value_str
            )
        else:
            buf += '>'

        this_ctrl_nv.include_in_json = True

        return buf

    @environmentfilter
    def theas_radio(self, this_env, this_value, *args, **kwargs):
        # This filter is called like:
        #   {{data.EmployerJob.JobApplicationsVia_code | theasRadio(id="email-app", name="ejReceiveAppsBy", class ="trigger", required="", data_rel="emailapp" checked_value="email", value="email")}}

        this_page = this_env.theas_page
        ctrl_name = None
        if 'name' in kwargs:
            ctrl_name = kwargs['name']

        assert ctrl_name, 'Filter theas_radio requires either id or name be provided.'

        if ctrl_name.startswith('theas:'):
            ctrl_name = ctrl_name[6:]

        # id should be specified for clarity.  If id is not provided, will try to find the correct control
        # based on name + value
        this_ctrl_nv, this_ctrl, value_changed = this_page.get_control(ctrl_name, datavalue=this_value,
                                                                       control_type='radio', **kwargs)

        this_attribs_str = ''
        for k, v in this_ctrl.attribs.items():
            this_attribs_str += ' {}="{}"'.format(k, v)

        buf = '<input name="{}"{} type="{}"{} value="{}"{}>'.format(
            this_ctrl_nv.name_prefix + this_ctrl_nv.name,
            format_str_if(this_ctrl.id, ' id="{}"'),
            this_ctrl_nv.control_type,
            this_attribs_str,
            this_ctrl.value,
            ' checked="checked"' if this_ctrl.checked else ''
        )

        this_ctrl_nv.include_in_json = True

        return buf

    @environmentfilter
    def theas_select(self, this_env, this_options, this_value, *args, **kwargs):
        '''theas_select() : Jinja filter for rendering an HTML select, with options
        Options can be provided either via a string (i.e. hard-coded in the HTML template)
        or via a list (i.e. from a data.xxx member)
        Call like this:

        {% filter theasSelect(data.MyRecordset.MyField, id="selectSeries",
        name="MyyRecordset:myField",
        source_list=data.MyLookupRecordset, source_value="qguid", source_label="SeriesTitle",
        class="form-control") %}

        someValue = Some Caption

        {% endfilter %}

        In this example, the options specified in the string (i.e. someValue = SomeCaption)
        first.  Then, any rows in data.MyLookupRecordset will be added (using the field
        qquid as the value and the field SeriesTitle as the caption)
        '''
        this_page = this_env.theas_page
        ctrl_name = None
        if 'name' in kwargs:
            ctrl_name = kwargs['name']

        assert ctrl_name, 'Filter theas_select requires either id or name be provided.'

        if ctrl_name.startswith('theas:'):
            ctrl_name = ctrl_name[6:]

        this_options_dict = OrderedDict()

        # add options from string passed in this_options
        if this_options:
            options_strings = this_options
            options_list = options_strings.splitlines(False)
            for nvstr in options_list:
                nvstr = nvstr.strip()
                if nvstr:
                    this_nv = nvstr.split('=')
                    this_options_dict[this_nv[0].strip()] = this_nv[1].strip()

        # add options from list passed in source_list
        if 'source_list' in kwargs and 'source_value' in kwargs and 'source_label' in kwargs:
            for this_row in kwargs['source_list']:
                this_options_dict[this_row[kwargs['source_value']]] = this_row[kwargs['source_label']]

        this_ctrl_nv, this_ctrl, value_changed = this_page.get_control(ctrl_name, datavalue=this_value,
                                                                       control_type='select',
                                                                       options_dict=this_options_dict, **kwargs)

        this_attribs_str = ''
        for k, v in this_ctrl.attribs.items():
            this_attribs_str += ' {}="{}"'.format(k, v)

        buf = '<select name="{}"{}{} >'.format(
            this_ctrl_nv.name_prefix + this_ctrl_nv.name,
            format_str_if(this_ctrl.id, ' id="{}"'),
            this_attribs_str
        )

        for temp_optval, temp_optctrl in this_ctrl_nv.controls.items():
            buf = buf + '\n<option value="{}"{}{}>{}</option>'.format(
                temp_optval,
                ' selected="selected"' if (
                (temp_optctrl.checked) or (not this_ctrl_nv.value and not temp_optval)) else '',
                ' disabled="disabled"' if not temp_optval else '',
                temp_optctrl.caption
            )
        buf = buf + '\n</select>'

        this_ctrl_nv.include_in_json = True

        return buf

    @environmentfilter
    def theas_textarea(self, this_env, this_value, escaping='urlencode', vuejs=False, *args, **kwargs):
        # This filter is called like:
        #   {{data.EmployerJob.BasicQualifications | theasTextarea(id="basicQualifications", name="ejBasicQualifications", class ="form-control")}}

        this_page = this_env.theas_page

        ctrl_name = None
        if 'name' in kwargs:
            ctrl_name = kwargs['name']

        assert ctrl_name, 'Filter theas_textarea requires either id or name be provided.'

        if ctrl_name.startswith('theas:'):
            ctrl_name = ctrl_name[6:]

        this_ctrl_nv, this_ctrl, value_changed = this_page.get_control(ctrl_name, datavalue=this_value,
                                                                       control_type='textarea', **kwargs)

        this_attribs_str = ''
        for k, v in this_ctrl.attribs.items():
            this_attribs_str += ' {}="{}"'.format(k, v)

        value_str = ''
        if this_ctrl_nv.value is not None and not isinstance(this_ctrl_nv.value, SilentUndefined):
            if escaping == 'urlencode':
                value_str = urlparse.quote(str(this_ctrl_nv.value))
            elif escaping == 'htmlentities':
                value_str = html.escape(str(this_ctrl_nv.value), quote=True)
            else:
                value_str = str(this_ctrl_nv.value)

        buf = '<textarea name="{}"{}{}{}>{}</textarea>'.format(
            this_ctrl_nv.name_prefix + this_ctrl_nv.name,
            format_str_if(this_ctrl.id, ' id="{}"'),
            'v-model={}'.format((this_ctrl_nv.name_prefix + this_ctrl_nv.name).replace(':', '$')) if vuejs else '',
            this_attribs_str,
            value_str
        )

        this_ctrl_nv.include_in_json = True

        return buf

    @environmentfilter
    def theas_checkbox(self, this_env, this_value, *args, **kwargs):
        # This filter is called like:
        #   {{data.EmployerJob.AgreeTerms | theasCheckbox(id="agreeTermsOfUse", name="ejAgreeTermsOfUse", checked_value="1", value="1")}}

        this_page = this_env.theas_page
        ctrl_name = None
        if 'name' in kwargs:
            ctrl_name = kwargs['name']

        assert ctrl_name, 'Filter theas_checkbox requires either id or name be provided.'

        if ctrl_name.startswith('theas:'):
            ctrl_name = ctrl_name[6:]

        this_ctrl_nv, this_ctrl, value_changed = this_page.get_control(ctrl_name, datavalue=this_value,
                                                                       control_type='checkbox', **kwargs)

        this_attribs_str = ''
        for k, v in this_ctrl.attribs.items():
            this_attribs_str += ' {}="{}"'.format(k, v)

        buf = '<input name="{}"{} type="{}"{} value="{}"{}>'.format(
            this_ctrl_nv.name_prefix + this_ctrl_nv.name,
            format_str_if(this_ctrl.id, ' id="{}"'),
            this_ctrl_nv.control_type,
            this_attribs_str,
            this_ctrl.value,
            ' checked="checked"' if this_ctrl.checked else ''
        )

        this_ctrl_nv.include_in_json = True

        return buf

    @environmentfilter
    def theas_define_functions(self, ctrl_name, this_env, *args, **kwargs):

        this_page = this_env.theas_page

        python_source = args[0]
        this_page.create_functions(python_source)

        return ''

    @environmentfilter
    def theas_define_filter(self, ctrl_name, this_env, *args, **kwargs):

        this_page = this_env.theas_page
        python_source = args[0]

        for n, f in this_page.create_functions(python_source).items():
            this_page.jinja_env.filters[n] = f

        return ''

    @environmentfilter
    def theas_echo(self, this_env, this_value, *args, **kwargs):
        '''

        :param this_env:
        :param this_value:
        :param args:
        :param kwargs:
        :return:

        This filter is called like:
                {{ 'active'|theasEcho(if_curpage='mypage') }}

                 This would cause the word "active" to be writen out to the HTML, if the
                 current page is equal to mypage

        Note that mypage is to contain the resource_code for the page.

        Can also be called on other ways, such as:
                {{ 'active'|theasEcho(control_name='Customer:CompanyName', target_value='ABC, Inc.') }}

                This would cause the word "active" to be writen out to the HTML, if the value of the
                Theas control named Customer:CompanyName equals the targe_value of "ABC, Inc."


                {{ data.SomeRow.SomeCol|theasEcho(else_output='N/A') }}

                This would cause the value of data.SomeRow.SomeCol to be written out to the HTML
                if it is not None.  If it is none, the else_output string of 'N/A' would be written.

                {{ data.SomeRow.SomeCol|theasEcho }}

                This would cause the value of data.SomeRow.SomeCol to be written out to the HTML
                if it is not None.  If it is None, an empty string would be written.  (Without this

        '''

        buf = ''

        this_page = this_env.theas_page
        target_curpage = None
        control_name = None
        target_value = None
        else_output = None
        append_str = ''

        if 'if_curpage' in kwargs:
            target_curpage = kwargs['if_curpage']

        if 'control_name' in kwargs:
            control_name = kwargs['control_name']

        if 'target_value' in kwargs:
            target_value = kwargs['target_value']

        if 'else_output' in kwargs:
            else_output = kwargs['else_output']

        if 'append_str' in kwargs:
            append_str = kwargs['append_str']

        # conditionally echo this_value if the current page matches target_curpage
        if target_curpage is not None and this_page and this_page.th_session:
            if this_page.th_session.current_resource.resource_code == target_curpage:
                buf = this_value

        # conditionally echo this_value if the value of control_name matches target_value
        elif control_name is not None and target_value is not None:
            this_ctrl_nv, this_ctrl, value_changed = this_page.get_control(control_name)

            if this_ctrl_nv is not None and this_ctrl_nv.value == target_value:
                buf = this_value

        # simply echo the provided this_value
        else:
            buf = this_value

        if buf:
            buf = buf + append_str
        else:
            if else_output:
                buf = else_output
            else:
                buf = ''

        return buf

    def render(self, template_str, data={}):
        # Call doOnBeforeRender function(s) if provided
        if len(self.doOnBeforeRender):
            for this_func in self.doOnBeforeRender:
                result_template_str, result_data = this_func(self, template_str=template_str, data=data)
                if result_template_str:
                    template_str = result_template_str
                if result_data:
                    data = result_data

        # self.authenicator = str(uuid.uuid4())  #set a new authenticator GUID

        # render "function_def" template to define functions
        # if self.template_function_def_str:
        #    function_def_template = self.jinja_env.from_string(self.template_function_def_str)
        #    function_def_template.render()

        # set th:CurrentPage
        if self.th_session and self.th_session.current_resource and self.th_session.current_resource.resource_code:
            self.get_control('th:CurrentPage', datavalue=self.th_session.current_resource.resource_code)

        # render output using template and data
        this_template = self.jinja_env.from_string(template_str)
        buf = this_template.render(data=data)

        # Call doOnAfterRender function(s) if provided
        if len(self.doOnAfterRender):
            for this_func in self.doOnAfterRender:
                result_template_str, result_data, result_buf = this_func(self, buf=buf, template_str=template_str,
                                                                         date=data)
                if result_template_str:
                    template_str = result_template_str
                if result_data:
                    data = result_data
                if result_buf:
                    buf = result_buf

        return buf

    def theas_exec(self, function_name):
        this_function = None
        this_result = None

        if function_name in self.functions:
            this_function = self.functions[function_name]
        elif ALLOW_UNSAFE_FUNCTIONS:
            if '_unsafe_' + function_name in self.functions:
                this_function = self.functions['_unsafe_' + function_name]

        if this_function is not None:
            this_result = this_function()
        return this_result

    def create_functions(self, python_source):
        """  This method allows the caller to create a new Theas method from Python source code
             :param python_source: source code that declares the new method
             :return: None

             To use, you must set ALLOW_UNSAFE_FUNCTIONS = True

             IMPORTANT:  the source code passed into python_source must be trusted.  If this string comes
             from an untrusted source, the code could contain malicious code that would compromise the server.

             This method does need to execute the code in the globals() context to create the function(s)
             defined in python_source, but the method does first rename all functions to a GUID-based name
             to avoid name collisions.
        """
        if not ALLOW_UNSAFE_FUNCTIONS:
            raise Exception(
                'Error in Theas.create_functions:  ALLOW_UNSAFE_FUCTIONS = False, so this method may not be called.')

        new_functions = {}

        # Get list of function names declared in python_source
        source_tree = ast.parse(python_source)

        class FuncLister(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                new_functions[node.name] = '_' + uuid.uuid4().hex
                print(node.name)
                self.generic_visit(node)

        FuncLister().visit(source_tree)

        # change function names to safer GUID-based name, since these need to get created in the global namespace
        class RewriteName(ast.NodeTransformer):
            def visit_FunctionDef(self, node):
                node.name = new_functions[node.name]
                return node

        source_tree = RewriteName().visit(source_tree)

        # execute modified source_tree that has the new function names
        code = compile(source_tree, '<string>', 'exec')
        exec(code, globals())

        # now the new functions exist in the global namespace, with the guid-based function name

        functions_created = {}

        # walk through list of new functions to turn them into methods of this object
        for k, v in new_functions.items():
            # retrieve the temporary global function object
            gf = globals()[v]

            # turn function into a method
            mf = types.MethodType(gf, self)

            # store method as an attribute of this object
            # note:  this is not necessary, as the function object is saved to the .functions dictionary
            # setattr(self, '_unsafe_' + k, mf)

            # clear out the temporary global function
            exec(v + ' = None', globals())

            # add function to the list of page functions
            self.functions['_unsafe_' + k] = mf

            functions_created[k] = mf

        return functions_created

    def serialize(self, control_list=None):
        buf = ''
        this_key = None
        this_control_nv = None

        if control_list is None:
            # serialize all controls
            for this_key, this_control_nv in self.control_names.items():
                buf += this_control_nv.name + '='
                if this_control_nv.value is not None and not isinstance(this_control_nv.value, SilentUndefined):
                    buf += urlparse.quote(str(this_control_nv.value))
                buf += '&'
        else:
            # serialize only the controls specified in control_list
            for this_control_nv in control_list:
                buf += this_control_nv.name + '='
                if this_control_nv.value is not None and not isinstance(this_control_nv.value, SilentUndefined):
                    buf += urlparse.quote(str(this_control_nv.value))
                buf += '&'

        return buf


# Official list is at: http://www.iana.org/assignments/media-types/media-types.xhtml
MIME_TYPE_EXTENSIONS = {
    '.jpg': 'image/jpeg',
    '.3g2': 'video/3gpp2',
    '.3gp': 'video/3gpp',
    '.7z': 'application/x-7z-compressed',
    '.ai': 'application/postscript',
    '.aif': 'audio/x-aiff',
    '.air': 'application/vnd.adobe.air-application-installer-package+zip',
    '.apk': 'application/vnd.android.package-archive',
    '.asf': 'video/x-ms-asf',
    '.avi': 'video/x-msvideo',
    '.bmp': 'image/bmp',
    '.cab': 'application/vnd.ms-cab-compressed',
    '.chm': 'application/vnd.ms-htmlhelp',
    '.css': 'text/css',
    '.doc': 'application/msword',
    '.docm': 'application/vnd.ms-word.document.macroenabled.12',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.dotm': 'application/vnd.ms-word.template.macroenabled.12',
    '.dotx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.template',
    '.dts': 'audio/vnd.dts',
    '.dwf': 'model/vnd.dwf',
    '.dwg': 'image/vnd.dwg',
    '.dxf': 'image/vnd.dxf',
    '.eml': 'message/rfc822',
    '.eps': 'application/postscript',
    '.exe': 'application/x-msdownload',
    '.gif': 'image/gif',
    '.gtar': 'application/x-gtar',
    '.hlp': 'application/winhlp',
    '.hqx': 'application/mac-binhex40',
    '.htm': 'text/html',
    '.html': 'text/html',
    '.icc': 'application/vnd.iccprofile',
    '.ico': 'image/x-icon',
    '.ics': 'text/calendar',
    '.jar': 'application/java-archive',
    '.java': 'text/x-java-source,java',
    '.jnlp': 'application/x-java-jnlp-file',
    '.jpeg': 'image/jpeg',
    '.jpgv': 'video/jpeg',
    '.js': 'application/javascript',
    '.json': 'application/json',
    '.kml': 'application/vnd.google-earth.kml+xml',
    '.kmz': 'application/vnd.google-earth.kmz',
    '.ktx': 'image/ktx',
    '.latex': 'application/x-latex',
    '.less': 'text/css',
    '.m3u': 'audio/x-mpegurl',
    '.mdb': 'application/x-msaccess',
    '.mid': 'audio/midi',
    '.mny': 'application/x-msmoney',
    '.mov': 'video/quicktime',
    '.mp3': 'audio/x-mpeg-3',  # 'audio/mpeg',
    '.mp4': 'video/mp4',
    '.mp4a': 'audio/mp4',
    '.mpeg': 'video/mpeg',
    '.mpg': 'video/mpeg',
    '.mpga': 'audio/mpeg',
    '.mpkg': 'application/vnd.apple.installer+xml',
    '.mpp': 'application/vnd.ms-project',
    '.onetoc': 'application/onenote',
    '.pcl': 'application/vnd.hp-pcl',
    '.pcx': 'image/x-pcx',
    '.pdf': 'application/pdf',
    '.pgp': 'application/pgp-signature',
    '.pl': 'application/x-perl',
    '.png': 'image/png',
    '.potm': 'application/vnd.ms-powerpoint.template.macroenabled.12',
    '.potx': 'application/vnd.openxmlformats-officedocument.presentationml.template',
    '.ppam': 'application/vnd.ms-powerpoint.addin.macroenabled.12',
    '.ppd': 'application/vnd.cups-ppd',
    '.ppsm': 'application/vnd.ms-powerpoint.slideshow.macroenabled.12',
    '.ppsx': 'application/vnd.openxmlformats-officedocument.presentationml.slideshow',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.pptm': 'application/vnd.ms-powerpoint.presentation.macroenabled.12',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.ps': 'application/postscript',
    '.psd': 'image/vnd.adobe.photoshop',
    '.pub': 'application/x-mspublisher',
    '.qfx': 'application/vnd.intu.qfx',
    '.qt': 'video/quicktime',
    '.qxd': 'application/vnd.quark.quarkxpress',
    '.ram': 'audio/x-pn-realaudio',
    '.rm': 'application/vnd.rn-realmedia',
    '.rsd': 'application/rsd+xml',
    '.rss': 'application/rss+xml',
    '.rtf': 'application/rtf',
    '.rtx': 'text/richtext',
    '.sh': 'application/x-sh',
    '.sit': 'application/x-stuffit',
    '.sitx': 'application/x-stuffitx',
    '.svg': 'image/svg+xml',
    '.swf': 'application/x-shockwave-flash',
    '.tar': 'application/x-tar',
    '.tcl': 'application/x-tcl',
    '.tif': 'image/tiff',
    '.tiff': 'image/tiff',
    '.torrent': 'application/x-bittorrent',
    '.tsv': 'text/tab-separated-values',
    '.ttf': 'application/x-font-ttf',
    '.txt': 'text/plain',
    '.vsd': 'application/vnd.visio',
    '.vue': 'application/javascript',
    '.wav': 'audio/x-wav',
    '.weba': 'audio/webm',
    '.webm': 'video/webm',
    '.wma': 'audio/x-ms-wma',
    '.wmd': 'application/x-ms-wmd',
    '.wmf': 'application/x-msmetafile',
    '.wmv': 'video/x-ms-wmv',
    '.woff': 'application/x-font-woff',
    '.wpd': 'application/vnd.wordperfect',
    '.wps': 'application/vnd.ms-works',
    '.wri': 'application/x-mswrite',
    '.wvx': 'video/x-ms-wvx',
    '.xap': 'application/x-silverlight-app',
    '.xhtml': 'application/xhtml+xml',
    '.xif': 'image/vnd.xiff',
    '.xlam': 'application/vnd.ms-excel.addin.macroenabled.12',
    '.xls': 'application/vnd.ms-excel',
    '.xlsm': 'application/vnd.ms-excel.sheet.macroenabled.12',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.xml': 'application/xml',
    '.xps': 'application/vnd.ms-xpsdocument',
    '.zip': 'application/zip',

    '.csv': 'text/csv'

}
