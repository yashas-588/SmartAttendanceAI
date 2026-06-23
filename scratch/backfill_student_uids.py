import os
import json
import firebase_admin
from firebase_admin import credentials, firestore, auth

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

print("\n--- BACKFILLING UNLINKED STUDENT DOCUMENTS ---")
students_ref = db.collection("students")
docs = list(students_ref.stream())

backfilled_count = 0
total_students = len(docs)

for doc in docs:
    d = doc.to_dict()
    doc_id = doc.id
    email = d.get("email")
    uid = d.get("uid")
    
    print(f"Checking doc: {doc_id} | Name: {d.get('name')} | Email: {email} | Current UID: {uid}")
    
    if not email:
        print("  Skipping: No email address.")
        continue
        
    email_clean = email.strip().lower()
    
    # Check if UID is missing or empty
    if not uid:
        try:
            # Query Firebase Auth to find the user with this email
            user = auth.get_user_by_email(email_clean)
            target_uid = user.uid
            
            # Update the student document in Firestore
            doc.reference.update({"uid": target_uid})
            print(f"  ✅ BACKFILLED: Matched email '{email_clean}' with Firebase Auth UID '{target_uid}'")
            backfilled_count += 1
        except auth.UserNotFoundError:
            print(f"  ❌ Skipping: No Firebase Auth user found for email '{email_clean}'")
        except Exception as ex:
            print(f"  ⚠️ Error fetching/updating user: {ex}")
    else:
        # Check if the UID actually exists in Firebase Auth to confirm link integrity
        try:
            user = auth.get_user(uid)
            print(f"  Integrity confirmed: UID '{uid}' exists in Firebase Auth for email '{user.email}'")
        except auth.UserNotFoundError:
            print(f"  ⚠️ INTEGRITY WARNING: Document has UID '{uid}' but no matching Firebase Auth user exists!")

print(f"\nDone. Processed {total_students} student documents. Successfully backfilled {backfilled_count} records.")
