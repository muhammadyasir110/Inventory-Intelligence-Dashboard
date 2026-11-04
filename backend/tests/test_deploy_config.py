"""
Vercel deployment files stay consistent with the backend (2.7.1):
root requirements.txt = backend/requirements.txt without uvicorn, vercel.json
sends every path to the FastAPI function and keeps the paper, docs and tests out
of the bundle, and the entry point imports the same app.
"""
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pins(path):
    out = set()
    for line in open(os.path.join(ROOT, path), encoding="utf-8"):
        line = line.split("#")[0].strip()
        if line:
            out.add(line.replace(" ", "").lower())
    return out


class VercelConfig(unittest.TestCase):
    def test_requirements_in_sync(self):
        backend = {p for p in pins("backend/requirements.txt") if not p.startswith("uvicorn")}
        self.assertEqual(pins("requirements.txt"), backend)

    def test_vercel_json(self):
        cfg = json.load(open(os.path.join(ROOT, "vercel.json"), encoding="utf-8"))
        self.assertEqual(cfg["rewrites"], [{"source": "/(.*)", "destination": "/api/index"}])
        fn = cfg["functions"]["api/index.py"]
        self.assertLessEqual(fn["maxDuration"], 300)          # Hobby plan maximum
        for d in ("paper/**", "docs/**", "backend/tests/**"):
            self.assertIn(d, fn["excludeFiles"])

    def test_entry_point_serves_the_app(self):
        src = open(os.path.join(ROOT, "api", "index.py"), encoding="utf-8-sig").read()
        self.assertIn("from main import app", src)


if __name__ == "__main__":
    unittest.main()
