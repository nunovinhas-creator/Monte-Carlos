"""Compatibility entry point; implementation in pipeline/settlement.py."""
import sys
from importlib import import_module

_impl = import_module("pipeline.settlement")
if __name__ == "__main__":
    _impl.resolver_jogos()
else:
    sys.modules[__name__] = _impl
