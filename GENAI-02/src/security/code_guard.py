"""Static guardrails for model-authored Python before isolated E2B execution."""
import ast


class UnsafeCodeError(ValueError):
    """Raised when code contains host, network, reflection, or dynamic-execution primitives."""


ALLOWED_IMPORTS = {
    "json", "math", "statistics", "decimal", "datetime", "collections", "itertools", "functools",
    "re", "csv", "typing", "array", "fractions", "enum", "operator", "copy", "time", "calendar",
    "numpy", "pandas", "matplotlib", "seaborn", "scipy",
}
BLOCKED_CALLS = {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint", "getattr", "setattr", "delattr", "globals", "locals", "vars"}
BLOCKED_ATTRIBUTES = {"__globals__", "__builtins__", "__subclasses__", "__mro__", "__class__", "__code__", "__getattribute__"}


def validate_generated_python(code: str) -> None:
    """Parse and reject code with obvious network, host I/O, or reflection escape hatches."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise UnsafeCodeError(f"Generated Python is syntactically invalid: {exc.msg} at line {exc.lineno}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root not in ALLOWED_IMPORTS:
                    raise UnsafeCodeError(f"Import of '{root}' is not on the sandbox allowlist")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root not in ALLOWED_IMPORTS:
                raise UnsafeCodeError(f"Import from '{root}' is not on the sandbox allowlist")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_CALLS:
                raise UnsafeCodeError(f"Call to '{node.func.id}' is blocked by the sandbox policy")
            if isinstance(node.func, ast.Attribute) and node.func.attr in BLOCKED_ATTRIBUTES:
                raise UnsafeCodeError(f"Reflection attribute '{node.func.attr}' is blocked")
        elif isinstance(node, ast.Attribute) and node.attr in BLOCKED_ATTRIBUTES:
            raise UnsafeCodeError(f"Reflection attribute '{node.attr}' is blocked")
        elif isinstance(node, ast.Name) and node.id in {"__builtins__", "__loader__", "__spec__"}:
            raise UnsafeCodeError(f"Runtime escape identifier '{node.id}' is blocked")
