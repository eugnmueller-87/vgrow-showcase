"""Every global a function reads exists in its module: serve() outside the demo runs for the first time on the Mac."""
from __future__ import annotations

import builtins
import dis
import importlib
import pkgutil
import types

import pytest

import vgrow


def _code_objects(code: types.CodeType):
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _code_objects(const)


def unknown_globals(source: str, namespace: object) -> set[str]:
    """The names functions in this source load as globals that neither the namespace nor builtins has."""
    found: set[str] = set()
    for code in _code_objects(compile(source, "<module>", "exec")):
        for instruction in dis.get_instructions(code):
            if instruction.opname == "LOAD_GLOBAL" and not (
                    hasattr(namespace, instruction.argval) or hasattr(builtins, instruction.argval)):
                found.add(instruction.argval)
    return found


MODULES = [info.name for info in pkgutil.walk_packages(vgrow.__path__, "vgrow.") if not info.name.endswith("__main__")]


def test_the_scan_finds_a_name_only_another_function_imported() -> None:
    source = "def make():\n    import json\n    return json\n\ndef serve():\n    return json.dumps({})\n"
    assert unknown_globals(source, types.ModuleType("sample")) == {"json"}


@pytest.mark.parametrize("name", MODULES)
def test_no_function_reads_a_global_its_module_does_not_have(name: str) -> None:
    module = importlib.import_module(name)
    with open(module.__file__, encoding="utf-8") as handle:
        assert unknown_globals(handle.read(), module) == set()
