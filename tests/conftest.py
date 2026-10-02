"""Shared test setup.

The analysis modules in scripts/ import each other by bare name, and the web
app's modules do the same within webapp/, so both directories go on sys.path
exactly as app.py and analysis.py arrange it at runtime.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBAPP_DIR = os.path.join(ROOT, "webapp")
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
EXAMPLES_DIR = os.path.join(WEBAPP_DIR, "examples")

for path in (WEBAPP_DIR, SCRIPTS_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
