"""Static check: every instance attribute is assigned before it is used.

This exists because it was missed. `_unavailable` was read in a method and
never initialised, which passed lint and passed type checking and then failed
at runtime in Home Assistant with "object has no attribute". Nothing in the
pure-module test suite could catch it, because the fault was in a file that
needs Home Assistant to import.

Parsing the source finds it without importing anything.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "abode_hvac_coordinator"


def _self_attributes(cls: ast.ClassDef) -> tuple[set[str], set[str]]:
    """Attributes assigned on self, and attributes read from self."""
    assigned: set[str] = set()
    used: set[str] = set()
    for node in ast.walk(cls):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            if isinstance(node.ctx, ast.Store):
                assigned.add(node.attr)
            else:
                used.add(node.attr)
    return assigned, used


#: Names provided by the Home Assistant base classes this project subclasses.
#: Anything here is inherited, not local state, so its absence is not a fault.
INHERITED = {
    "add_suggested_values_to_schema",
    "async_abort",
    "async_create_entry",
    "async_show_form",
    "async_show_menu",
    "async_request_refresh",
    "async_set_updated_data",
    "async_on_remove",
    "async_write_ha_state",
    "config_entry",
    "coordinator",
    "trace",
    "_room_id",
    "data",
    "defer",
    "entity_description",
    "hass",
    "last_update_success",
    "logger",
    "name",
    "update_interval",
}


def _declared_names(cls: ast.ClassDef) -> set[str]:
    """Methods, annotated attributes and class-level names."""
    names: set[str] = set()
    for node in cls.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
    return names


class TestNoUninitialisedAttributes(unittest.TestCase):
    def test_every_private_attribute_is_assigned_somewhere(self):
        problems: list[str] = []
        for path in sorted(SRC.glob("*.py")):
            tree = ast.parse(path.read_text())
            for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
                assigned, used = _self_attributes(cls)
                declared = _declared_names(cls)
                for attr in sorted(used - assigned - declared - INHERITED):
                    if attr.startswith("__"):
                        continue
                    # Methods defined on a sibling class in a mixin pair.
                    if any(
                        attr in _declared_names(other)
                        for other in ast.walk(tree)
                        if isinstance(other, ast.ClassDef)
                    ):
                        continue
                    problems.append(f"{path.name}:{cls.name}.{attr}")
        self.assertEqual(problems, [], f"attributes used but never assigned: {problems}")


if __name__ == "__main__":
    unittest.main()


class TestNoStrayConstantsInCalls(unittest.TestCase):
    """A bare CONSTANT on its own line inside a call is an insertion fault.

    This exists because it happened three times. A scripted edit meant to add a
    constant to an import list matched inside a `config_entry.options.get(...)`
    call instead and inserted it as a third positional argument. The result is
    valid Python, so `ruff` passes and the module byte-compiles; it fails at
    runtime the moment the entry is set up.

    **mypy is the real net**, and it is now run across the whole package with
    Home Assistant installed. It caught the second and third occurrences.

    This test is a narrow backstop for the single call that has actually been
    hit, and nothing wider. An indentation heuristic was tried and produced
    seven false positives on legitimately wrapped calls — it could not tell an
    insertion from a line break, so it was removed rather than shipped with
    exceptions.
    """

    def test_dict_get_is_never_called_with_three_positional_arguments(self):
        """`dict.get` takes a key and a default. A third is always a mistake."""
        problems: list[str] = []
        for path in sorted(SRC.glob("*.py")):
            tree = ast.parse(path.read_text())
            for call in [n for n in ast.walk(tree) if isinstance(n, ast.Call)]:
                func = call.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "get"
                    and len(call.args) > 2
                ):
                    problems.append(f"{path.name}:{call.lineno}")
        self.assertEqual(problems, [], f".get() with too many arguments: {problems}")


#: Modules that must import nothing from Home Assistant, so the whole decision
#: path can be built and tested in a plain Python session.
PURE_MODULES = (
    "const", "forecast", "forms", "grace", "hci", "models", "modes",
    "psychro", "regulate", "scheduling", "staleness", "sun", "tariff",
    "thermal", "weather",
)


class TestPureModulesStayPure(unittest.TestCase):
    """The pure/impure split is the reason the test suite runs at all.

    Checked by parsing imports rather than by searching for the word. A
    docstring that names `homeassistant.components.weather.Forecast` to say
    which field names a payload uses is documentation, not a dependency, and a
    grep cannot tell the difference.
    """

    def test_no_pure_module_imports_home_assistant(self):
        problems: list[str] = []
        for name in PURE_MODULES:
            path = SRC / f"{name}.py"
            self.assertTrue(path.exists(), f"{name}.py is missing")
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    problems.extend(
                        f"{name}.py imports {a.name}"
                        for a in node.names
                        if a.name.split(".")[0] == "homeassistant"
                    )
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module.split(".")[0] == "homeassistant":
                        problems.append(f"{name}.py imports from {module}")
        self.assertEqual(problems, [], f"pure modules importing HA: {problems}")

    def test_every_module_is_classified(self):
        """A new module must be declared pure or impure, not silently skipped."""
        on_disk = {p.stem for p in SRC.glob("*.py")} - {"__init__"}
        impure = {
            "actuator", "config_flow", "coordinator", "diagnostics",
            "entity", "sensor", "store",
        }
        self.assertEqual(
            on_disk - set(PURE_MODULES) - impure,
            set(),
            "a module exists that is neither declared pure nor declared impure",
        )
