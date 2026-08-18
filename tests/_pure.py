"""Load the pure modules without executing the package's ``__init__``.

``custom_components/abode_power_tariffs/__init__.py`` imports Home Assistant.
The pure modules do not, and the point of separating them is that they can be
tested without it — so the package is reconstructed here from the file system
under a different name.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PACKAGE = "abode_power_tariffs_pure"
PURE_MODULES = (
    "const",
    "plan",
    "validate",
    "intervals",
    "allowance",
    "strip",
    "serialise",
)

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "abode_power_tariffs"


def load() -> types.ModuleType:
    """Import every pure module and return the synthetic package."""
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]

    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    sys.modules[PACKAGE] = package

    for name in PURE_MODULES:
        spec = importlib.util.spec_from_file_location(
            f"{PACKAGE}.{name}", ROOT / f"{name}.py"
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{PACKAGE}.{name}"] = module
        spec.loader.exec_module(module)
        setattr(package, name, module)

    return package
