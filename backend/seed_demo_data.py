import urllib.request, json, os

# Login
data = json.dumps({'username':'admin','password':'admin123'}).encode()
req = urllib.request.Request('http://127.0.0.1:8000/api/auth/login', data=data, headers={'Content-Type':'application/json'})
res = json.loads(urllib.request.urlopen(req).read())
token = res['token']

# Read sample CSV
csv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'sample_data', 'sample_inventory.csv'))
with open(csv_path, 'rb') as f:
    file_bytes = f.read()

# Build multipart form data
boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
body = (
    f'--{boundary}\r\n'
    f'Content-Disposition: form-data; name="file"; filename="sample_inventory.csv"\r\n'
    f'Content-Type: text/csv\r\n\r\n'
).encode('utf-8') + file_bytes + f'\r\n--{boundary}--\r\n'.encode('utf-8')

req = urllib.request.Request(
    'http://127.0.0.1:8000/api/upload',
    data=body,
    headers={
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Authorization': f'Bearer {token}'
    }
)
try:
    resp = urllib.request.urlopen(req)
    print('Upload success:', json.loads(resp.read()))
except urllib.error.HTTPError as e:
    print('Upload error:', e.code, e.read().decode())
