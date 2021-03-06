#-*- coding: utf-8 -*-

"""
faitoutlib - the backend library performing the actual work of this project.

 (c) 2013 - Copyright Red Hat Inc.

 Authors:
 - Pierre-Yves Chibon <pingou@pingoured.fr>

 Distributed under License GPLv3 or later
 You can find a copy of this license on the website
 http://www.gnu.org/licenses/gpl.html

 This program is free software; you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation; either version 3 of the License, or
 (at your option) any later version.

 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
 along with this program; if not, write to the Free Software
 Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
 MA 02110-1301, USA.
"""

import random
import string
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import scoped_session
from sqlalchemy.orm.exc import NoResultFound

import model


class FaitoutException(Exception):
    """ Top level exceptions for all the customs exception of Faitout.
    """
    pass


class TooManyConnectionException(FaitoutException):
    """ Exception thrown when the user has requested to many database
    connection within a certain time frame.
    """
    pass


class WrongOriginException(FaitoutException):
    """ Exception thrown when someone has requested to drop a database
    from a different IP than the person that requested it.
    """
    pass


class NoDatabaseException(FaitoutException):
    """ Exception thrown when someone has requested to drop a database
    that does not exist.
    """
    pass


def get_engine(db_url, debug=False, pool_recycle=3600):
    """ Create the engine use to connect to the database.

    :arg db_url: URL used to connect to the database. The URL contains
    information with regards to the database engine, the host to connect
    to, the user and password and the database name.
      ie: <engine>://<user>:<password>@<host>/<dbname>
    :arg debug: a boolean specifying wether we should have the verbose
        output of sqlalchemy or not.
    :return an engine that can be used to connect the database.
    """
    return create_engine(db_url, echo=debug, pool_recycle=pool_recycle)


def create_session(db_url, debug=False, pool_recycle=3600):
    """ Create the Session object to use to query the database.

    :arg db_url: URL used to connect to the database. The URL contains
    information with regards to the database engine, the host to connect
    to, the user and password and the database name.
      ie: <engine>://<user>:<password>@<host>/<dbname>
    :arg debug: a boolean specifying wether we should have the verbose
        output of sqlalchemy or not.
    :return a Session that can be used to query the database.
    """
    engine = get_engine(db_url, debug=debug, pool_recycle=pool_recycle)
    scopedsession = scoped_session(sessionmaker(bind=engine))
    return scopedsession


def get_new_connection(
        session, admin_engine, remote_ip, host, port, max_con=3,
        outformat='text', unlimited=False):
    """ Create a new connection to the database for the specified IP
    address.

    In case the IP address provided is not part of the whitelist and has
    required more than 3 connection over the last X minutes, the method
    will throw a TooManyConnection exception.

    A FaitoutException is thrown if something went wrong at the database
    level.

    :arg session: the session with which to connect to the database.
    :arg admin_engine: the engine with which to connect to the postgresql
        database to create the new database and user.
    :arg remote_ip: the IP address of the user that requested a new
        connection.
    :arg host: the host / ip of the postgresql server to return in the
        database url.
    :arg port: the port of the postgresql server to return in the database
        url.
    :kwarg max_con: specify the maximum number of active connections
        allowed per IP at the same time.
    :kwarg outformat: specify the return format of the connection
        information. At the moment 'text' and 'json' are supported, 'text'
        being the default.
    :kwarg unlimited: a boolean specifying wether this IP address is allowed
        unlimited active connections.
    :raise TooManyConnectionException: if the user requested too many
        connection too quickly.
    :raise FaitoutException: generic exception raised in case of problem.
    :return: a string of the URL to connect to the database if outformat
        is 'text', a dictionnary of the same information if outformat is
        'json'.

    """

    ## Check if user is allowed to ask for a new connection
    if not unlimited \
            and model.Connection.by_ip(
            session, remote_ip, cnt=True) >= max_con:
        raise TooManyConnectionException(
            '%s has already 3 active connection, please re-try later' %
            remote_ip
        )

    ## Generate user
    user = string_generator(20)
    ## Generate password
    password = string_generator(40)
    ## Generate database name
    db_name = string_generator(15)

    connection = model.Connection(user, password, db_name, remote_ip)
    session.add(connection)
    try:
        session.commit()
    except Exception as err:  # pragma: no cover
        session.rollback()
        print >> sys.stderr, err
        raise FaitoutException(
            'An error has occured, please contact the administrator'
        )

    ## Create database, user and grant permission
    try:
        create_database(admin_engine, db_name, user, password)
    except Exception as err:  # pragma: no cover
        print >> sys.stderr, err
        raise FaitoutException(
            'An error has occured, please contact the administrator'
        )

    info = {
        "dbname": db_name,
        "username": user,
        "password": password,
        "port": port,
        "host": host,
    }
    if outformat == 'json':
        return info
    else:
        return 'postgresql://%(username)s:%(password)s@' \
               '%(host)s:%(port)s/%(dbname)s' % info


