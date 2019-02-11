Error Handling
##############

Concepts
********

Error conditions can arise for lots of reasons.  The more complex an application is, the more opportunity there is for errors.  In a multi-tier web application, errors may arise at any point in the technology stack:  in Javascript, HTML, CSS, workstation, network, firewall / load balancer, web server (i.e. Theas python code), database connection, database, business logic, data stored in the database, and more.

Besides programming errors (which are the responsibility of the programmer to avoid), errors may arise due to the environment (network down, disk full, package missing, CPU busy, etc.) or due to user-provided data and commands.

In other words, an "error" does not necessarily mean that the programmer did something wrong.  Instead, an "error" simply means that the system could not complete a task it was asked to complete.

Error messages exist to communicate the nature of the problem, so that the problem can be solved and the desired task completed.

The best scenario for all involved is generally for the user to be able to resolve the problem on their own.
The next best scenario is for the user to understand and be able to communicate specifics of a problem to someone who can help.

Interestingly, these two scenarios really require different kinds of error messages:  in the first case, clear non-intimidating information to the user is important.  In the second case, detailed technical information for the support or programming professional is important.

Front-end errors (HTML, CSS, Javascript, browser, workstation, etc.) are the responsibility of the front-end "view" to catch, manage and report.

Ideally in a controlled environment with good testing and sound code, the web server (Theas python code) and the database server (Microsoft SQL Server) should not create errors:  these layers should be robust and dependable.

The business logic in a Theas application is for the most part coded and executed within SQL (in SQL stored procedures).

For example, suppose a Theas application presents the user with a form.  When the form is submitted, a Theas



