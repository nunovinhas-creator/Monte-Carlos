"""Compatibility entry point; implementation in analysis/calibration_o25.py."""
import sys
from importlib import import_module

_impl = import_module("analysis.calibration_o25")
if __name__ == "__main__":
    _impl.cli()
else:
    sys.modules[__name__] = _impl
