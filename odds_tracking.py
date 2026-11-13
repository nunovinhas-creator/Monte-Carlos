"""Compatibility entry point; implementation in pipeline/odds_tracking.py."""
import sys
from importlib import import_module

_impl = import_module("pipeline.odds_tracking")
if __name__ == "__main__":
    _impl.capturar()
else:
    sys.modules[__name__] = _impl
