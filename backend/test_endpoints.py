"""Manual debugging script (not part of the unit-test suite).
Requires a running server and a session token:
    set IIP_TOKEN=<token from POST /api/auth/login>   (Windows)
    export IIP_TOKEN=<token>                          (Linux/macOS)
"""
import os as _os
AUTH_HEADERS = {"Authorization": "Bearer " + _os.environ.get("IIP_TOKEN", "")}
import http.client

conn = http.client.HTTPConnection('127.0.0.1', 8000)
paths = [
    '/api/batches',
    '/api/dashboard/latest/summary',
    '/api/dashboard/latest/items?page_size=5',
    '/api/dashboard/latest/filters',
]
for path in paths:
    conn.request('GET', path, headers=AUTH_HEADERS)
    res = conn.getresponse()
    data = res.read().decode('utf-8', errors='replace')
    print(path, res.status)
    print(data[:2000])
    print('---')
