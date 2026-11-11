"""The one function of ``imp`` that CodeQL 2.11's Python extractor uses.

``imp`` was removed in Python 3.12. codeql.py puts this directory on the
extractor's PYTHONPATH only when it runs on 3.12 or later.
"""

import importlib.util as _util


def find_module(name, path=None):
    spec = _util.find_spec(name)
    if spec is None:
        raise ImportError(name)
    return (None, spec.origin, None)
