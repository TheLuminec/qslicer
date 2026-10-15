"""WSGI entry point for running the site behind a real web server.

    gunicorn --chdir webapp --workers 1 --threads 4 wsgi:application

``app.py`` only calls ``app.run(debug=True)`` under ``__main__``, so importing
it here gives a plain, non-debug application object.

One worker. The analysis cache in ``analysis.py`` is an in-process dictionary:
``/api/analyze`` builds a QPDG and hands back a token, and ``/api/slice`` looks
that token up again. With more than one worker process the slice request may
land somewhere that has never seen the token, and the user is told to analyze
again at random. Threads are fine -- use them for concurrency instead.
"""

import os
import sys

WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)

from app import app as application  # noqa: E402

# Behind nginx the client's address and scheme arrive in headers. Flask does not
# trust them by default, and nothing here needs them today -- no redirects to
# absolute URLs, no scheme-dependent behavior. Turn this on if that changes,
# and only with the proxy count set correctly.
#
# from werkzeug.middleware.proxy_fix import ProxyFix
# application.wsgi_app = ProxyFix(application.wsgi_app, x_for=1, x_proto=1, x_host=1)

if __name__ == "__main__":
    # A convenience for checking the production object locally; the real server
    # is whatever imports `application`.
    application.run(host="127.0.0.1", port=8000)
