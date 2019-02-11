Introduction to Theas
#####################


What is Theas?
**************

Theas is a turn-key web application server and development platform for building database-driven web applications.

You can think of Theas being kind of like Wordpress, but for building web applications (not websites), and providing a built-in web server (not requiring Apache or nginx).

Theas is a server-side web application framework that provides:

    #. turnkey performance and security-aware embedded web server
    #. extensions to Jinja2 templating via custom filters
    #. session management
    #. session data management and multi-tier data binding
    #. database-only content and templates (no filesystem used)
    #. mechanism for using template-specific stored procedure to retrieve and update data
    #. support for both normal POST / GET requests and async / AJAX request ... and more.
   
.. note::   
    Theas does not impose any restrictions on client-side development: you are free to use whatever tools and frameworks you like.

Theas is built in Python (currently 3.6), using Tornado for the web server, Jinja2 for templating, and pymssql for MS SQL connectivity. Theas was built to work with Microsoft SQL, and in particular, the SQL-based OpsStream business execution platform, but can be used with other database platforms as well.

Overview
********

Theas (Greek word for goddess) is a server-side web application framework for building database-driven web applications to be used by authenticated users. Theas is not designed for public facing websites.

Theas is written in Python, but you don’t need to write Python code to use Theas. You need only write standard HTML / CSS / JavaScript, incorporate the Jinja2 templating syntax in your HTML, and write stored procedures that return data to the HTM templates and process data submitted by HTML pages.

With Theas, no file-system based content is used. All content served up by Theas comes from a database. This includes HTML templates, and any other resources such as CSS and JS files, images, and the like that you choose to allow Theas to manage.

Theas provides its own self-contained web server (built on top of Tornado, a popular Python package). Theas does not require (or support) use of Apache, Nginx, IIS, or any such general-purpose web server.

Theas is cross-platform: It can be run from Windows, Linux or OS X, but it was written primarily to run on Windows.

Theas can be extended to use any database engine, but it was written to use Microsoft’s MS SQL. At present this is the only database engine supported natively without modifying Theas code.

Though Theas is a valuable framework for web application development on its own, it was developed as a front end for the commercially-available OpsStream business execution platform. When paired with OpsStream, Theas can provide a great deal of functionality including dynamic database schema modification (configured from a browser), robust business process execution / workflow capabilities, and comprehensive role-based security.


Getting started
***************

Theas requires a handful of configuration settings be set. These are stored in the file settings.cfg but they can also be passed in as command-line parameters when Theas is launched. Settings include things like the HTTP port Theas is to use and the database connection information.

Theas can be run as an application (i.e. a console application) or as a Windows service.

No installation is required: Theas can be launched directly from any folder.

Multiple instances of Theas can be run simultaneously—connected either to different databases, or connected to the same database. Each Theas instance must run on its own HTTP port.

For simplicity I recommend that each instance of Theas be run in its own folder with its own settings.cfg file. (In other words, make a copy of the whole Theas folder and edit the settings.cfg file to run a second instance of Theas.) I also recommend that you rename the Theas.exe file to something specific, such as theas_MyApplication.exe Doing so allows you to see the Theas process clearly in task manager (and service manager, if running as a service).

I typically run multiple Theas instances on the same server, each supporting a different web application and each connected to a different database, and each on its own HTTP port. I then typically run a reverse proxy in front of the Theas instances to route requests to specific domains and host names on standard HTTP / HTTPS ports to the correct Theas HTTP port.

Theas expects that SSL / HTTPS will be provided by the reverse proxy. Theas does not natively support or require SSL.

Theas includes a simple MS SQL database. This can be run on any version of MS SQL (SQL 2005 or later), and can be run on any edition including the free MS SQL Server Express, and including hosted SQL from Microsoft Azure or other provider.

.. note::
    I encourage you to consider running the commercially-available OpsStream business execution platform in lieu of the simple database provided with Theas. OpsStream provides a turnkey environment for robust, complex, secure, enterprise-class applications.

    But Theas can be used as a free, OSS (open-source software) platform without any dependencies on OpsStream when the included Theas database is run on the free edition of Microsoft SQL Server Express.

*******
About the name
*******

Why is this project named “Theas”? Theas is the Greek work for "goddess".

