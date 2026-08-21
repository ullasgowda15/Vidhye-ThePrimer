"""Renderers for endpoints whose body is not JSON.

**The trap these exist to close.** A DRF `APIView` negotiates content in
`initial()` — *before* your handler runs. It compares the request's `Accept`
header against `renderer_classes`, and if nothing matches it raises
`NotAcceptable` and the handler is never called. That is true even when the
handler would have returned a plain `HttpResponse` and never touched a renderer
at all, which is exactly how a binary endpoint is written.

So a view that returns a zip, and a client that politely says
`Accept: application/zip`, produce **HTTP 406 "Could not satisfy the request
Accept header."** — with no zip, no log line from the view, and nothing in the
handler to debug, because the handler did not run.

That is what `POST /api/v1/account/export` did from the day it was written. The
tests called it with no `Accept` header at all, so negotiation fell through to
`JSONRenderer` and every one of them passed; the frontend sent
`accept: 'application/zip'` and got a 406. A parent asking for their own child's
data — §12.8, the one endpoint that must never quietly fail — got an error about
a header.

Adding the media type to `renderer_classes` is the whole fix. The renderer is
never asked to render the success body: the view returns an `HttpResponse`, and
DRF passes non-`Response` results through `finalize_response` untouched. It
exists so that negotiation has something to say yes to.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework.renderers import BaseRenderer, JSONRenderer


class BinaryRenderer(BaseRenderer):
    """Negotiates a binary media type; renders nothing in the normal case.

    Subclass it and set `media_type`/`format` for the type you actually serve.
    """

    media_type = "application/octet-stream"
    format = "bin"
    # `None`, not the default `utf-8`. DRF appends `; charset=utf-8` to the
    # Content-Type when a renderer declares one, and a zip with a charset is a
    # lie that some clients act on.
    charset = None

    def render(
        self,
        data: Any,
        accepted_media_type: str | None = None,
        renderer_context: Mapping[str, Any] | None = None,
    ) -> bytes:
        if isinstance(data, bytes | bytearray | memoryview):
            return bytes(data)
        # A safety net rather than a path taken: reached only if a DRF `Response`
        # is negotiated to this renderer — an error body, on a view that has not
        # overridden `finalize_response` to serve those as JSON. Emitting the JSON
        # anyway means a client gets a readable `detail` instead of a truncated
        # binary body, which is the difference between a bad error and a mystery.
        return JSONRenderer().render(data)


class ZipRenderer(BinaryRenderer):
    """`application/zip` — the account export (§12.8)."""

    media_type = "application/zip"
    format = "zip"
