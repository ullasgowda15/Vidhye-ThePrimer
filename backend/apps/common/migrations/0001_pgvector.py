"""Enable the pgvector extension.

Memory embeddings live in the same Postgres as everything else (PRODUCT.md
§16.5), so the extension has to exist before any `vector` column is created on
Day 8. Doing it here — in the first app to migrate — means a clean database is
ready before the memory tables land.
"""

from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    initial = True

    dependencies: list = []

    operations = [
        VectorExtension(),
    ]
