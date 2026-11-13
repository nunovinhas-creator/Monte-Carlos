"""Compatibility entry point; implementation in src/adjusted_xg.py."""
import sys
from importlib import import_module

_impl = import_module("src.adjusted_xg")
sys.modules[__name__] = _impl
