"""Compatibility entry point; implementation in src/api.py."""
import sys
from importlib import import_module

_impl = import_module("src.api")
sys.modules[__name__] = _impl
