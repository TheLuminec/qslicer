"""A stand-in for the ``io`` module that keeps what a parser throws away.

``QiskitParser.parse_file`` wraps the execution of the submitted program in its
own ``io.StringIO()`` and discards the result, which would leave the Console tab
permanently empty. Swapping the module reference for ``CaptureIO`` gives us a
copy of what the program printed without changing anything in ``scripts/``.

Used by parse_worker.py. If the parser ever grows a way to hand back captured
output, delete this file and its two call sites.
"""

import io
from typing import Any, List


class CapturedBuffer(io.StringIO):
    """A StringIO that hands its contents to ``sink`` when it is closed."""

    def __init__(self, sink: List[str]) -> None:
        super().__init__()
        self._sink = sink

    def close(self) -> None:
        try:
            self._sink.append(self.getvalue())
        except ValueError:  # already closed
            pass
        super().close()


class CaptureIO:
    """Everything except ``StringIO`` falls through to the real ``io`` module."""

    def __init__(self, sink: List[str]) -> None:
        self._sink = sink

    def __getattr__(self, name: str) -> Any:
        return getattr(io, name)

    def StringIO(self, *args: Any, **kwargs: Any) -> CapturedBuffer:
        return CapturedBuffer(self._sink)
