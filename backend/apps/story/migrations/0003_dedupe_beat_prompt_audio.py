"""Repair beats whose `interaction.prompt_audio` repeats their narration.

Written before `dedupe_prompt_audio` guarded the write path, so these rows carry
the defect: the field held the whole narration again, which meant the child heard
the entire beat twice in one clip and read it twice on the page.

The audio is dropped along with it, deliberately. `spoken_line` concatenates the
two, so the rendered clip *contains* the duplicate — fixing the row without
dropping the clip would leave a correct caption over a voice that still says
everything twice. A beat with no audio is re-narrated on next play (§18.5), which
costs one TTS call and is the only way the clip becomes right.

Reversible in the only sense that matters: the forward pass is idempotent and the
backward pass is a no-op, because restoring a duplicate is not a thing to want.
"""

from django.db import migrations


def _normalised(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def dedupe(apps, schema_editor) -> None:
    Beat = apps.get_model("story", "Beat")

    repaired = []
    for beat in Beat.objects.exclude(interaction=None).iterator():
        interaction = beat.interaction or {}
        prompt = str(interaction.get("prompt_audio") or "").strip()
        if not prompt:
            continue

        said, asked = _normalised(beat.narration), _normalised(prompt)
        if not (asked in said or said in asked):
            continue

        interaction["prompt_audio"] = None
        beat.interaction = interaction
        # Force a re-render: the stored clip has the duplicate baked into it.
        beat.audio = None
        repaired.append(beat)

    if repaired:
        Beat.objects.bulk_update(repaired, ["interaction", "audio"])


class Migration(migrations.Migration):
    dependencies = [("story", "0002_adventure_beat_choice_and_more")]

    operations = [migrations.RunPython(dedupe, migrations.RunPython.noop)]
