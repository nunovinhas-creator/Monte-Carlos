"""Compatibility entry point; implementation in analysis/roi_report.py."""
import sys
from importlib import import_module

_impl = import_module("analysis.roi_report")
if __name__ == "__main__":
    _impl.gerar()
else:
    sys.modules[__name__] = _impl
