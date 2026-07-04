from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "mcp_portal"


def test_source_callables_have_google_style_docstrings() -> None:
    """Verify source classes, functions, and methods are documented."""
    failures: list[str] = []

    for path, node in _iter_source_callables():
        docstring = ast.get_docstring(node)
        if not docstring:
            failures.append(f"{path}:{node.lineno} {node.name} is missing a docstring")
            continue

        missing_sections = _missing_google_sections(node, docstring)
        if missing_sections:
            sections = ", ".join(missing_sections)
            failures.append(f"{path}:{node.lineno} {node.name} is missing {sections}")

    assert failures == []


def _iter_source_callables() -> (
    list[tuple[Path, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]]
):
    """Collect source class, function, and method definitions.

    Returns:
        A list of source paths paired with callable or class AST nodes.
    """
    callables: list[tuple[Path, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]] = []

    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                callables.append((path.relative_to(SOURCE_ROOT.parent), node))

    return callables


def _missing_google_sections(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, docstring: str
) -> list[str]:
    """Find required Google-style sections that are missing from a docstring.

    Args:
        node: AST node for the class, function, or method.
        docstring: Parsed docstring for the AST node.

    Returns:
        Section names required by the callable signature but absent from the docstring.
    """
    required_sections: list[str] = []

    if isinstance(node, ast.ClassDef):
        has_attributes = any(isinstance(child, ast.AnnAssign) for child in node.body)
        if has_attributes:
            required_sections.append("Attributes:")
    else:
        if _has_documented_arguments(node):
            required_sections.append("Args:")
        if _returns_value(node):
            required_sections.append("Returns:")

    return [section for section in required_sections if section not in docstring]


def _has_documented_arguments(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Report whether a function has arguments that should appear in `Args:`.

    Args:
        node: Function or method AST node.

    Returns:
        True when the callable accepts non-implicit arguments.
    """
    positional_args = [
        arg.arg
        for arg in [*node.args.posonlyargs, *node.args.args]
        if arg.arg not in {"self", "cls"}
    ]

    return bool(
        positional_args
        or node.args.kwonlyargs
        or node.args.vararg is not None
        or node.args.kwarg is not None
    )


def _returns_value(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Report whether a function has a non-None return annotation.

    Args:
        node: Function or method AST node.

    Returns:
        True when the return annotation is present and not `None`.
    """
    if node.returns is None:
        return False

    return not isinstance(node.returns, ast.Constant) or node.returns.value is not None
