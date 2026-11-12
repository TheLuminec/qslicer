"""Shared test setup.

The web app's modules import each other by bare name, so webapp/ goes on
sys.path as app.py arranges it at runtime. ``qslicer`` comes from the repository
root (``pythonpath = .`` in pytest.ini) or from an installed copy.
"""

import os
import sys

# On Linux, qiskit_aer's bundled OpenMP (libgomp) needs a slot of static TLS,
# which is reserved only for libraries loaded early. Imported halfway through
# the run (by an example the executing parser runs, or QS012 looking for
# AerSimulator), it can fail with "cannot allocate memory in static TLS block"
# once other native libraries have used the slots up. Load it first.
try:
    import qiskit_aer  # noqa: F401
except ImportError:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBAPP_DIR = os.path.join(ROOT, "webapp")
EXAMPLES_DIR = os.path.join(ROOT, "qslicer", "examples")

if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)
