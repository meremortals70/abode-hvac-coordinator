"""Import the component's pure modules without running its package __init__.

The package's __init__.py sets the integration up in Home Assistant, so
importing it needs Home Assistant installed. The pure tests run with
nothing installed (Section 13.2), so they register the package by its
path and import the pure modules directly.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = ROOT / "custom_components" / "abode_hvac_coordinator"
PACKAGE = "custom_components.abode_hvac_coordinator"


def _register() -> None:
    """Register the package by its path. Where Home Assistant is installed
    (the Home Assistant tests), the real package is imported instead, so
    Home Assistant's loader and these tests see one and the same package."""
    if PACKAGE in sys.modules:
        return
    if importlib.util.find_spec("homeassistant") is not None:
        sys.path.insert(0, str(ROOT))
        importlib.import_module(PACKAGE)
        return
    parent = types.ModuleType("custom_components")
    parent.__path__ = [str(PACKAGE_DIR.parent)]
    pkg = types.ModuleType(PACKAGE)
    pkg.__path__ = [str(PACKAGE_DIR)]
    sys.modules.setdefault("custom_components", parent)
    sys.modules[PACKAGE] = pkg


def load(name: str) -> Any:
    """Import custom_components.abode_hvac_coordinator.<name>."""
    _register()
    return importlib.import_module(f"{PACKAGE}.{name}")
