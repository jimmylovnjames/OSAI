"""Importing this package registers every tool in REGISTRY."""
from . import apps, browser, desktop, files, shell, shell_ui, system  # noqa: F401
from .base import REGISTRY, Tool, ToolContext, ToolError, normalize_result

__all__ = ["REGISTRY", "Tool", "ToolContext", "ToolError", "normalize_result"]
