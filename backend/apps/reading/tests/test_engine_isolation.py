"""The two properties that make the vendored engine safe to keep here.

Both are invisible until they break, and both break by accident rather than by
decision — one convenient import is all it takes. So they are asserted rather
than written down (Docs/Build/Reading-Integration.md §5.1, §2.1).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "engine"

#: Top-level packages the engine must never reach for. Django and DRF because the
#: engine's portability is the whole reason this port was cheap — it survived a
#: FastAPI-to-Django migration unchanged, and it will only survive the next move
#: if it still knows nothing about the web layer. `apps` because a dependency on a
#: sibling app is the same mistake wearing a different name.
FORBIDDEN_ROOTS = {"django", "rest_framework", "apps", "config", "celery"}

#: The heavy half. These may be imported *inside* the three model-backed modules,
#: which is why this set is checked against module-scope imports only.
ML_ROOTS = {"torch", "transformers", "faster_whisper", "numpy", "phonemizer", "espeakng_loader"}

#: The three modules that legitimately load models, and are therefore allowed to
#: import the heavy packages at module scope. Everything else in the engine must
#: be importable on a box that has none of them installed.
MODEL_BACKED = {"audio.py", "whisper_engine.py", "phoneme_engine.py"}


def _module_scope_imports(path: Path) -> set[str]:
    """Root package names imported at module scope — not inside a function."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_engine_knows_nothing_about_django() -> None:
    """No file in the engine imports the framework, at any scope.

    Checked over the whole tree rather than by importing it, because an import
    test only catches what a particular entry point happens to pull in.
    """
    offenders: dict[str, set[str]] = {}
    for path in sorted(ENGINE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        bad = roots & FORBIDDEN_ROOTS
        if bad:
            offenders[path.name] = bad

    assert not offenders, (
        f"The engine reached into the web layer: {offenders}. "
        "Adapters around it may import Django; the engine may not."
    )


def test_only_the_model_backed_modules_import_the_heavy_packages() -> None:
    """torch and friends stay behind a function call.

    §2.1 forbids the speech models from the app box. That is an infrastructure
    rule, but it is only enforceable if importing the *pure* half of the engine —
    the phonics patterns, the mastery rule, the teaching ladder — does not drag a
    gigabyte of wheels in behind it.
    """
    offenders: dict[str, set[str]] = {}
    for path in sorted(ENGINE.rglob("*.py")):
        if path.name in MODEL_BACKED:
            continue
        bad = _module_scope_imports(path) & ML_ROOTS
        if bad:
            offenders[str(path.relative_to(ENGINE))] = bad

    assert not offenders, (
        f"Heavy imports at module scope outside {sorted(MODEL_BACKED)}: {offenders}. "
        "Move them inside the function that needs them."
    )


def test_the_pure_engine_imports_in_a_process_with_no_ml_packages_loaded() -> None:
    """The belt to the previous test's braces, run for real in a clean process.

    A subprocess rather than an assertion about `sys.modules` here, because this
    test session has already imported plenty and cannot prove what a fresh one
    would pull.
    """
    probe = (
        "import sys;"
        "import apps.reading.engine.content.skills;"
        "import apps.reading.engine.tutoring.loop;"
        "import apps.reading.engine.detection;"
        f"loaded = sorted(m for m in sys.modules if m.split('.')[0] in {ML_ROOTS!r});"
        "print(loaded)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[3],
        check=True,
    )
    assert (
        result.stdout.strip() == "[]"
    ), f"Importing the engine loaded ML packages: {result.stdout.strip()}"
