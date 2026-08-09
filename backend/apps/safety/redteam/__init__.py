"""The red-team harness (PRODUCT.md §19.7, §13.11).

A package rather than a test module because §19.7 asks for a *growing library*
run against every prompt and model change, and a library needs somewhere to live
that is not a single file of assertions. `corpus.py` is the data; the tests beside
it are the assertions over that data.

Run it on its own with:

    cd backend && python -m pytest apps/safety/redteam -q
"""
