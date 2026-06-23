import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase Admin with local CLI config fallback
if not firebase_admin._apps:
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

db = firestore.client()

print("\n=== 1. Classroom Settings Document ===")
settings_ref = db.collection("settings").document("classroom")
settings_doc = settings_ref.get()
if settings_doc.exists:
    print(json.dumps(settings_doc.to_dict(), indent=2, default=str))
else:
    print("Classroom settings document does not exist!")

print("\n=== 2. Active Sessions ===")
active_sess = db.collection("sessions").where("is_active", "==", True).stream()
count = 0
for s in active_sess:
    count += 1
    print(f"Session ID: {s.id}")
    print(json.dumps(s.to_dict(), indent=2, default=str))
    print("-" * 30)
if count == 0:
    print("No active sessions found.")

print("\n=== 3. Recent Attendance Logs ===")
logs = db.collection("attendance_logs").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(5).stream()
for l in logs:
    print(f"Log ID: {l.id}")
    print(json.dumps(l.to_dict(), indent=2, default=str))
    print("-" * 30)
