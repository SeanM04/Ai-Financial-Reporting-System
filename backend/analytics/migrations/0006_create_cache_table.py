"""Create the database cache table used for rate limits, AI quota counters and locks.

Running it from a migration means `python manage.py migrate` is enough on a new
server. It does nothing when the cache is Redis (REDIS_URL set).
"""

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command('createcachetable', database=schema_editor.connection.alias, verbosity=0)


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0005_prompt_module_traceability'),
    ]

    operations = [
        migrations.RunPython(create_cache_table, migrations.RunPython.noop),
    ]
