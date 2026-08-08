"""What branching needs on the row (PRODUCT.md §8.5).

`next_hint` and `continuity_refs` were generated from Day 4 and thrown away —
there was nowhere to put them, and nothing read them within a single beat. The
choice loop reads them across *two requests*: the beat leaves a steer behind, the
child taps, and the consequence is written against what the beat before it meant
to happen. A hint that lives only in a response object cannot survive that.

`choice_media` and `Choice.value` are the other half: the picture on each option's
card (§14.4) and what the child actually answered (§18.6).

The unique constraint is the one thing here that changes behaviour rather than
capacity — see `Choice`'s docstring on why a double-tap must not advance the story
twice.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('story', '0003_dedupe_beat_prompt_audio'),
    ]

    operations = [
        migrations.AddField(
            model_name='beat',
            name='choice_media',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='beat',
            name='continuity_refs',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='beat',
            name='next_hint',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='choice',
            name='value',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name='choice',
            constraint=models.UniqueConstraint(fields=('beat',), name='choice_beat_uniq'),
        ),
    ]
