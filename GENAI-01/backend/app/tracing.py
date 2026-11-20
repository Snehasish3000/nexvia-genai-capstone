"""LangSmith tracing bridge; calls remain functional when tracing is not configured."""

from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

try:
    from langsmith import traceable as _traceable
except ImportError:
    def traceable(*, name: str, run_type: str = "chain") -> Callable[[F], F]:
        def decorate(function: F) -> F:
            return function
        return decorate
else:
    def traceable(*, name: str, run_type: str = "chain") -> Callable[[F], F]:
        return _traceable(name=name, run_type=run_type)
