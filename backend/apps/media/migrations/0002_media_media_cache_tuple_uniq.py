"""One `Media` row per child per cache tuple (PRODUCT.md §16.6, §22.4).

Nothing locked the window between the cache read and the row write, so two
workers rendering the same beat each wrote a row. The constraint settles it in
the database instead.

Any database written before this migration may already hold such a pair, and
`AddConstraint` would simply refuse — so the duplicates are collapsed first,
keeping the oldest row of each group. The rows removed are by definition
redundant copies of a row that is kept, and their storage object is
content-addressed and shared, so nothing a child can see goes away.
"""

from django.db import migrations, models


def drop_duplicate_tuples(apps, schema_editor):
    Media = apps.get_model("media", "Media")
    seen: set[tuple] = set()
    doomed: list = []

    for row in Media.objects.order_by("created_at", "id").values_list(
        "id", "child_id", "type", "style_hash", "character_hash", "scene_hash"
    ):
        key = row[1:]
        if key in seen:
            doomed.append(row[0])
        else:
            seen.add(key)

    if doomed:
        Media.objects.filter(id__in=doomed).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0001_initial"),
        ("profiles", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(drop_duplicate_tuples, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="media",
            constraint=models.UniqueConstraint(
                fields=("child", "type", "style_hash", "character_hash", "scene_hash"),
                name="media_cache_tuple_uniq",
            ),
        ),
    ]
