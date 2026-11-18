"""Inputs that used to cost the static parsers unbounded time, for a public server.

Each runs in a subprocess with a deadline, so a regression fails cleanly
instead of hanging the suite. The deadlines are generous; the fixed code
finishes these in well under a second.
"""

import os
import subprocess
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_bounded(code, seconds=15):
    """Run ``code`` in a fresh interpreter; fail if it is still running after ``seconds``."""
    try:
        done = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT,
                              capture_output=True, text=True, timeout=seconds)
    except subprocess.TimeoutExpired:
        pytest.fail(f"still running after {seconds} s")
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout


def test_empty_comment_run_does_not_backtrack():
    # "/**/" * 40 took hours in the old version-2 regex (time doubled per comment).
    out = run_bounded("""
        from qslicer.parsers import staticopenqasmparser as p
        print(p._is_version_2("/**/" * 40 + "X"))
        print(p._is_version_2("/**/" * 200000 + "OPENQASM 2.0;"))
    """)
    assert out.split() == ["False", "True"]


@pytest.mark.parametrize("source, expected", [
    ("OPENQASM 2.0;", True),
    ("  \n// header\n/* block\n comment */ OPENQASM 2.0;", True),
    ("OPENQASM 3.0;", False),
    ("// no newline OPENQASM 2.0;", False),
    ("/* unterminated OPENQASM 2.0;", False),
    ("qubit q; OPENQASM 2.0;", False),
])
def test_version_2_detection(source, expected):
    from qslicer.parsers.staticopenqasmparser import _is_version_2
    assert _is_version_2(source) is expected


def test_many_unterminated_opaque_lines_are_linear():
    # 20,000 "opaque" lines with no ';' took about 12 s with the old regex.
    run_bounded("""
        from qslicer.parsers import staticopenqasmparser as p
        source = "OPENQASM 2\\n" + "opaque\\n" * 20000
        assert p._OPAQUE.sub(r"\\1gate\\2 { }", source) == source
    """, seconds=5)


def test_repeated_squaring_of_a_big_int_is_refused():
    # x = x * x on a 7**30000 ran unguarded in C (Mult was not in the guarded set).
    out = run_bounded("""
        from qslicer.parsers.staticqiskitparser import StaticParseError, StaticQiskitParser
        src = "x = 7 ** 30000\\nfor i in range(40):\\n    x = x * x\\n"
        try:
            StaticQiskitParser(src, time_limit=20).parse()
        except StaticParseError as exc:
            print("refused:", exc)
        except ValueError as exc:
            print("other:", exc)
    """)
    assert "too large" in out
