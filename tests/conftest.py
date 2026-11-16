"""
Shared pytest configuration for centralize-imap-success-master spec tests.

Ensures the project root is on sys.path so `import imap_engine` works.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Driving _worker with default settings calls record_live_to_already(), which
# writes to the real master list on disk -- so every test run appended its own
# fixtures (a@ok.test, alice@example.com) to the production file, and the next
# union sync carried them back into the cluster. Make that write a no-op for the
# whole session.
os.environ.setdefault("IMAP_NO_MASTER_WRITE", "1")
