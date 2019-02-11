Theas Dependencies
##################

pymssql
*******

Theas needs to connect to Microsoft SQL Server.

There are two basic approaches available for connecting to Microsoft SQL:

  #. ODBC
    #. Requires a driver to connect a particular data source (such as Microsoft SQL Server) to a intermediate data management library
    #. Pros:  Widely used, support for many types of data sources, support for many client environments

  #. Low-Level TDS (Tabular Data Stream) protocol
    #. Prods:  No drivers required, portable to most client environments, direct high performance connection to the database.
    #. Limited to Microsoft SQL Server (and Sybase), full documentation only recently available
    See:  https://msdn.microsoft.com/en-us/library/dd304523.aspx

Theas was designed to work in conjunction with Microsoft SQL Server.  This decision goes beyond simply saying that we decided to use MSSQL to store some data for Theas.  Rather, we designed Theas to be more of a layer on top of MSSQ>

For the sake of portability, performance, and simplicity, it makes sense for Theas to use TDS, and Theas would not benefit from introduction of ODBC support.

This led to us selecting pymssql as the database library for Theas.

The pymssql package actually has two modules:  the pymssql module, which provides a high-level way of interacting with the database, and the _mssql module, upon which pymssql builds, and which provides a lower-level way of interacting with the database.  Theas uses _mssql directly.  Theas does not use the high-level pymssql module.

pymssql has one main dependencies:  freetds  To make use of encrypted database connections (such as when connecting to a SQL server on Microsoft Azure), freetds depends upon OpenSSL.

pymssql does have several other dependencies, such as Cython, but only freetds directly impacts the database functionality...which is the main feature of pymssql.

To completely build pymssql and all dependencies, the flow is:

  #. Download OpenSSL source
  #. Build OpenSSL
  #. Download freetds source
  #. Build freetds
  #. Download pymssql source
  #. Copy OpenSSL and freetds binaries to appropriate locations within the pymssql package
  #. Build pymssql
  #. Install pymssql

Note that it **is** possible to simply install pymssql with `pip install pymssql`, however doing so will not provide you with the SSL support needed for connecting to certain databases (such as Azure).

Unfortunately, building OpenSSL, freetds and pymssql on Windows is not well documented.  However it is not terribly complex to perform these builds once you have the needed knowledge and tools.  With this documentation, you should have no problem building OpenSSL, freetds, and pymssql without any problems.

Theas is a fairly new platform created in 2016.  As such, it is is designed to run primarily in modern environments:  Windows 7, Windows 10, and Windows Server 2016 and later, against Microsoft SQL 2016 or later (any edition).

Historically Theas has run in other environments (such as Windows Server 2008R2 against MSSQL 2008R2).  Also, Theas has been lightly tested from various Linux environments (Ubuntu, Debian, etc.) and should run fine there too...though most use of Theas has been on Windows.

Theas was originally created in Python 3.4, but has since been updated to run in Python 3.6  All dependencies are expected to be at the latest stable release.  In other words, there is no legacy dead weight:  the current versions of everything should work fine.

I do maintain GitHub forks for all Theas dependencies.  Occasionally I encounter a bug or make an improvement to one of these projects...and then submit a pull request to the master, and then rebase to the master once the pull request has been committed to the master.

Theas was designed to work in conjunction with Microsoft SQL Server.

Why?

  #. Microsoft SQL Server is a comfortable choice for the kinds of mid-sized and large-sized business applications that would be run on Theas
  #. Theas was originally created to work with the OpsStream Rapid Improvement Toolset, which runs on MSSQL
  #. Theas embraces a "data-first" approach that makes good use of the rich capabilities of MSSQL
