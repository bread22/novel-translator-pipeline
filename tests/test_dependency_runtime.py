from __future__ import annotations

import sys
import unittest

from starlette.testclient import TestClient

from translator.web.app import create_app


@unittest.skipIf(sys.version_info >= (3, 14), "Python 3.14 is outside the declared compatibility range")
class DependencyRuntimeTests(unittest.TestCase):
    def test_health_request_completes_inside_testclient_lifespan(self) -> None:
        with TestClient(create_app()) as client:
            response = client.get("/health", timeout=10)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
