"""Guard tests: the suite must never be able to reach a production datastore.

These lock in the session-autouse safety net from ``conftest.py`` so it cannot
be silently removed.
"""

import os


class TestDatastoreSafety:
    def test_database_url_is_sandboxed(self):
        # Any inherited DATABASE_URL (incl. a real production one) must be
        # overwritten with a local, obviously-fake sandbox value.
        assert os.environ["DATABASE_URL"] == "sqlite:///:memory:"

    def test_no_remote_datastore_endpoints(self):
        for var in ("DATABASE_URL", "DB_URL", "POSTGRES_URL", "MONGODB_URI", "REDIS_URL"):
            value = os.environ[var]
            assert any(token in value for token in ("memory", "localhost")), value
