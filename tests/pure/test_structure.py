"""The rules of the structure (Section 5) [D002].

Modules 1-7, the helpers, const.py and models.py import nothing from Home
Assistant, and a module imports only modules earlier in the cycle, plus
the helpers, const.py and models.py.
"""

from __future__ import annotations

import ast
import unittest

from tests.pure._load import PACKAGE_DIR

FOUNDATION = ["const.py", "models.py"]
HELPERS = ["hci.py", "psychro.py", "sun.py", "capabilities.py", "linalg.py"]
# the cycle order of Section 5; each entry lists its files
CYCLE = [
    ("observe", ["observe.py"]),
    ("model", ["model/__init__.py", "model/physics.py", "model/filter.py", "model/unit.py",
               "model/humidity.py", "model/trust.py", "model/forecast.py"]),
    ("comfort", ["comfort.py"]),
    ("economics", ["economics.py"]),
    ("control", ["control/__init__.py", "control/plan.py", "control/pid.py", "control/rl.py",
                 "control/arbiter.py"]),
    ("delivery", ["delivery.py"]),
    ("limits", ["limits.py"]),
]
# modules 8-10 may use Home Assistant, but still import only earlier modules (Section 5)
LATER = [("act", ["act.py"]), ("evidence", ["evidence.py"]), ("memory", ["memory.py"])]
# integration files that decide nothing but still import nothing from Home Assistant
PURE_INTEGRATION = ["forms.py"]
FORBIDDEN_ROOTS = ("homeassistant", "voluptuous", "probatio")


def imports_of(path: str) -> list[str]:
    """Every absolute module name a file imports, relative ones resolved."""
    tree = ast.parse((PACKAGE_DIR / path).read_text(encoding="utf-8"))
    pkg_parts = ["pkg", *path.split("/")[:-1]]
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                out.append(node.module or "")
            else:
                base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                name = ".".join([*base, node.module]) if node.module else ".".join(base)
                if node.module is None:
                    out.extend(f"{name}.{alias.name}" for alias in node.names)
                else:
                    out.append(name)
    return out


def present(files: list[str]) -> list[str]:
    return [f for f in files if (PACKAGE_DIR / f).exists()]


def owner(module: str) -> str | None:
    """The cycle module or helper that a 'pkg.x.y' import names."""
    parts = module.split(".")
    if parts[0] != "pkg" or len(parts) < 2:
        return None
    return parts[1]


class NoHomeAssistant(unittest.TestCase):
    def test_pure_files_import_nothing_from_home_assistant(self) -> None:
        files = FOUNDATION + HELPERS + PURE_INTEGRATION + [f for _, fs in CYCLE for f in fs]
        checked = present(files)
        self.assertGreaterEqual(len(checked), len(FOUNDATION + HELPERS))
        for f in checked:
            for mod in imports_of(f):
                self.assertFalse(mod.split(".")[0] in FORBIDDEN_ROOTS, f"{f} imports {mod}")


class CycleOrder(unittest.TestCase):
    def test_imports_only_earlier_modules(self) -> None:
        shared = {"const", "models", *(h[:-3] for h in HELPERS)}
        order = [name for name, _ in CYCLE + LATER]
        for f in present(FOUNDATION + HELPERS):
            for mod in imports_of(f):
                o = owner(mod)
                if o is not None:
                    self.assertIn(o, shared, f"{f} imports {mod}")
        for i, (name, files) in enumerate(CYCLE + LATER):
            allowed = shared | set(order[: i + 1])
            for f in present(files):
                for mod in imports_of(f):
                    o = owner(mod)
                    if o is not None:
                        self.assertIn(o, allowed, f"{f} imports {mod}")

    def test_const_and_models_import_no_helper(self) -> None:
        for f in FOUNDATION:
            for mod in imports_of(f):
                o = owner(mod)
                if o is not None:
                    self.assertIn(o, {"const", "models"}, f"{f} imports {mod}")


if __name__ == "__main__":
    unittest.main()
