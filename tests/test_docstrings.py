"""Enforce high-level module and Google-style callable documentation."""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "mcp_portal"


def test_python_modules_have_docstrings() -> None:
    """Verify source and test modules describe their high-level purpose."""
    project_root = SOURCE_ROOT.parents[1]
    paths = [*SOURCE_ROOT.rglob("*.py"), *(project_root / "tests").rglob("*.py")]
    missing = [
        path.relative_to(project_root)
        for path in paths
        if not ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
    ]

    assert missing == []


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


def test_dataclass_fields_have_documentation_metadata() -> None:
    """Verify every dataclass field is documented and uses `Annotated` metadata."""
    failures: list[str] = []

    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not _is_dataclass(node):
                continue

            documented = _documented_attributes(ast.get_docstring(node) or "")
            for field_node in _class_fields(node):
                name = field_node.target.id
                location = f"{path.relative_to(SOURCE_ROOT.parent)}:{field_node.lineno}"
                if name not in documented:
                    failures.append(f"{location} {node.name}.{name} is missing from Attributes:")
                annotated_description = _annotated_description(field_node.annotation)
                if annotated_description is None:
                    failures.append(f"{location} {node.name}.{name} does not use Annotated")
                elif name in documented and annotated_description != documented[name]:
                    failures.append(
                        f"{location} {node.name}.{name} Annotated metadata differs from Attributes:"
                    )

    assert failures == []


def test_pydantic_fields_have_descriptions() -> None:
    """Verify every Pydantic model field declares a non-empty `Field` description."""
    failures: list[str] = []

    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not _is_pydantic_model(node):
                continue

            for field_node in _class_fields(node):
                field_call = _pydantic_field_call(field_node)
                description = (
                    next(
                        (
                            keyword.value.value
                            for keyword in field_call.keywords
                            if keyword.arg == "description"
                            and isinstance(keyword.value, ast.Constant)
                            and isinstance(keyword.value.value, str)
                        ),
                        "",
                    )
                    if field_call is not None
                    else ""
                )
                if not description.strip():
                    location = f"{path.relative_to(SOURCE_ROOT.parent)}:{field_node.lineno}"
                    failures.append(
                        f"{location} {node.name}.{field_node.target.id} needs Field(description=...)"
                    )

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


def _is_dataclass(node: ast.ClassDef) -> bool:
    """Report whether a class has the standard `dataclass` decorator.

    Args:
        node: Class AST node to inspect.

    Returns:
        True when the class is decorated with `dataclass`.
    """
    return any(
        (isinstance(decorator, ast.Call) and _name(decorator.func) == "dataclass")
        or _name(decorator) == "dataclass"
        for decorator in node.decorator_list
    )


def _is_pydantic_model(node: ast.ClassDef) -> bool:
    """Report whether a class directly derives from Pydantic `BaseModel`.

    Args:
        node: Class AST node to inspect.

    Returns:
        True when `BaseModel` is one of the declared bases.
    """
    return any(_name(base) == "BaseModel" for base in node.bases)


def _class_fields(node: ast.ClassDef) -> list[ast.AnnAssign]:
    """Return directly declared annotated class fields.

    Args:
        node: Class AST node to inspect.

    Returns:
        Annotated assignments whose targets are simple field names.
    """
    return [
        child
        for child in node.body
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
    ]


def _documented_attributes(docstring: str) -> dict[str, str]:
    """Extract field descriptions from a Google-style `Attributes:` section.

    Args:
        docstring: Parsed class docstring.

    Returns:
        Field names mapped to their indented attribute descriptions.
    """
    descriptions: dict[str, str] = {}
    in_attributes = False
    for line in docstring.splitlines():
        if line == "Attributes:":
            in_attributes = True
            continue
        if in_attributes and line and not line.startswith(" "):
            break
        if in_attributes and line.startswith("    ") and ":" in line:
            name, description = line.strip().split(":", 1)
            descriptions[name] = description.strip()
    return descriptions


def _annotated_description(annotation: ast.expr) -> str | None:
    """Return the non-empty string description carried by `Annotated` metadata.

    Args:
        annotation: Field type annotation.

    Returns:
        The first string metadata value, or None when one is not declared.
    """
    if not (
        isinstance(annotation, ast.Subscript)
        and _name(annotation.value) == "Annotated"
        and isinstance(annotation.slice, ast.Tuple)
    ):
        return None
    return next(
        (
            item.value
            for item in annotation.slice.elts[1:]
            if isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value.strip()
        ),
        None,
    )


def _pydantic_field_call(field_node: ast.AnnAssign) -> ast.Call | None:
    """Find the Pydantic `Field` call attached to a model field.

    Args:
        field_node: Annotated model field assignment.

    Returns:
        The direct or `Annotated`-metadata `Field` call, when present.
    """
    if isinstance(field_node.value, ast.Call) and _name(field_node.value.func) == "Field":
        return field_node.value
    if isinstance(field_node.annotation, ast.Subscript) and isinstance(
        field_node.annotation.slice, ast.Tuple
    ):
        return next(
            (
                item
                for item in field_node.annotation.slice.elts[1:]
                if isinstance(item, ast.Call) and _name(item.func) == "Field"
            ),
            None,
        )
    return None


def _name(node: ast.expr) -> str | None:
    """Return the final identifier represented by an AST expression.

    Args:
        node: Name or attribute expression.

    Returns:
        Final identifier, or None for another expression type.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


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
