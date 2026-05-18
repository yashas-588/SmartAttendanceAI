import requests
import base64
import os

data = os.urandom(1000000)
b64 = base64.b64encode(data).decode('utf-8')
res = requests.post("https://3d8d9efc06f144.lhr.life/api/verify-liveness", json={"frames": [b64]})
print(res.status_code)
print(res.text)