def string_generator(length=15):
    """ Return a randomly generated string of lower and upper ASCII
    characters and number.

    :kwarg length: the length of the string to return

    """
    chars = string.ascii_uppercase + string.digits + string.ascii_lowercase
    return ''.join(random.choice(chars) for x in range(length))


def create_database(admin_engine, db_name, username, password):  # pragma: no cover
    """ Using the with the provided engine, create a new database with the
    specified name, create a new database user with the specified username
    and password and set this user as admin of this database.

    :arg admin_engine: the engine used to connect to the database
    :arg db_name: the name of the database to create
    :arg username: the name of the user to create
    :arg password: the password of the user to create

    """
    conn = admin_engine.connect()
    try:
        try:
            conn.execute("commit")
        except:
            pass
        conn.execute("CREATE USER \"%s\" WITH PASSWORD '%s';" %
                     (username, password))
        conn.execute("commit")
        conn.execute('create database "%s";' % db_name)
        conn.execute("commit")
        conn.execute("GRANT ALL PRIVILEGES ON DATABASE \"%s\" to \"%s\";" %
                     (db_name, username))
    finally:
        conn.close()


def clean_connection(session, admin_engine, remote_ip, db_name):
    """ Drop all the table of the specified database.

    In case the IP address provided is not the IP that requested the
    connection a WrongOriginException exception is thrown.

    A FaitoutException is thrown if something went wrong at the database
    level.

    :arg session: the session with which to connect to the database.
    :arg admin_engine: the engine with which to connect to the postgresql
        database to create the new database and user.
    :arg remote_ip: the IP address of the user that requested a new
        connection.
    :arg db_name: the name of the database to drop.
    :raise NoDatabaseException: thrown when the database name provided
        could not be associated with any database.
    :raise WrongOriginException: if the user requested to drop the db from
        a different IP than the user asking for the db.
    :raise FaitoutException: generic exception raised in case of problem.
    :return: a string of the URL to connect to the database if outformat
        is 'text', a dictionnary of the same information if outformat is
        'json'.

    """
    try:
        connection = model.Connection.by_db_name(session, db_name)
    except NoResultFound:
        raise NoDatabaseException(
            'Database %s could not be found' % db_name)

    if connection.connection_active is False:
        raise NoDatabaseException(
            'No active database named %s could be found' % db_name)

    if connection.connection_ip != remote_ip:
        raise WrongOriginException(
            '%s did not request this database and thus is not allowed to '
            'clean it.' % remote_ip)

    try:
        clean_database(admin_engine, db_name)
    except Exception as err:  # pragma: no cover
        print >> sys.stderr, 'ERROR while cleaning\n', err
        raise FaitoutException(
            'An error has occured, please contact the administrator'
        )

    return 'Database %s has been cleaned' % db_name