According to the Jinja2 documentation at http://jinja.pocoo.org/docs/dev/faq/#why-is-it-called-jinja:

"""
The name Jinja was chosen because it’s the name of a Japanese temple and temple and template share a similar pronunciation.
"""

Theas was designed to work with and empower Jinja to do more.

If Jinja is a temple, Theas (goddess) seems like an appropriate name to convey the relationship between Jinja and Theas.

Theas not only controls Jinja, but provides power to Jinja. Theas is the goddess of the temple.


Architecture
************

Database Overview
=================

Theas is designed to work with Microsoft SQL (version 2008R2 or later). More specifically, Theas was designed with the MSSQL-based OpsStream (http://opsstream.com) business process execution system in mind--but Theas can be run with or without OpsStream.

Theoretically, Theas can work with any database environment of your choosing with only minor modification.

Theas directly calls 7 required stored procedures:

    #. theas.spInitSession
    Purpose: Get SQL statements needed to run on a new SQL connection (i.e. for a new session) to initialize things.
    -> sws.spInitSession
    
    #. theas.spAuthenticateUser
    Purpose: Verify credentials (username/password) for a user login, and if successful establish a new application session.
    -> opsstream.spactAuthenticateUser
    
    #. theas.spGetWebResources    
    Purpose: Get web resources, such as HTML templates, static HTML blocks, javascript and CSS files, logos, etc.
    -> opsusr.spapiGetSysWebResources
    
    #. theas.spGetAttachments
    Purpose: Get a specific blob attachments to a data record.
    -> opsstream.spgetQuestAttachments
    
    #. theas.spInsFiles
    Purpose: When processing uploaded files, insert the files received from the client browser into a temporary SQL table. After that, a normal stored procedure (such as an APIStoredProc referenced in the web resource record) can take responsibility for processing these and permanently storing them as required.
    -> sws.spinsFiles2
    
    #. theas.spUpdSessionData
    Purpose: Non-essential, but provides a way to save transitory data to a user session. (Generally the TheasServer keeps session data in memory as long as the session is active. Writing session data to the database is needed only if you need session data to survive a TheasServer restart.)
    -> opsstream.spudSessionData
    
    #. theas.spGetSessionData
    Purpose: Non-essental, but provides a way to retrieve transitory session data stored in the database by theas.spUpdSessionData.
    -> opsstream.spgetSessionData
    
All other stored procedures are web resource-specific. These are created to return data needed by a particular page, process HTTP posted data from a particular page (API StoredProc) or in the case of API AsyncStoredProc to process async (i.e. AJAX) requests from the browser.


Jinja Environment
=================

The Jinja templating engine uses an "environment" that is often instantiated as a global environment used for rendering all templates in an application.

Theas instantiates a separate Jinja environment for each session. The reasons for this are:

Theas implements certain Jinja custom filters that need to have access to the Theas session. This means that the Jinja templating engine needs to pass the Theas session to the custom filter somehow.

This could be done through two different means: Have each filter look in the template context's data for a session token, and then look up the session in the global list of Theas sessions, or to directly store the Theas session in the Jinja environment.

The former approach is ugly, as it requires several lines of code to retrieve the session in each filter, requires that an environment global be set pointing to the global list of sessions (i.e. environment.globals['TheasSessionList']=xxx), puts more load on the global session list and provides more opportunity for locking problems, and has to trust data in the template rather than python code for the execution of filters.

The later approach (of storing the session in the environment) eliminates all these problems. Furthermore, the cost of instantiating a new environment for each session is relatively small, and also provides the additional benefit of allowing custom functions to be added while the server is running.

So in Theas, the Jinja environment is handled kind of like the SQL connection: there is one environment per session, and it is used for the duration of the session.


UX Flow
=======

Page Lifecycle
==============
Theas Controls


Using Theas
***********

Working with Forms
==================

Async Requests
==============

Resources
=========

Request Lifecycle
=================

Error Handling
==============

Client-side Javascript
======================

Controlling Navigation
======================


Session Management
******************

One essential aspect of a web application is “session state management”. HTTP is sessionless: the browser makes a request, the web server sends a response, end of story. There is nothing in HTTP that ties a subsequent request together with the first, and nothing that manages a “session” of interactions with the same user within a period of time. Sessions often involve authentication (logging in with a username and password, for example). Sessions always involve preserving some data between multiple requests.

Theas does a number of things to take care of session state management for your applications.

Establishment of a session
--------------------------

When a browser makes a request to Theas, the Theas server first checks for the presence of a SessionToken sent by the browser. Theas looks for the session token in a request argument named theas:th:ST (either POST or GET). If it doesn’t find a session token there, Theas then looks in a cookie named theas:th:ST.

If Theas cannot find a SessionToken and a corresponding valid session in memory at the Theas server to go along with it, Theas automatically creates a new session and sends the new SessionToken to the browser along with the rest of the response.

If Theas does find a valid session in memory at the Theas server corresponding to the SessionToken from the browser, Theas retrieves that session and locks it for the duration of the processing of the request. During this time the server can access built-in Theas information and functionality, the values of Theas controls created by the web application, and a persistent SQL connection dedicated to this session.

Authentication
--------------

There are several ways the developer can use authentication. It might be that some pages should be viewable by public, non-authenticated users, while some pages might require authentication to view.

Each Theas Web Resource (a definition of a page, including HTML template, SQL stored procedure references, and various other settings) may indicate that a page RequiresAuthentication.

If a resource is flagged as RequiresAuthentication = true, and the session of the requestor has not been authenticated, the Theas server will serve up the login screen in lieu of the requested resource. Once the login screen is submitted with a valid username and password, the Theas server will automatically serve up the originally-requested resource.

The user will remain logged in at least for the duration of the session. The timeout value for the session can be configured in the settings.cfg file.

```
session_max_idle_minutes = 60
# help="Maximum idle time (in minutes) that user sessions will remain active", type=int`
```

But Theas also supports a “remember me” capability, in which a user token gets stored in a browser cookie in addition to the session token. In this way, if a browser makes a request and there is not a valid session (such as the original session has timed out and purged), Theas will establish a new session…and will immediately log the stored user into the session without requiring credentials to be provided.

Obviously the “remember me” capability does increase the risk of authenticated resources being served by individuals other than the authorized users. But this flag does increase convenience, and is helpful in some cases.

The “remember me” capability is turned off by default, but can be enabled by editing the settings.cfg file:

```
remember_user_token = True
#help="Save the user token in a cookie, and automatically log user in on future visits.", type=bool
```

Separately, there are really two types of resources. A resource flagged as IsPublic means that the resource data stored in Theas can be directly served up to anyone who requests it, without Theas performing any processing or authentication. These “public” resources are generally things like javascript libraries, logos, CSS files, and other such things that can be served up directly without processing any templates or performing other processing.

Public resources are cached in-memory in the Theas web server. This allows them to be served up very quickly. But this also means that you should use common sense when having Theas serve up public resources: serving up huge files or large numbers of files can use more than a reasonable amount of memory in the server.

Usually resources that are not flagged as IsPublic are flagged as RenderJinjaTemplate. In this this case Theas loads the resource template (and caches it in-memory in the Theas web server), but for each request from the browser will pass the template and data to Jinja for processing of the response.

Note that other combinations of these flags is possible. IsPublic = false and RenderJinjaTemplate = false means that the resource stored procedure WILL be called, but the value returned by the stored procedure in the Content field of the General resultset (data.General.Content) will be returned as the response to the browser’s request (with no processing by Theas).

If IsPublic = true and RenderJinjaTemplate = true, the IsPublic = true flag would take priority, and the resource would be served up directly without template processing occurring. This combination has no useful purpose. To avoid confusion, RenderJinjaTemplate never be set to true if IsPublic is set to true. There is no way to render a jinja template for a public resource. (But you can render a Jinja template for a non-authenticated user.)

SQL Connection management
-------------------------

Theas works with Microsoft SQL Server. Resources are stored in the database, and many resource records contain references to stored procedures that are executed when processing requests for these resources.

Most web servers that interact with a SQL database will establish a SQL connection when a request is received from the browser. When the response is sent, the SQL connection is either closed, or is returned to a pool of idle connections that can be used for a future request.

Theas takes a different approach. Each session will establish a SQL connection. (The actual establishment of the connection is deferred until Theas actually needs the database.) when a request is received from the browser, Theas retrieves the existing session (if present) along with the SQL connection. That SQL connection is used for all requests in that session.

This approach of session-centric SQL connection management provides the benefits of connection pooling (i.e. reusing a SQL connection for multiple requests), but additionally provides the benefit of being able to easily manage session state in SQL: information stored in the SQL connection (such as CONTEXT_INFO) is available to each SQL call made. This provides a way for SQL to securely know the authenticated user on each call, without having to have the web server pass in this information with each call.

Theas Controls (aka Theas Params)
---------------------------------

Theas provides a simple way to set server-side session-scoped variables. This can be done from a SQL stored procedure, a tag in an HTML template, a JavaScript call, or an HTTP form post.

When defined using a template tag, these session-scoped variables are called Theas Controls, because the tag instructs the templating engine to actually render HTML elements that retrieve and/or update these variables. Internally within the Theas server source code itself and within stored procedures called by Theas, these variables are called Theas Params. (In other words Theas Controls and Theas Params really refer to the same thing: a set of session-scoped variables. This documentation will refer to these as Theas Params.)

The neat thing about Theas Params is that they are automatically kept in sync across the whole application stack: Whether in HTML, JavaScript, SQL, or internally in the Theas server, values stored in Theas Params can be retrieved and updated.

For example, an HTML form could define a Theas Param (via naming convention). When the form is submitted, Theas will store the value of that Theas Param. If the Theas server then calls a stored procedure for this session, all Theas Params for the session will automatically be passed into the stored procedure. The net effect is that the stored procedure can access the values that were stored by the HTTP form post…with zero coding involved.

Conversely, a stored procedure could update a Theas Param, and JavaScript code could read that updated value, again with zero coding involved.

Theas Params can be created in any of the following ways:

Using a special filter command inside an HTML template processed by Theas {{ "__th"|theasInput(name="theas:Test1", class="form-control", placeholder="just a test field", id="test1", size="10", maxlength="25") }}
Make a javascript call to th.get()
Manually create an HTML form field where the name starts with ‘theas:’
Set the parameter value from a SQL stored procedure and return as part of TheasParams
Have JavaScript make an async call to a Theas resource...in which the SQL stored proc can update the parameter value as above, and in which the async response can automatically have a payload that will automatically update the JavaScript / HTML copies of these parameters.

Files / Binary data
-------------------

The Theas server can receive HTTP form posts containing files. When received, the Theas server automatically inserts the data received into an internal temporary SQL table. In this way, the Theas resource stored procedures have direct access to both metadata about the file and the actual binary data itself.


URL Routing
***********

A website is generally comprised of a number of HTML pages. Each page generally has one or more link to a different page. These links are generally a URL to a specific location, and may either be absolute (and thus contain the complete URL beginning with HTTP://) or relative to the site (containing no leading slash).

So a page named Page1 might have a link to Page2 like this: <a href=”Page2.html”>Go to Page 2</html>

When the link is clicked, the browser navigates to Page2.

Or, in the case of an absolute link: <a href=”http://someothersite.com/somepage.html”>Visit another site</html>

When the link is clicked, the browser navigates to somepage.html from the specified site. In this way, the determination of what page the browser is to display is explicitly determined by the URL included in the href attribute.

But consider what happens when someone visits a website without specifying a particular page. For example, what if a user opened a new browser tab and went to http://www.mysite.com … what page should be shown? This URL does not include an indication of the page. (A URL that indicates a specific page would be like http://www.mysite.com/Page3.html ... where Page3.html is the specific page on the server located at www.mysite.com)

When a specific page is not requested by the browser, the web server needs to decide what to serve up. Normally, for a public-facing website, the web server has a list of pages to try to serve if the page is not specified in the URL. For example, the web server at www.mysite.com might be set up to look for default pages in the following order:

Index.html
Index.htm
Default.html
Default.htm
Thus when a request comes in to the server without a page specified, the server will look to see if there is a document Index.html on the server. If so, it serves it up. If not, it goes on to Index.htm: if it exists, it serves it up. If not, it goes on to Default.html… and so on.

If no document is found to match any of the default pages to be searched, the web server may respond with a general hard-coded “welcome” page for the server. Some servers may display a list of links to available documents. Some servers may return an error, or redirect the browser to a different site.

The point is this: even in the case of a simple website that uses only static HTML pages, there are times when the browser doesn’t specify a specific page to serve. In these cases the web server needs to decide what page to serve.

With dynamic web sites and web applications, determining what content to send to the browser is even more challenging. Instead of simply depending on hard-coded URLs in href attributes, data from a database and/or logic in the application may instead dictate what should be served.

Consider a simple web application that has a login screen. If a browser visits that site and the user is not logged in, the user should be served the login screen. But if a browser visits that site (same URL) and the user IS logged in, the user should be served the secure page instead of the login screen.

Things become even more complex when other business logic in the application needs to determine what content should be served to the browser. And add to that the complexity of user-initiated navigation to specific pages such as through a menu: it could be that the user’s click should influence what is shown, but that the application needs to make a decision in view of that click.

For example, suppose that when a user logs in the system checks to see if the user’s password is about to expire. If the password is about to expire, the system should serve up the change password screen. Otherwise, the system should serve up Page1.

Then in Page1, the user clicks on a link to go to AdvancedFeature1. The system looks at the user’s account, and determines whether the user has permissions to go to that page. If the user does have permission, the system should serve up AdvancedFeature1. If the user does not have permission, the system should serve up either an error page or a page explaining how the user can upgrade to unlock this feature.

This whole business of determining what content to actually send to the browser gets complicated in a hurry. This work of determining what content to serve is called “routing”, because the application needs to determine what page to route the user to.

Some places with information that may influence routing decisions include:

Static HTML links
Links stored in a database
Web server business logic
Client-side (javascript) business logic
Session state
Database state
Server state
…and more
Since there are so many different factors that can influence routing decisions, it can get confusing keeping things straight.

For example, if code in the web server application says to go to Page8 but javascript in the browser explicitly requests Page9 which one is right? Which content should be served up?

To deal with this complexity web application developers sometimes use 3rd party routing libraries. For example, Microsoft provides URL routing in ASP.net (see https://docs.microsoft.com/en-us/aspnet/web-forms/overview/getting-started/getting-started-with-aspnet-45-web-forms/url-routing )

The basic idea is that when the browser requests a URL, the URL is parsed into segments according to patterns defined in a route table. The route table may have placeholders to indicate variable data embedded in the URL. Microsoft’s documentation uses this example: routes.MapPageRoute( "ProductsByCategoryRoute", "Category/{categoryName}", "~/ProductList.aspx" ); Category/{categoryName}

This statement adds a route named ProductsByCategoryRoute. This route will apply if the URL from the browser matches Category/xxx. If the route applies, xxx will be taken to be the value of categoryName, and then the web server will load the ProductList.aspx page and will pass into it the categoryName. The output from processing of ProductList.aspx will be sent to the browser.

If the browser requested a URL of http://mysite/Category/abc the router would break this URL down into segments and would understand that the Category route should be taken, and that abc is the value of the categoryName variable… and the actual content would be generated by the HTML and code in ProductList.aspx. In summary, routing is simply the means by which a URL is mapped to the page that should generate content for the response

Traditional web sites and many web applications use pages. For example, the browser might make a request to a URL, and the web server then serves up one page of information that is displayed in the browser. A link is clicked, and the browser requests a different URL and the web server then serves up a different page of information.

An alternate approach that is sometimes used in modern web applications and some web sites is a “single page application”. This means that the browser makes a request to a URL, and the web server then serves up one page (as normal). This page is made up of HTML, CSS, and JavaScript like any other web page. The difference is that the JavaScript in this page is actually an application that runs that updates things in the browser’s window.

For example, this application might display some menu buttons. When a button is clicked, instead of the browser navigating to a different URL, no request is sent to the web server at all. Instead, the JavaScript application that is running simply take appropriate action based on the click, and updates the window accordingly. Some people think that SPA’s are a good thing and think that they provide a better user experience. Some people think that they are a bad thing, saying that they are more complicated to create, harder to support, face challenges with search engines, don’t work natively with the browser’s “back” button, and don’t deliver as good of a user experience.

In a single-page application, there aren’t really “pages” to route to. (I guess it would be possible to have a JavaScript router load pages from URLs and make these available inside a SPA…but this doesn’t seem to be too useful.) Instead, the JavaScript app would simply update the browser window with new content as needed. If using Vue.js (a JavaScript library for updating the browser window) you could use a router to route URLs to Vue “components”. Each Vue component would be responsible for generating content for the view indicated by the route.

