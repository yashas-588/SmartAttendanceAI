import os
import base64
import requests

def test_attendance():
    img_path = "/Users/yashasr/smart-attendance-ai-recovered/dataset/yashas/0.jpg"
    with open(img_path, "rb") as f:
        img_data = f.read()
    
    b64_str = base64.b64encode(img_data).decode('utf-8')
    payload = {"image": b64_str}
    
    print("Sending request...")
    try:
        res = requests.post("http://127.0.0.1:5005/api/mark-attendance", json=payload)
        print("Status Code:", res.status_code)
        print("Response JSON:", res.json())
    except Exception as e:
        print("Request failed:", e)

if __name__ == "__main__":
    test_attendance()
