"""
The reading engine: framework-agnostic domain logic.

Nothing in here knows about HTTP, Django, or any database. It is imported by
both the API layer and the background workers, and was written before either
existed - which is why the move to Django costs nothing.
"""
