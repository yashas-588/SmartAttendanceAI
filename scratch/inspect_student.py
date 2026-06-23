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

doc_ref = db.collection("students").document("L0FGtvHqGKRrcW2gC6WTmj43dEK2")
doc = doc_ref.get()
if doc.exists:
    d = doc.to_dict()
    if 'descriptors' in d:
        d['descriptors'] = '[Present]'
    print(json.dumps(d, indent=2))
else:
    print("Student not found by doc.id. Searching by uid field...")
    docs = db.collection("students").where("uid", "==", "L0FGtvHqGKRrcW2gC6WTmj43dEK2").stream()
    for doc in docs:
        d = doc.to_dict()
        if 'descriptors' in d:
            d['descriptors'] = '[Present]'
        print(f"ID: {doc.id} => {json.dumps(d, indent=2, default=str)}")
