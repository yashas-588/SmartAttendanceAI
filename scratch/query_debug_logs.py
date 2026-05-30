import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase Admin with local CLI config fallback
if not firebase_admin._apps:
    try:
        firebase_admin.initialize_app(options={
            'projectId': 'smart-attendance-ai-7139f'
        })
        firestore.client()
    except Exception as e:
        print(f"Default credentials failed: {e}. Trying local firebase CLI token...")
        if firebase_admin._apps:
            try:
                firebase_admin.delete_app(firebase_admin._apps['[default]'] if '[default]' in firebase_admin._apps else list(firebase_admin._apps.values())[0])
            except Exception:
                pass
        
        from google.oauth2.credentials import Credentials as OAuthCredentials
        
        class LocalCLICredential(firebase_admin.credentials.Base):
            def __init__(self, google_cred):
                self.google_cred = google_cred
            def get_credential(self):
                return self.google_cred
        
        config_path = os.path.expanduser('~/.config/configstore/firebase-tools.json')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            refresh_token = cfg.get('tokens', {}).get('refresh_token')
            if refresh_token:
                google_cred = OAuthCredentials(
                    token=None,
                    refresh_token=refresh_token,
                    token_uri='https://oauth2.googleapis.com/token',
                    client_id='563584335869-fgrhgmd47bqnekij5i8b5pr03ho849e6.apps.googleusercontent.com',
                    client_secret='j9iVZfS8kkCEFUPaAeJV0sAi'
                )
                firebase_admin.initialize_app(
                    LocalCLICredential(google_cred),
                    options={
                        'projectId': 'smart-attendance-ai-7139f'
                    }
                )
                print("✅ Firebase initialized with CLI credentials fallback")

db = firestore.client()

STUDENT_UID = "6dUZjtD5mnc5uxIE08NhzEcojcm1"

print("\n--- QUERYING Liveness Debug Logs ---")
docs = db.collection("liveness_debug_logs")\
         .where("student_uid", "==", STUDENT_UID)\
         .limit(50)\
         .stream()

docs_list = []
for doc in docs:
    docs_list.append(doc.to_dict())

docs_list.sort(key=lambda x: x.get("timestamp") or datetime.min, reverse=True)

for d in docs_list[:10]:
    timestamp_str = d.get("timestamp").isoformat() if d.get("timestamp") else "None"
    print(f"[{timestamp_str}] Event: {d.get('event_type')}")
    print(f"  Details: {d.get('details')}")
    print(f"  Session ID: {d.get('session_id')}")
    print(f"  Fingerprint: {d.get('device_fingerprint')}")
    print("-" * 50)
