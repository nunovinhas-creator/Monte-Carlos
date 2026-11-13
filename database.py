"""Compatibility entry point; implementation in pipeline/database.py."""
import sys
from importlib import import_module

_impl = import_module("pipeline.database")
sys.modules[__name__] = _impl
