# Copyright (2016-2017) Hewlett Packard Enterprise Development LP.
# Copyright (2016-2017) Universidade Federal de Campina Grande
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from __future__ import with_statement

from logging.config import fileConfig

from alembic import context
from neutron_lib.db import model_base
from oslo_config import cfg
from oslo_db.sqlalchemy import session
import sqlalchemy as sa
from sqlalchemy import event

from neutron.db.migration.alembic_migrations import external
from neutron.db.migration.models import head  # noqa

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
neutron_config = config.neutron_config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = model_base.BASEV2.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.

MYSQL_ENGINE = None
ONEVIEW_VERSION_TABLE = 'oneview_alembic_version'


def set_mysql_engine():
    try:
        mysql_engine = neutron_config.command.mysql_engine
    except cfg.NoSuchOptError:
        mysql_engine = None

    global MYSQL_ENGINE
    MYSQL_ENGINE = (mysql_engine or
                    model_base.BASEV2.__table_args__['mysql_engine'])


def include_object(object, name, type_, reflected, compare_to):
    if type_ == 'table' and name in external.TABLES:
        return False
    else:
        return True


def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    set_mysql_engine()

    kwargs = dict()
    if neutron_config.database.connection:
        kwargs['url'] = neutron_config.database.connection
    else:
        kwargs['dialect_name'] = neutron_config.database.engine
    kwargs['include_object'] = include_object
    kwargs['version_table'] = ONEVIEW_VERSION_TABLE
    context.configure(**kwargs)

    with context.begin_transaction():
        context.run_migrations()


@event.listens_for(sa.Table, 'after_parent_attach')
def set_storage_engine(target, parent):
    if MYSQL_ENGINE:
        target.kwargs['mysql_engine'] = MYSQL_ENGINE


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    set_mysql_engine()
    engine = session.create_engine(neutron_config.database.connection)

    connection = engine.connect()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        version_table=ONEVIEW_VERSION_TABLE,
    )

    try:
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.close()
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
