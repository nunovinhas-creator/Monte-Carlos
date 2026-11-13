"""Compatibility entry point; implementation in src/main.py."""
import sys
from importlib import import_module

_impl = import_module("src.main")
if __name__ == "__main__":
    _impl.analisar()
else:
    sys.modules[__name__] = _impl
