"""The Primer's AI layer.

A thin orchestration layer over provider SDKs (PRODUCT.md §16.4, §19). Day 1
ships the seam only: a typed `Beat` contract, a model router with Claude
primary, and a validate-and-repair wrapper. Memory retrieval, the safety layer,
illustration, and TTS plug into this later.
"""
