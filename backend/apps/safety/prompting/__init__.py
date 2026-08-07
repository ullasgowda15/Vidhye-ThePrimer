"""Prompt assembly (PRODUCT.md §19.1-19.2).

Import from here, not from the submodules — the split between `layers` and
`assembler` is an implementation detail.
"""

from apps.safety.prompting.assembler import (
    LAYER_ORDER,
    PromptVersionUnavailable,
    assemble,
    safety_policy_layer,
    system_identity_layer,
)
from apps.safety.prompting.layers import DATA_PREAMBLE, LayerKind, PromptLayer, render

__all__ = [
    "DATA_PREAMBLE",
    "LAYER_ORDER",
    "LayerKind",
    "PromptLayer",
    "PromptVersionUnavailable",
    "assemble",
    "render",
    "safety_policy_layer",
    "system_identity_layer",
]
