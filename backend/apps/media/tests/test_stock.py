"""The safe stock illustrations (PRODUCT.md §13.2's `IMGFB`).

What has to be true of a picture that stands in for a refused one: it exists, it
is servable, it is *ours* (hand-drawn, in the repository, not produced by the
pipeline that just failed), it is stable for a given beat, and it costs a provider
call zero times.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.media import service as media_service
from apps.media import stock
from apps.media.models import Media, MediaType, SafetyStatus

pytestmark = pytest.mark.django_db


def test_every_named_stock_file_exists_and_is_a_picture() -> None:
    """The pool is a named list, not a glob: adding a file is not the same act as
    putting it in front of a child. This is the other side of that — a name with
    no file behind it would fail only at the moment it was needed."""
    for name in stock.STOCK_NAMES:
        data = stock._read(name)
        assert data.lstrip().startswith(b"<svg")
        assert b"<script" not in data.lower(), "stock art must not carry script"


def test_a_substitution_is_stored_servable_and_labelled(child: Any) -> None:
    media = stock.safe_illustration(child, seed="beat-1")

    assert media.type == MediaType.IMAGE
    assert media.safety_status == SafetyStatus.PASSED
    assert media.is_servable
    assert media.url.endswith(".svg")
    # Its own provider label, so "how often did we substitute" is one query and
    # so stock art can never be counted as a render in a cost report (§22.4).
    assert media.provider == stock.PROVIDER
    assert stock.is_stock(media)


def test_the_same_beat_always_gets_the_same_picture(child: Any) -> None:
    first = stock.safe_illustration(child, seed="beat-abc")
    second = stock.safe_illustration(child, seed="beat-abc")

    assert first.id == second.id
    assert Media.objects.filter(provider=stock.PROVIDER).count() == 1


def test_different_beats_are_not_all_the_same_meadow(child: Any) -> None:
    """Three substitutions in a row should not read as one broken picture."""
    chosen = {stock.choose(f"beat-{index}") for index in range(40)}

    assert chosen == set(stock.STOCK_NAMES)


def test_a_substitution_never_calls_a_provider(child: Any) -> None:
    """The bytes are already on disk. The pipeline still scans and stores them —
    what it must not do is pay anyone."""
    media_service.STATS.reset()

    stock.safe_illustration(child, seed="x")

    assert media_service.STATS.by_provider == {stock.PROVIDER: 1}
    assert set(media_service.STATS.by_provider) & {"local-placeholder", "cloudflare"} == set()


def test_a_second_child_reuses_the_stored_object(child: Any, django_user_model: Any) -> None:
    """Rows are per-child, objects are shared (§16.6, §17.5) — stock is no
    different, and one file on disk serving every family is the point."""
    import datetime

    from apps.profiles.models import Child

    sibling = Child.objects.create(
        parent=child.parent,
        first_name="Meera",
        birthdate=datetime.date(datetime.date.today().year - 4, 1, 1),
    )

    mine = stock.safe_illustration(child, seed="same")
    theirs = stock.safe_illustration(sibling, seed="same")

    assert mine.id != theirs.id, "each child owns their own row"
    assert mine.url == theirs.url, "and they share one object"
