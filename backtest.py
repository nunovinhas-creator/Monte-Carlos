"""Compatibility entry point; implementation in analysis/backtest.py."""
import sys
from importlib import import_module

_impl = import_module("analysis.backtest")
if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
