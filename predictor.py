"""Compatibility entry point; implementation in src/predictor.py."""
import sys
from importlib import import_module

_impl = import_module("src.predictor")
sys.modules[__name__] = _impl