def clean_database(admin_engine, db_name):  # pragma: no cover
    """ Using the provided engine, drop all tables ofthe specified database.

    :arg admin_engine: the engine used to connect to the database
    :arg db_name: the name of the database to clean

    """
    conn = admin_engine.connect()
    try:
        try:
            conn.execute("commit")
        except:
            pass
        conn.execute("SELECT pg_terminate_backend(pg_stat_activity.pid)"
                     " FROM pg_stat_activity"
                     " WHERE pg_stat_activity.datname = '%s'"
                     " AND pid <> pg_backend_pid();" % db_name)
        conn.execute("commit")
        conn.execute('drop database "%s";' % db_name)
        conn.execute("commit")
        conn.execute('create database "%s";' % db_name)
        conn.execute("commit")
    finally:
        conn.close()


def drop_connection(session, admin_engine, remote_ip, db_name):
    """ Drop the specified database and the user associated with it.

    In case the IP address provided is not the IP that requested the
    connection a WrongOriginException exception is thrown.

    A FaitoutException is thrown if something went wrong at the database
    level.

    :arg session: the session with which to connect to the database.
    :arg admin_engine: the engine with which to connect to the postgresql
        database to create the new database and user.
    :arg remote_ip: the IP address of the user that requested a new
        connection.
    :arg db_name: the name of the database to drop.
    :raise NoDatabaseException: thrown when the database name provided
        could not be associated with any database.
    :raise WrongOriginException: if the user requested to drop the db from
        a different IP than the user asking for the db.
    :raise FaitoutException: generic exception raised in case of problem.
    :return: a string of the URL to connect to the database if outformat
        is 'text', a dictionnary of the same information if outformat is
        'json'.

    """
    try:
        connection = model.Connection.by_db_name(session, db_name)
    except NoResultFound:
        raise NoDatabaseException(
            'Database %s could not be found' % db_name)

    if connection.connection_active is False:
        raise NoDatabaseException(
            'No active database named %s could be found' % db_name)

    if connection.connection_ip != remote_ip:
        raise WrongOriginException(
            '%s did not request this database and thus is not allowed to '
            'drop it.' % remote_ip)

    try:
        drop_database(admin_engine, db_name, connection.connection_user)
    except Exception as err:  # pragma: no cover
        print >> sys.stderr, 'ERROR while dropping\n', err
        raise FaitoutException(
            'An error has occured, please contact the administrator'
        )

    connection.connection_active = False
    try:
        session.commit()
    except Exception as err:  # pragma: no cover
        session.rollback()
        print >> sys.stderr, err
        raise FaitoutException(
            'An error has occured, please contact the administrator'
        )

    return 'Database %s has been dropped' % db_name


def drop_database(admin_engine, db_name, username):  # pragma: no cover
    """ Using the provided engine, drop the specified database and user.

    :arg admin_engine: the engine used to connect to the database
    :arg db_name: the name of the database to drop
    :arg username: the name of the user to drop

    """
    conn = admin_engine.connect()
    try:
        try:
            conn.execute("commit")
        except:
            pass
        conn.execute("SELECT pg_terminate_backend(pg_stat_activity.pid)"
                     " FROM pg_stat_activity"
                     " WHERE pg_stat_activity.datname = '%s'"
                     " AND pid <> pg_backend_pid();" % db_name)
        conn.execute("commit")
        conn.execute('drop database "%s";' % db_name)
        conn.execute("commit")
        conn.execute('drop user "%s";' % username)
        conn.execute("commit")
    finally:
        conn.close()


def get_stats(session):
    """ Retrieve some statistics about the current usage of faitout.

    :arg session: the session with which to connect to the faitout database.

    """
    output = {}

    output['total_connections'] = model.Connection.search(
        session, cnt=True)
    output['active_connections'] = model.Connection.search(
        session, active=True, cnt=True)
    output['unique_ip'] = model.Connection.cnt_unique_ip(session)

    return output
