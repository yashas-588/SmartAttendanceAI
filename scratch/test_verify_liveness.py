import os
import json
import uuid
import requests
import firebase_admin
from datetime import datetime, timezone, timedelta
from firebase_admin import credentials, firestore, auth

# 1. Initialize Firebase Admin with local CLI config fallback
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
        
        import json
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

# Student Info
STUDENT_UID = "6dUZjtD5mnc5uxIE08NhzEcojcm1"
STUDENT_EMAIL = "yashasr416@gmail.com"
API_KEY = "AIzaSyCDBG4wECmxqmrcX5dkhBAA_r6bFTg4KCE"
BASE_URL = "https://smart-attendance-ai-7139f.web.app"
TEST_PASS = "TestPassword123!"

# Clear liveness cooldown first
print("\n[0] Clearing liveness cooldown for student in Firestore...")
db.collection("liveness_cooldown").document(STUDENT_UID).delete()
print("✅ Cooldown document deleted.")

# 2. Update password of the student
print("\n[1] Temporarily setting password using Admin SDK...")
auth.update_user(STUDENT_UID, password=TEST_PASS)

# 3. Authenticate with REST API using email/password
print("[2] Signing in via Firebase Auth REST API to get ID token...")
url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={API_KEY}"
res = requests.post(url, json={"email": STUDENT_EMAIL, "password": TEST_PASS, "returnSecureToken": True})
res_data = res.json()
id_token = res_data.get("idToken")
if not id_token:
    print(f"Failed to get ID token: {res_data}")
    exit(1)
print("✅ Successfully authenticated. ID Token obtained.")

headers = {
    "Authorization": f"Bearer {id_token}",
    "Content-Type": "application/json"
}

# 4. Create a fresh active session in DB for testing
print("\n[3] Creating a fresh active session in Firestore...")
# Deactivate all active sessions first
for doc in db.collection("sessions").where("is_active", "==", True).stream():
    doc.reference.update({"is_active": False})

session_id = str(uuid.uuid4())
now_utc = datetime.now(timezone.utc)
expires_utc = now_utc + timedelta(hours=2) # 2 hours in future

session_data = {
    "teacher_id": "test_teacher_uid_for_verify_liveness",
    "class_id": "TEST-101",
    "subject": "Dynamic Trace Verification",
    "created_at": firestore.SERVER_TIMESTAMP,
    "expires_at": expires_utc,
    "is_active": True,
    "duration_minutes": 120,
    "location": None,
    "location_radius": 10,
    "attendance_count": 0,
}
db.collection("sessions").document(session_id).set(session_data)
print(f"✅ Created fresh active session: {session_id}")

# 5. Request challenge
print("\n[4] Requesting challenge sequence...")
req_data = {
    "session_id": session_id,
    "device_fingerprint": "mock_device_fingerprint_for_testing"
}
req_res = requests.post(f"{BASE_URL}/api/request-challenge", headers=headers, json=req_data)
print(f"Request Challenge Status: {req_res.status_code}")
print(f"Response Payload: {req_res.text}")

if req_res.status_code != 200:
    print("Failed to request challenge!")
    exit(1)

chal_data = req_res.json()
challenge_seq = chal_data.get("challenge_sequence")
challenge_token = chal_data.get("challenge_token")

# 6. Verify Liveness
print("\n[5] Calling verify-liveness with exact token and completed challenges...")
verify_data = {
    "session_id": session_id,
    "challenge_token": challenge_token,
    "device_fingerprint": "mock_device_fingerprint_for_testing",
    "completed_challenges": challenge_seq, # simulate successful completion of all challenges
    "attempt_duration": 15.5,
    "scores": {
        "quality": 1.0,
        "texture": 1.0,
        "biometric": 1.0,
        "head": 1.0,
        "blink": 1.0
    }
}
verify_res = requests.post(f"{BASE_URL}/api/verify-liveness", headers=headers, json=verify_data)
print(f"Verify Liveness Status: {verify_res.status_code}")
print(f"Response Payload: {verify_res.text}")

if verify_res.status_code == 200:
    liveness_token = verify_res.json().get("token")
    
    # 7. Mark Attendance
    print("\n[6] Calling mark-attendance...")
    mark_data = {
        "session_id": session_id,
        "liveness_token": liveness_token,
        "matched_name": "Yashas",
        "confidence": 0.95,
        "lat": None,
        "lon": None,
        "liveness_score": 100,
        "gps_accuracy": None
    }
    mark_res = requests.post(f"{BASE_URL}/api/mark-attendance", headers=headers, json=mark_data)
    print(f"Mark Attendance Status: {mark_res.status_code}")
    print(f"Response Payload: {mark_res.text}")
