"""
The tutoring loop: what a tutor does with a detection result.

`detection` answers "what went wrong". This package answers "so what do we say
and do about it" - which word to stop on, what to say, when to ask for another
try, and what to read next.
"""

from . import coaching, loop, selection, session

__all__ = ["coaching", "loop", "selection", "session"]
