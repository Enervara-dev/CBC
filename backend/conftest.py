"""Pytest bootstrap: put `backend/src` on sys.path so tests import the packages
(`db`, `services`, `models`) the same way the application does."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
