#!usr/bin/python
# Filename: theas.py
# -*- coding: utf-8 -*-
version = '0.78'

'''Theas complements the Jinja2 templeting enjine by provining persistent server-side state manamengemt,
datta binding, and event handling.

The name "Jinja" was selected because it is the name for a Japneese temple.  "Theas" is the Greek word for
godess...as in "the godess of the temple".  Theas controls the way a template can be rendered from, and bound
to, server-side "components'.

Often times a Jinja2 template may be used to render an HTML form.  Theas provides multiple means of
creating and updating a server-side "control" (TheasControl) that stores the value and other attributes of
HTML form fields.

These "controls" can then be automatically updated by Theas on an HTTP POST.  Also, the "controls" can
be updated in server-side code, and the updated controls will automatically be rendered the next time the
template is rendered.

The main class provided is Theas.  This is provided to represent a "page" that is sent to a browser.  It
manages a list of TheasControls, provides a number of custom Jinja filters, a wraper around Jinja to provide a
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

NOTE:  dynamic defintion of event handers can be very handy, but does pose a potential security risk if the
event handler source comes from "untrusted" outside sources.  By default, a constant ALLOW_UNSAFE_FUNCTIONS = False
is defined to disable this capability.

If dynamic source (surch as from Jinja templates) is properly managed and comes from only trusted sources,
ALLOW_UNSAFE_FUNCTIONS may be safely enabled.

If enabled, you can do things like:

a) Define custom Jinja filters via Python source embedded within a Jinja filter itself.  Note that really the
filter definition source is placed within a SEPARATE template file that is renderd first.  Then filters defined
in that template file can be used directly from a template file that is used for the actual rendering of the
HTML output.

b) Define custom Jinja filters in a database that loads them at run-time.

c) Define custom Theas methods that can be called when a template is rendered, when an HTTP form post is
received, or when an HTTP AJAX request is received.  These can be defined in static Python source, or dynamically--
either embedded in a Jinja template, or in a string stored in a database or some other location.

See https://github.com/mitsuhiko/jinja2 and http://jinja.pocoo.org for more information on Jinja2


this_data['_Local']['theasParams'

Within your template, you can access data from the resultsets like this:
        {{ data.Employer.JobTitle }}
        {{ data.Employer.Company }}
        {{ data.Employer.WorkplaceLocation }}


You can add form fields containing Theas control values to your page by including filters like this in your template:
        {{ data.EmployerJob.JobTitle|theasHidden(name="EmployerJob:JobTitle") }}
        {{ data.EmployerJob.Company|theasHidden(name="EmployerJob:Company") }}
        {{ data.EmployerJob.WorkplaceLocation|theasHidden(name="EmployerJob:WorkplaceLocation") }}

Within your template, you can also access access the values of Theas controls like this:
    {{ data._Local.theasParams["Theas:nextPage"] }}

    You could also use theasParams to access data values, such as:

    {{ data._Local.theasParams["EmployerJob:JobTitle"] }}

    ...but this would return the value of the session.theas_page.control, which a) would not exist unless the control
    had been previously created by a filter or by Python code prior to the start of template rendering.
'''
import types
import string
from collections import OrderedDict
import ast
import uuid


from jinja2 import Template, Undefined, environmentfilter, Markup, escape
from jinja2.environment import Environment

ALLOW_UNSAFE_FUNCTIONS = False


def format_str_if(this_str, fmt_str):
    buf = ''

    if this_str:
        this_str = this_str
        buf = fmt_str.format(this_str)
    return buf


#-----Jinja2 undefined variable class-----
class SilentUndefined(Undefined):
    def _fail_with_undefined_error(self, *args, **kwargs):
        return ''

class TheasException(Exception):
    pass

class TheasControl:
    def __init__(self):
        #self.control_nv = None
        self.id = None
        self.authenticator = None
        self.checked = ''
        self.value = ''
        self.caption = ''
        self.attribs = OrderedDict()

    #def __del__(self):
        #self.control_nv = None

