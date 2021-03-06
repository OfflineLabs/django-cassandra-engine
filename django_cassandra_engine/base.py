from itertools import chain

from django.db.backends.signals import connection_created
from djangotoolbox.db.base import (
    NonrelDatabaseClient,
    NonrelDatabaseFeatures,
    NonrelDatabaseIntrospection,
    NonrelDatabaseOperations,
    NonrelDatabaseValidation,
    NonrelDatabaseWrapper
)
from django_cassandra_engine.connection import CassandraConnection
from django_cassandra_engine.creation import DatabaseCreation
from django_cassandra_engine.schema import DatabaseSchemaEditor
from django_cassandra_engine.utils import (
    get_cql_models,
    get_installed_apps,
    CursorWrapper
)


class DatabaseFeatures(NonrelDatabaseFeatures):
    string_based_auto_field = True
    supports_long_model_names = False
    supports_microsecond_precision = True
    can_rollback_ddl = True
    uses_savepoints = False
    requires_rollback_on_dirty_transaction = False
    atomic_transactions = True


class DatabaseOperations(NonrelDatabaseOperations):

    def sql_flush(self, style, tables, sequences, allow_cascade=False):
        """
        Truncate all existing tables in current keyspace.

        :returns: an empty list
        """

        for table in tables:
            qs = "TRUNCATE {}".format(table)
            self.connection.connection.execute(qs)

        return []


class DatabaseClient(NonrelDatabaseClient):
    pass


class DatabaseValidation(NonrelDatabaseValidation):
    pass


class DatabaseIntrospection(NonrelDatabaseIntrospection):

    def __init__(self, *args, **kwargs):

        super(NonrelDatabaseIntrospection, self).__init__(*args, **kwargs)
        self._cql_models = {}
        self._models_discovered = False

    def _discover_models(self):
        """
        Return a dict containing a list of cqlengine.Model classes within
        installed App.
        """

        apps = get_installed_apps()
        for app in apps:
            self._cql_models[app.__name__] = get_cql_models(app)

    @property
    def cql_models(self):
        if not self._models_discovered:
            self._discover_models()
            self._models_discovered = True
        return self._cql_models

    def django_table_names(self, only_existing=False):
        """
        Returns a list of all table names that have associated cqlengine models
        and are present in settings.INSTALLED_APPS.
        """

        all_models = list(chain.from_iterable(self.cql_models.values()))
        tables = [model.column_family_name(include_keyspace=False)
                  for model in all_models]

        return tables

    def table_names(self, cursor=None):
        """
        Returns all table names in current keyspace
        """

        connection = self.connection.connection
        keyspace_name = connection.keyspace
        keyspace = connection.cluster.metadata.keyspaces[keyspace_name]

        return keyspace.tables

    def get_table_list(self, cursor):
        return self.table_names()

    def sequence_list(self):
        """
        Sequences are not supported
        """
        return []

    def get_relations(self, *_):
        """No relations in nonrel database"""
        return []

    def get_table_description(self, *_):
        """
        Unfortunately we can't use `DESCRIBE table_name` here
        because DESCRIBE isn't part of CQL language..
        """
        return ""


class DatabaseWrapper(NonrelDatabaseWrapper):

    vendor = 'cassandra'

    def __init__(self, *args, **kwargs):
        super(DatabaseWrapper, self).__init__(*args, **kwargs)

        # Set up the associated backend objects
        self.features = DatabaseFeatures(self)
        self.ops = DatabaseOperations(self)
        self.client = DatabaseClient(self)
        self.creation = DatabaseCreation(self)
        self.validation = DatabaseValidation(self)
        self.introspection = DatabaseIntrospection(self)

        self.commit_on_exit = False
        self.connected = False
        self.autocommit = True

        del self.connection

    def connect(self):
        if not self.connected or self.connection is None:
            settings = self.settings_dict
            self.connection = CassandraConnection(**settings)
            connection_created.send(sender=self.__class__, connection=self)
            self.connected = True

    def __getattr__(self, attr):
        if attr == "connection":
            assert not self.connected
            self.connect()
            return getattr(self, attr)
        raise AttributeError(attr)

    def reconnect(self):

        if self.connected:
            self.connection.close_all()
            del self.connection
            self.connected = False
        self.connect()

    def close(self):
        pass

    def _commit(self):
        pass

    def _rollback(self):
        pass

    def _cursor(self):
        return CursorWrapper(self.connection.cursor(), self)

    def schema_editor(self, *args, **kwargs):
        """
        Returns a new instance of this backend's SchemaEditor (Django>=1.7)
        """
        return DatabaseSchemaEditor(self, *args, **kwargs)
