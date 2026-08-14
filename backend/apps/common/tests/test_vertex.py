"""The Vertex client cache, and the dead socket it used to hand out forever.

`vertex.client()` memoises a `genai.Client` so that a beat, a safety grade and an
image moderation do not each pay an OAuth token exchange. The cache is the right
call; what it cannot do on its own is notice that the connection pool *inside* the
thing it cached has closed.

When that happens every call through the client raises
`Cannot send a request, as the client has been closed` **locally** — httpx refuses
before opening a socket — so it is instant, it never reaches Vertex, and no retry
or fallback provider helps because nothing is wrong at the far end. `lru_cache`
then serves the same dead object to every subsequent caller in the process.

The symptom that led here: an adventure titled "A day with cat at the Hollow Oak"
whose story was about a snail. `build_plan` and beat one failed 158ms apart — far
too fast to have left the machine — and each fell back independently, one to a
title templated from the child's seed interest and the other to a pre-vetted beat
out of the safety pool. Two unrelated emergency paths, one closed socket.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.common import vertex


class _Transport:
    def __init__(self, closed: bool) -> None:
        self.is_closed = closed


class _ApiClient:
    # `Any`, because one test swaps in a transport whose `is_closed` raises — the
    # SDK's privates are read defensively and the fake has to be able to misbehave
    # in the same way the real one might.
    _httpx_client: Any

    def __init__(self, closed: bool) -> None:
        self._httpx_client = _Transport(closed)


class _FakeClient:
    """Shaped like the parts of `genai.Client` the health check reads."""

    def __init__(self, closed: bool = False, serial: int = 0) -> None:
        self._api_client = _ApiClient(closed)
        self.serial = serial


@pytest.fixture(autouse=True)
def _clean_cache() -> Any:
    """The cache is process-global, so a client built by one test would otherwise
    be handed to the next one and make its assertions meaningless."""
    vertex._client.cache_clear()
    yield
    vertex._client.cache_clear()


@pytest.fixture
def configured(settings: Any) -> None:
    settings.GOOGLE_CLOUD_PROJECT = "vidhye"
    settings.GOOGLE_CLOUD_LOCATION = "global"
    settings.GOOGLE_APPLICATION_CREDENTIALS = "/nowhere/sa.json"


class TestTheCacheDoesNotOutliveTheSocket:
    def test_a_live_client_is_returned_from_cache_without_rebuilding(
        self, configured: None, monkeypatch: Any
    ) -> None:
        """The ordinary path, asserted so the fix below cannot be "rebuild always".

        A token exchange per beat is exactly the cost the cache exists to avoid.
        """
        built = []

        def build(*args: Any, **kwargs: Any) -> Any:
            built.append(1)
            return _FakeClient(closed=False, serial=len(built))

        monkeypatch.setattr(vertex, "_client", vertex.lru_cache(maxsize=4)(build))

        first = vertex.client()
        second = vertex.client()

        assert first is second
        assert len(built) == 1, "a healthy client was rebuilt"

    def test_a_closed_transport_is_replaced_rather_than_handed_out_again(
        self, configured: None, monkeypatch: Any
    ) -> None:
        """The defect. The first client's pool is closed; the caller must still get
        something it can make a request with."""
        built: list[_FakeClient] = []

        def build(*args: Any, **kwargs: Any) -> Any:
            # Only the first one is born closed, standing in for a pool that was
            # closed some time after it was cached.
            made = _FakeClient(closed=not built, serial=len(built) + 1)
            built.append(made)
            return made

        monkeypatch.setattr(vertex, "_client", vertex.lru_cache(maxsize=4)(build))

        first = vertex.client()

        assert len(built) == 2, "the closed client was handed out instead of rebuilt"
        assert first is built[1]
        assert first._api_client._httpx_client.is_closed is False

    def test_the_replacement_is_then_cached_like_any_other(
        self, configured: None, monkeypatch: Any
    ) -> None:
        """A rebuild that did not repopulate the cache would pay a token exchange
        on every call from then on — the original cost, arrived at from the other
        side."""
        built: list[_FakeClient] = []

        def build(*args: Any, **kwargs: Any) -> Any:
            made = _FakeClient(closed=not built, serial=len(built) + 1)
            built.append(made)
            return made

        monkeypatch.setattr(vertex, "_client", vertex.lru_cache(maxsize=4)(build))

        first = vertex.client()
        second = vertex.client()
        third = vertex.client()

        assert first is second is third
        assert len(built) == 2, "the healthy replacement was not cached"


class TestTheHealthCheckNeverBreaksTheCallItProtects:
    """`_transport_closed` reads SDK privates, so it must fail open in every
    direction. A predicate that raises would take down the generation it was added
    to keep alive — a strictly worse outcome than the bug."""

    @pytest.mark.parametrize(
        "candidate",
        [
            None,
            object(),
            _FakeClient(closed=False),
        ],
        ids=["none", "unrecognised-object", "healthy"],
    )
    def test_anything_it_cannot_read_is_treated_as_healthy(self, candidate: Any) -> None:
        assert vertex._transport_closed(candidate) is False

    def test_a_raising_transport_is_treated_as_healthy(self) -> None:
        class Exploding:
            @property
            def is_closed(self) -> bool:
                raise RuntimeError("the SDK changed shape under us")

        broken = _FakeClient()
        broken._api_client._httpx_client = Exploding()

        assert vertex._transport_closed(broken) is False

    def test_a_closed_transport_is_the_one_thing_it_reports(self) -> None:
        assert vertex._transport_closed(_FakeClient(closed=True)) is True
