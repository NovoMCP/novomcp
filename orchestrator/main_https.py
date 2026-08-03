"""Dev shim: keep ``python main_https.py`` working from a source checkout after
the engine moved under ``src/novomcp/``.

Installed environments get the ``novomcp`` console command instead
(see pyproject ``[project.scripts]``); both resolve to ``novomcp.main_https:main``.
"""
import os
import sys

# Make the src-layout package importable when running from a checkout without
# installing (dev convenience only; installed environments don't need this).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from novomcp.main_https import app, main  # noqa: E402,F401

if __name__ == "__main__":
    main()