noneTheasControl = TheasControl()

class TheasControlNV:
    def __init__(self, name='', control_type=None):
        self.name = name
        self.controls = OrderedDict()
        self.__datavalue = ''
        self.value = ''
        self.control_type = control_type

    def __del__(self):
        #for ctrl_value in self.controls:
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

        if not isinstance(jinja_environment, Environment):
            #set up new jinja environment
            self.jinja_env = Environment()

            self.jinja_env.theas_page = self

            self.jinja_env.undefined = SilentUndefined


            self.jinja_env.filters['theasSessionToken'] = self.theas_sessiontoken
            self.jinja_env.filters['theasXSRF'] = self.theas_xsrf
            self.jinja_env.filters['theasHidden'] = self.theas_hidden
            self.jinja_env.filters['theasInput'] = self.theas_input
            self.jinja_env.filters['theasRadio'] = self.theas_radio
            self.jinja_env.filters['theasSelect'] = self.theas_select
            self.jinja_env.filters['theasTextarea'] = self.theas_textarea
            self.jinja_env.filters['theasCheckbox'] = self.theas_checkbox

            self.jinja_env.filters['theasEcho'] = self.theas_echo
            self.jinja_env.filters['friendlydate'] = self.format_friendlydate

            # self.jinja_env.filters['button'] = self.theas_button

            self.jinja_env.filters['theasDefineFunctions'] = self.theas_define_functions
            self.jinja_env.filters['theasDefineFilter'] = self.theas_define_filter

        else:
            #reuses existing jinja environment
            self.jinja_env = jinja_environment


        self.th_session = theas_session
        self.control_names = {}
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
        result = 'text/html'

        fn = None

        if filename:
            fn, sep, extension = filename.rpartition('.')
        if fn:
            global MIME_TYPE_EXTENSIONS
            if extension:
                extension = '.' + extension
                if extension in MIME_TYPE_EXTENSIONS:
                    result = MIME_TYPE_EXTENSIONS[extension]

        return result

    # ------------Jinja filter functions-------------
    def format_friendlydate(self, value, pre="", post="", formatstr="%a %m/%d/%y"):
        '''

        :param value:
        :param pre:
        :param post:
        :param formatstr:
        :return:

        Default format is DOW MM/DD/YY as per strftime('%a %m/%d/%y')
        '''
        s = ''
        if value:
            s = value.strftime(formatstr)
        if value and pre:
            s = pre + s
        if value and post:
            s = s + post
        return s

    def get_control(self, ctrl_name, control_type=None, id=None, auto_create=True, **kwargs):
        # NOTE:  pass in datavalue='xxx' to set the value of the control

        # If control is a radio, checkbox or select, you can pass in value='yyy' which does
        # not set the value of the control but merely defines the value that will be used if the
        # control element ins checked.

        # For other types, it is an error to pass in value='yyy'

        # HTML id attributes are unique.  HTML name attributes are not necessisarly unique.
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

        if ctrl_name:
            # The HTML <input> names begin with theas:, but in Python and elsewhere
            # we omit this prefix.
            if ctrl_name.startswith('theas:'):
                ctrl_name = ctrl_name[6:]

            if ctrl_name in (self.control_names):
                #look up exisitng control by name
                this_ctrl_nv = self.control_names[ctrl_name]

            if this_ctrl_nv is None:
                if auto_create:
                    if not control_type:
                        control_type = 'hidden'
                    this_ctrl_nv = TheasControlNV(name=ctrl_name, control_type=control_type)
                    self.control_names[ctrl_name] = this_ctrl_nv


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

            if this_ctrl_nv.control_type not in ('radio', 'select') and len(this_ctrl_nv.controls) == 1:
                this_ctrl = this_ctrl_nv.controls[list(this_ctrl_nv.controls.keys())[0]]
                if this_ctrl is not None and value_param != this_ctrl.value:
                    this_ctrl = None

            if this_ctrl is None:
                if this_ctrl_nv.control_type != 'select' and (value_param or len(this_ctrl_nv.controls) == 0):
                    #We must create a new control.  Value and other attributes will be set below.
                    this_ctrl = TheasControl()
                    #this_ctrl.control_nv = this_ctrl_nv

                    this_ctrl.value = value_param
                    this_ctrl_nv.controls[value_param] = this_ctrl
                    value_changed = True


            this_options_dict = this_ctrl_nv.controls

            this_attribs = {}

            for this_key, this_paramvalue in kwargs.items():

                if this_key == 'options_dict':
                    this_options_dict = kwargs[this_key]

                elif this_key in ('name', 'value', 'datavalue', 'source_list', 'source_value', 'source_label'):
                    #this kwarg does not apply or has already been handled
                    pass

                else:
                    #HTML attributes that have a - are a problem, because this is not a valid character for a
                    #Python identifier.  In particular, the HTML5 data-xxx="yyy" tag is a problem.
                    #It is up to the user to replace - with _ in attribute names, however Theas does
                    #treat data_ as data- internally
                    if this_key.startswith('data_'):
                        this_key = this_key.replace('data_', 'data-')
                    this_attribs[this_key] = this_paramvalue

            if this_ctrl_nv.control_type == 'select' and this_options_dict is not None:
                #create pseudo control for each select option
                for this_opt, this_caption in this_options_dict.items():
                    if this_opt not in this_ctrl_nv.controls:
                        temp_ctrl = TheasControl()
                        #temp_ctrl.control_nv = this_ctrl_nv
                        temp_ctrl.value = this_opt
                        temp_ctrl.caption = this_caption

                        this_ctrl_nv.controls[this_opt] = temp_ctrl

            if have_datavalue_param:
                if this_ctrl_nv.datavalue != datavalue_param:
                    value_changed = True
                # We want to go ahead and assign this value even if we don't think it has changed, because the
                # datavalue property setter will set .checked which will not yet be set in the case of a new
                # auto-created control.
                this_ctrl_nv.datavalue = datavalue_param

            if this_ctrl is None:
                this_ctrl = noneTheasControl
            else:
              this_ctrl.authenticator = self.authenicator  # record which authenticator created the control

            if this_ctrl is not None:
                this_ctrl.attribs = this_attribs
                if id:
                    this_ctrl.id = id

            if control_type is not None and control_type != this_ctrl_nv.control_type and (not this_ctrl_nv.control_type or this_ctrl_nv.control_type=='hidden'):
                # Even on an existing control we want to update control_type if it is provided, because
                # the control could have been created from TheasParams from a stored procedure and defaulted
                # to hidden...but now a filter or something else is specifying the "real" type.
                this_ctrl_nv.control_type = control_type

        return this_ctrl_nv, this_ctrl, value_changed


    def get_controls(self):
        #return dictionary of control name-value pairs
        this_result = {}
        for this_ctrl_name, this_nv in self.control_names.items():
            this_result[this_ctrl_name] = this_nv.value

        return this_result


    def get_value(self, ctrl_name):
        this_result = None

        this_ctrl_nv, this_ctrl, value_changed = self.get_control(ctrl_name)

        if this_ctrl_nv is not None:
            this_result = this_ctrl_nv.value

        return this_result


    def set_value(self, ctrl_name, new_value):
        this_ctrl_nv, this_ctrl, value_changed = self.get_control(ctrl_name, datavalue=new_value)

        this_result = None
        if this_ctrl_nv is not None:
            this_result = this_ctrl_nv.value

        return this_result, value_changed



    def process_client_request(self, request_handler = None, accept_any = False, buf = None, *args, **kwargs):
        #handle updating theas_page controls
        #('Theas: process_client_request starting')

        perform_processing = True

        self.th_session.log('Theas', 'Updating Theas controls')

        if len(self.doOnBeforeProcessRequest):
            for this_func in self.doOnBeforeProcessRequest:
                perform_processing = this_func(self, request_handler=None, accept_any=accept_any)

        theas_name = ''
        changed_controls = []

        if buf and buf.index('=') > 0:
            for this_nv in buf.split('&'):
                if this_nv and this_nv.index('=') > 0:
                    this_name, v = this_nv.split('=')

                    theas_name = this_name

                    if theas_name.startswith('theas:'):
                        theas_name = theas_name[6:]


                    #The HTML <input> names begin with theas:, but in Python and elsewhere
                    #we omit this prefix.
                    if theas_name.startswith('theas:'):
                        theas_name = theas_name[6:]

                    if '~~' in v:
                        #note that buf may contain spesial escaping of & and = (i.e. from SQL stored procedure)
                        v = v.replace('~~amp~~', '&').replace('~~eq~~', '&')

                    this_ctrl_nv, this_ctrl, value_changed = self.get_control(theas_name,
                                                               datavalue=v,
                                                               auto_create=True)

                    if value_changed:
                        changed_controls.append(this_ctrl_nv)

                    #print('Theas: Updated control {}={}  Result={}'.format(theas_name, v, this_ctrl_nv.datavalue))

        else:
            '''            if perform_processing and request_handler and request_handler.request.body_arguments:
                # process fields in the body of the posted data
                for this_name, this_value in request_handler.request.body_arguments.items():
                    theas_name = this_name

                    if theas_name.startswith('theas:'):
                        theas_name = theas_name[6:]


                    this_ctrl_nv, this_ctrl, value_changed = self.get_control(theas_name,
                                                datavalue=this_value[0].decode('utf-8'),
                                                auto_create=True)

                    #print('Theas: Updated control {}={}  Result={}'.format(theas_name, this_value[0].decode('utf-8'), this_ctrl_nv.datavalue))

                self.authenicator = str(uuid.uuid4())  #set a new authenicator GUID
            '''

            if perform_processing and request_handler and request_handler.request.arguments:
                # process query string parameters

                for this_name, this_value in request_handler.request.arguments.items():
                    theas_name = this_name

                    if theas_name.startswith('theas:'):
                        theas_name = theas_name[6:]

                    this_ctrl_nv, this_ctrl, value_changed = self.get_control(theas_name,
                                                                              datavalue=this_value[0].decode('utf-8'),
                                                                              auto_create=True)

                    # print('Theas: Updated control {}={}  Result={}'.format(theas_name, this_value[0].decode('utf-8'), this_ctrl_nv.datavalue))

                self.authenicator = str(uuid.uuid4())  # set a new authenicator GUID


        if len(self.doOnAfterProcessRequest):
            for this_func in self.doOnAfterProcessRequest:
                this_func(self, request_handler=request_handler, accept_any=accept_any)


        return changed_controls


    #@environmentfilter
    #def theas_hidden(self, this_env, ctrl_name, *args, **kwargs):
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
    def theas_sessiontoken(self, this_env, this_value, *args, **kwargs):
        # This filter is called like:
        #         {{ "_th"|theasST }}
        # No arguments are required.  The "_th" can be any value (i.e. the value is ignored)

        buf = '<input name="{}" type="hidden" value="{}"/>'.format(
            'theas:th:ST',
            this_env.theas_page.th_session.session_token
        )
        return buf

    @environmentfilter
    def theas_xsrf(self, this_env, this_value, *args, **kwargs):
        # This filter is called like:
        #         {{ "_th"|theasXSRF }}
        # No arguments are required.  The "_th" can be any value (i.e. the value is ignored)
        # This filter is just for convenience and consistency:  The user could directly use
        # {{ data._Local.xsrf_formHTML }} instead.

        #buf = this_env.theas_page.th_session.current_data['_Local']['xsrf_formHTML']
        buf = this_env.theas_page.th_session.current_xsrf_form_html

        return buf


    @environmentfilter
    def theas_hidden(self, this_env, this_value, *args, **kwargs):
        #This filter is called like:
        #         {{ data._Local.osST|hidden(name="theas:HelloWorld") }}
        #The arguments  behave as documented at: http://jinja.pocoo.org/docs/dev/api/#custom-filters
        #The environment is passed as the first argument.  The value that the fitler was called on is
        #passed as the second argument (this_value).  Additional arguments inside the parenthesis are
        #passed in args[] or kwargs[]

        this_page = this_env.theas_page

        ctrl_name = None
        if 'name' in kwargs:
            ctrl_name = kwargs['name']

        assert ctrl_name, 'Filter theas_hidden requires either id or name be provided.'

        if ctrl_name.startswith('theas:'):
            ctrl_name = ctrl_name[6:]

        if str(this_value) == '__th':
            #use server value for this control
            this_value = self.get_value(ctrl_name)


        #id is not used for hidden
        this_ctrl_nv, this_ctrl, value_changed = this_page.get_control(ctrl_name, datavalue=this_value, control_type='hidden', **kwargs)

        buf = '<input name="{}" type="hidden" value="{}"/>'.format(
            'theas:' + this_ctrl_nv.name,
            '' if this_ctrl_nv.value is None else this_ctrl_nv.value
        )
        return buf


    @environmentfilter
    def theas_input(self, this_env, this_value, *args, **kwargs):
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

        #id not used for looking up (but might be provided and might need to be rendered)
        this_ctrl_nv, this_ctrl, value_changed = this_page.get_control(ctrl_name, datavalue=this_value, control_type=type, **kwargs)

        this_attribs_str = ''
        for k, v in this_ctrl.attribs.items():
            this_attribs_str = this_attribs_str + ' {}="{}"'.format(k, v)

        buf = '<input name="{}"{} type="{}"{}'.format(
                'theas:' + this_ctrl_nv.name,
                format_str_if(this_ctrl.id, ' id="{}"'),
                this_ctrl_nv.control_type,
                this_attribs_str
            )

        #include value="" attribute, but only if we have a value
        if this_ctrl_nv.value:
            buf += ' value="{}">'.format(this_ctrl_nv.value)
        else:
            buf += '>'

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

        #id should be specified for clarity.  If id is not provided, will try to find the correct control
        #based on name + value
        this_ctrl_nv, this_ctrl, value_changed = this_page.get_control(ctrl_name, datavalue=this_value, control_type='radio', **kwargs)

        this_attribs_str = ''
        for k, v in this_ctrl.attribs.items():
            this_attribs_str = this_attribs_str + ' {}="{}"'.format(k, v)



        buf = '<input name="{}"{} type="{}"{} value="{}"{}>'.format(
            'theas:' + this_ctrl_nv.name,
            format_str_if(this_ctrl.id, ' id="{}"'),
            this_ctrl_nv.control_type,
            this_attribs_str,
            this_ctrl.value,
            ' checked="checked"' if this_ctrl.checked else ''
        )
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



        this_ctrl_nv, this_ctrl, value_changed = this_page.get_control(ctrl_name, datavalue=this_value, control_type='select', options_dict=this_options_dict, **kwargs)

        this_attribs_str = ''
        for k, v in this_ctrl.attribs.items():
            this_attribs_str = ' {}="{}"'.format(k, v)

        buf = '<select name="{}"{}{} >'.format(
            'theas:' + this_ctrl_nv.name,
            format_str_if(this_ctrl.id, ' id="{}"'),
            this_attribs_str
            )

        for temp_optval, temp_optctrl in this_ctrl_nv.controls.items():
            buf = buf + '\n<option value="{}"{}{}>{}</option>'.format(
                temp_optval,
                ' selected="selected"' if ((temp_optctrl.checked) or (not this_ctrl_nv.value and not temp_optval)) else '',
                ' disabled="disabled"' if not temp_optval else '',
                temp_optctrl.caption
            )
        buf = buf + '\n</select>'

        return buf


    @environmentfilter
    def theas_textarea(self, this_env, this_value, *args, **kwargs):
        # This filter is called like:
        #   {{data.EmployerJob.BasicQualifications | theasTextarea(id="basicQualifications", name="ejBasicQualifications", class ="form-control")}}

        this_page = this_env.theas_page

        ctrl_name = None
        if 'name' in kwargs:
            ctrl_name = kwargs['name']

        assert ctrl_name, 'Filter theas_textarea requires either id or name be provided.'

        if ctrl_name.startswith('theas:'):
            ctrl_name = ctrl_name[6:]

        this_ctrl_nv, this_ctrl, value_changed = this_page.get_control(ctrl_name, datavalue=this_value, control_type='textarea', **kwargs)

        this_attribs_str = ''
        for k, v in this_ctrl.attribs.items():
            this_attribs_str = ' {}="{}"'.format(k, v)

        buf = '<textarea name="{}"{}{}>{}</textarea>'.format(
            'theas:' + this_ctrl_nv.name,
            format_str_if(this_ctrl.id, ' id="{}"'),
            this_attribs_str,
            '' if this_ctrl_nv.value is None else this_ctrl_nv.value
        )
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

        this_ctrl_nv, this_ctrl, value_changed = this_page.get_control(ctrl_name, datavalue=this_value, control_type='checkbox', **kwargs)

        this_attribs_str = ''
        for k, v in this_ctrl.attribs.items():
            this_attribs_str = ' {}="{}"'.format(k, v)

        buf = '<input name="{}"{} type="{}"{} value="{}"{}>'.format(
            'theas:' + this_ctrl_nv.name,
            format_str_if(this_ctrl.id, ' id="{}"'),
            this_ctrl_nv.control_type,
            this_attribs_str,
            this_ctrl.value,
            ' checked="checked"' if this_ctrl.checked else ''
        )
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



    def render(self, template_str, data = {}):
        #Call doOnBeforeRender function(s) if provided
        if len(self.doOnBeforeRender):
            for this_func in self.doOnBeforeRender:
                result_template_str, result_data = this_func(self, template_str=template_str, data=data)
                if result_template_str:
                    template_str = result_template_str
                if result_data:
                    data = result_data

        self.authenicator = str(uuid.uuid4())  #set a new authenticator GUID

        # render "function_def" template to define functions
        #if self.template_function_def_str:
        #    function_def_template = self.jinja_env.from_string(self.template_function_def_str)
        #    function_def_template.render()

        # render output using template and data
        this_template = self.jinja_env.from_string(template_str)
        buf = this_template.render(data=data)


        #Call doOnAfterRender function(s) if provided
        if len(self.doOnAfterRender):
            for this_func in self.doOnAfterRender:
                result_template_str, result_data, result_buf = this_func(self, buf=buf, template_str=template_str, date=data)
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
            raise Exception('Error in Theas.create_functions:  ALLOW_UNSAFE_FUCTIONS = False, so this method may not be called.')

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
            #setattr(self, '_unsafe_' + k, mf)

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
                if this_control_nv.name:
                    buf = buf + this_control_nv.name.replace('%', '%25').replace('&', '%26').replace('=', '%3D') + '=' + str(this_control_nv.value).replace('%', '%25').replace('&', '%26').replace('=', '%3D') + '&'
        else:
            # serialize only the controls specified in control_list
            for this_control_nv in control_list:
                if this_control_nv.name:
                    buf = buf + this_control_nv.name.replace('%', '%25').replace('&', '%26').replace('=', '%3D') + '=' + str(this_control_nv.value).replace('%', '%25').replace('&', '%26').replace('=', '%3D') + '&'

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
    '.mp3': 'audio/mpeg',
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

