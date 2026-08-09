"""Which model wrote each beat (PRODUCT.md §19.3).

The Day-5 review had to work out whether the misattributed consequence beats came
from the configured model or the one before it, and had to do it from option
counts and hint phrasing because nothing recorded the answer. The properties this
engine is judged on — does the consequence name the child's own choice, does the
close plant a hook — are model-dependent, and the router has a fallback chain, so
the row has to say who wrote it. Blank on every beat generated before this.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('story', '0004_beat_hints_and_choice_media'),
    ]

    operations = [
        migrations.AddField(
            model_name='beat',
            name='model',
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
