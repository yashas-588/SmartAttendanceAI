import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase
if not firebase_admin._apps:
    from google.oauth2.credentials import Credentials as OAuthCredentials
    config_path = os.path.expanduser('~/.config/configstore/firebase-tools.json')
    with open(config_path, 'r', encoding='utf-8') as f:
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
        LocalCLICredential := type('LocalCLICredential', (credentials.Base,), {
            '__init__': lambda s, c: setattr(s, 'google_cred', c),
            'get_credential': lambda s: s.google_cred
        })(google_cred),
        options={'projectId': 'smart-attendance-ai-7139f'}
    )

db = firestore.client()

print("\n--- Liveness Debug Logs ---")
docs = db.collection("liveness_debug_logs").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(10).stream()
for d in docs:
    print(f"ID: {d.id} => {json.dumps(d.to_dict(), indent=2, default=str)}")
    print("-" * 50)

print("\n--- Attendance Logs ---")
docs = db.collection("attendance_logs").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(10).stream()
for d in docs:
    print(f"ID: {d.id} => {json.dumps(d.to_dict(), indent=2, default=str)}")
    print("-" * 50)

print("\n--- Attendance ---")
docs = db.collection("attendance").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(10).stream()
for d in docs:
    print(f"ID: {d.id} => {json.dumps(d.to_dict(), indent=2, default=str)}")
    print("-" * 50)
