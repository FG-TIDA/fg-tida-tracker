from django.db import models


class CaseInsensitiveEmailField(models.CharField):
    """Preserve PostgreSQL citext email identity and existing FK column types.

    Django removed CICharField in 5.1. Retaining the PostgreSQL extension avoids
    changing case-insensitive uniqueness or breaking LIKE lookups with a
    nondeterministic collation. Historical migrations keep their original field.
    """

    def db_type(self, connection):
        return 'citext' if connection.vendor == 'postgresql' else super().db_type(connection)
