"""Compatibility entry point; implementation in src/team_stats.py."""
import sys
from importlib import import_module

_impl = import_module("src.team_stats")
sys.modules[__name__] = _impl
