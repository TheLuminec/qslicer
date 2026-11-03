"""Run CodeQL's Python extractor without its autobuild script.

``codeql database trace-command`` runs this with the extractor's environment
set. It supplies the two variables autobuild.cmd/.sh would have set and runs
the extractor's index.py, so no CodeQL file has to be edited.
"""

import os
import runpy
import sys

root = os.environ["CODEQL_EXTRACTOR_PYTHON_ROOT"]
os.environ.setdefault("LGTM_SRC", os.getcwd())
os.environ.setdefault("LGTM_WORKSPACE", os.environ["CODEQL_EXTRACTOR_PYTHON_SCRATCH_DIR"])
index = os.path.join(root, "tools", "index.py")
sys.argv = [index]
sys.path.insert(0, os.path.dirname(index))
runpy.run_path(index, run_name="__main__")
