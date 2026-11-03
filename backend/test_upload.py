"""Manual debugging script (not part of the unit-test suite).
Requires a running server and a session token:
    set IIP_TOKEN=<token from POST /api/auth/login>   (Windows)
    export IIP_TOKEN=<token>                          (Linux/macOS)
"""
import os as _os
AUTH_HEADERS = {"Authorization": "Bearer " + _os.environ.get("IIP_TOKEN", "")}
import http.client
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
file_path = os.path.join(BASE_DIR, '..', 'sample_data', 'sample_inventory.csv')
with open(file_path, 'rb') as f:
    file_data = f.read()

boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
lines = []
lines.append(f'--{boundary}')
lines.append('Content-Disposition: form-data; name="file"; filename="sample_inventory.csv"')
lines.append('Content-Type: text/csv')
lines.append('')
lines.append(file_data)
lines.append(f'--{boundary}--')
lines.append('')

body = b''
for item in lines:
    if isinstance(item, str):
        body += item.encode('utf-8') + b'\r\n'
    else:
        body += item + b'\r\n'

conn = http.client.HTTPConnection('127.0.0.1', 8000)
headers = {
    'Content-Type': f'multipart/form-data; boundary={boundary}',
    'Content-Length': str(len(body)),
    **AUTH_HEADERS,
}
conn.request('POST', '/api/upload', body, headers)
res = conn.getresponse()
print(res.status, res.reason)
print(res.read().decode('utf-8', errors='replace'))
