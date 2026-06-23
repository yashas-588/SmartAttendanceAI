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

print("\n--- ACTIVE SESSIONS AUDIT ---")
active_sess = db.collection("sessions").where("is_active", "==", True).stream()

count = 0
for s in active_sess:
    count += 1
    d = s.to_dict()
    print(f"Session ID: {s.id}")
    print(f"Subject: {d.get('subject')}")
    print(f"Location Value: {d.get('location')} (Type: {type(d.get('location'))})")
    print(f"Location Radius Value: {d.get('location_radius')} (Type: {type(d.get('location_radius'))})")
    print(f"Does 'location' key exist? {'location' in d}")
    print(f"Does 'location_radius' key exist? {'location_radius' in d}")
    print("Full Session Document JSON:")
    print(json.dumps(d, indent=2, default=str))
    print("=" * 60)

if count == 0:
    print("No active sessions found.")
