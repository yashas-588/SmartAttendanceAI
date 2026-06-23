import os
import json
import requests
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime

# Initialize Firebase Admin
if not firebase_admin._apps:
    from google.oauth2.credentials import Credentials as OAuthCredentials
    class LocalCLICredential(credentials.Base):
        def __init__(self, google_cred):
            self.google_cred = google_cred
        def get_credential(self):
            return self.google_cred
    config_path = os.path.expanduser('~/.config/configstore/firebase-tools.json')
    with open(config_path, 'r') as f:
        cfg = json.load(f)
    refresh_token = cfg['tokens']['refresh_token']
    google_cred = OAuthCredentials(
        token=None,
        refresh_token=refresh_token,
        token_uri='https://oauth2.googleapis.com/token',
        client_id='563584335869-fgrhgmd47bqnekij5i8b5pr03ho849e6.apps.googleusercontent.com',
        client_secret='j9iVZfS8kkCEFUPaAeJV0sAi'
    )
    firebase_admin.initialize_app(
        LocalCLICredential(google_cred),
        options={'projectId': 'smart-attendance-ai-7139f'}
    )

db = firestore.client()

teacher_email = "admin@gmail.com"
teacher_password = "12345678"

# Sign in via email/password REST API
print(f"Authenticating as {teacher_email}...")
api_key = "AIzaSyCDBG4wECmxqmrcX5dkhBAA_r6bFTg4KCE" # From firebase-config.js
id_resp = requests.post(
    f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}",
    json={"email": teacher_email, "password": teacher_password, "returnSecureToken": True}
)
id_resp_data = id_resp.json()
if "idToken" not in id_resp_data:
    print(f"Failed to get ID token: {id_resp_data}")
    exit(1)

id_token = id_resp_data["idToken"]
teacher_uid = id_resp_data["localId"]
print(f"Authenticated successfully. UID: {teacher_uid}")

# Today's date in YYYY-MM-DD
today = datetime.now().strftime("%Y-%m-%d")

# Payload for creating extra class
payload = {
    "date": today,
    "start_time": "16:00",
    "end_time": "17:00",
    "subject": "c++",
    "class_id": "ISE Sem4 SecC",
    "room": "Room 303",
    "department": "ISE",
    "semester": "4",
    "section": "C"
}

print("\n--- API Request Payload Sent ---")
print(json.dumps(payload, indent=2))

# Post request to api
url = "https://smart-attendance-ai-7139f.web.app/api/scheduled-sessions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {id_token}"
}

print(f"\nSending POST request to: {url}...")
res = requests.post(url, json=payload, headers=headers)
print(f"Response Status Code: {res.status_code}")
print("\n--- API Response Body Received ---")
try:
    print(json.dumps(res.json(), indent=2))
except Exception:
    print(res.text)

# Query scheduled sessions to confirm it was created
sched_ref = db.collection("scheduled_sessions").where("teacher_id", "==", teacher_uid).where("date", "==", today).where("class_id", "==", "ISE Sem4 SecC").get()
print("\n--- Verification in Firestore ---")
if sched_ref:
    print("Found scheduled session in DB:")
    for doc in sched_ref:
        print(f"Document ID: {doc.id}")
        print(json.dumps(doc.to_dict(), indent=2, default=str))
else:
    print("Scheduled session not found in Firestore!")
