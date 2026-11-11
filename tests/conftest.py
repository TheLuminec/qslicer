"""Shared test setup.

The web app's modules import each other by bare name, so webapp/ goes on
sys.path as app.py arranges it at runtime. ``qslicer`` comes from the repository
root (``pythonpath = .`` in pytest.ini) or from an installed copy.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBAPP_DIR = os.path.join(ROOT, "webapp")
EXAMPLES_DIR = os.path.join(ROOT, "qslicer", "examples")

if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)
