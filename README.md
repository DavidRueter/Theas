![Theas: Business-class web apps](resources/TheasLogo_wTag.jpg?raw=true "Theas")
# Theas

Theas is a full-stack business-class web application solution.

It includes everything you need to build and deploy powerful, secure web applications for business, including: database layer, self-contained web server, server-side scripting, and full HTML5 support with specific examples for both vue.js and jquery.  It currently runs on Python 3.4 or later.  It was primarily written to deploy as Windows service, but also runs under Linux.  It works with Microsoft SQL Server.

1) turnkey performance and security-aware embedded web server (written in Python 3.4, based on Tornado)
2) extensions to Jinja2 templating via custom filters
3) session management
4) session data management and multi-tier data binding
5) database-only content and templates (no filesystem used)
6) mechanism for using template-specific stored procedure to retrieve and update data
7) support for both normal POST / GET requests and async / AJAX request
... and more.

Theas does not impose any restrictions on client-side development:  you are free to use whatever tools and frameworks you like.

See thie Wiki for more information.

This project is actively maintained as of 4/4/2019.  It was created on 4/8/2016.  I have not yet created a sample stand-alone database for Theas, as I have been using Theas with OpsStream (a MSSQL application from http://opsstream.com).

I am happy to help you get your dev environment set up, and to help you get your Theas server working--either with OpsStream or some other database of your choosing.  Let me know if you have questions or if I can be of assistance.

David Rueter
drueter@assyst.com
