#!/usr/bin/env python3
import sys
import os
import json
from datetime import datetime, timezone

# Add src to python path so we can import our modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import firebase_admin
from firebase_admin import credentials, firestore
from google.oauth2.credentials import Credentials as OAuthCredentials

# Import our modified functions
from session_manager import create_session, end_session
from app import _ss_overlap

PROJECT_ID = "smart-attendance-ai-7139f"

# ── Init Firebase via CLI refresh token ─────────────────────────────────────
class LocalCLICredential(firebase_admin.credentials.Base):
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

if not firebase_admin._apps:
    firebase_admin.initialize_app(
        LocalCLICredential(google_cred),
        options={'projectId': PROJECT_ID}
    )

db = firestore.client()

def run_tests():
    print("\n" + "="*50)
    print("  RUNNING CONCURRENT-SESSION INTEGRATION TESTS")
    print("="*50 + "\n")

    teacher_id = "test_teacher_123"
    test_sessions = []
    test_scheduled = []

    try:
        # ──────────────────────────────────────────────────────────────────────
        # TEST 1: create_session() Compound Uniqueness Checks
        # ──────────────────────────────────────────────────────────────────────
        print("Test 1: create_session() Compound Uniqueness Checks...")
        
        # 1.1 Start first active session: CSE Sem4 SecA (DBMS)
        print("  Starting CSE Sem4 SecA (DBMS)...")
        res1 = create_session(
            db, teacher_id,
            class_id="CSE Sem4 SecA", subject="DBMS",
            duration_minutes=10, location=None, location_radius=10,
            department="CSE", semester="4", section="A"
        )
        if "error" in res1:
            raise Exception(f"Failed to start CSE Sem4 SecA (DBMS): {res1['error']}")
        sid1 = res1["session_id"]
        test_sessions.append(sid1)
        print("    Success!")

        # 1.2 Try starting duplicate session: CSE Sem4 SecA (duplicate check)
        print("  Attempting to start duplicate session: CSE Sem4 SecA (COA)...")
        res_dup = create_session(
            db, teacher_id,
            class_id="CSE Sem4 SecA", subject="COA",
            duration_minutes=10, location=None, location_radius=10,
            department="CSE", semester="4", section="A"
        )
        if "error" in res_dup:
            print(f"    Correctly blocked: {res_dup['error']}")
        else:
            test_sessions.append(res_dup["session_id"])
            raise Exception("ERROR: Allowed starting duplicate active session for same class!")

        # 1.3 Start concurrent session for different section: CSE Sem4 SecB (COA)
        print("  Starting concurrent section: CSE Sem4 SecB (COA)...")
        res2 = create_session(
            db, teacher_id,
            class_id="CSE Sem4 SecB", subject="COA",
            duration_minutes=10, location=None, location_radius=10,
            department="CSE", semester="4", section="B"
        )
        if "error" in res2:
            raise Exception(f"Failed to start CSE Sem4 SecB (COA): {res2['error']}")
        sid2 = res2["session_id"]
        test_sessions.append(sid2)
        print("    Success!")

        # 1.4 Start concurrent session for different semester: CSE Sem5 SecA (ML)
        print("  Starting concurrent semester: CSE Sem5 SecA (ML)...")
        res3 = create_session(
            db, teacher_id,
            class_id="CSE Sem5 SecA", subject="ML",
            duration_minutes=10, location=None, location_radius=10,
            department="CSE", semester="5", section="A"
        )
        if "error" in res3:
            raise Exception(f"Failed to start CSE Sem5 SecA (ML): {res3['error']}")
        sid3 = res3["session_id"]
        test_sessions.append(sid3)
        print("    Success!")

        print("\n  => All create_session() uniqueness validation tests PASSED ✓\n")

        # ──────────────────────────────────────────────────────────────────────
        # TEST 2: _ss_overlap() Filtering Checks
        # ──────────────────────────────────────────────────────────────────────
        print("Test 2: _ss_overlap() Overlap Checks...")

        # Add a mock scheduled session for CSE Sem4 SecA: 10:00 - 11:00
        print("  Adding mock scheduled session: CSE Sem4 SecA (10:00-11:00)...")
        ref1 = db.collection("scheduled_sessions").add({
            "teacher_id": teacher_id,
            "date": "2026-06-07",
            "start_time": "10:00",
            "end_time": "11:00",
            "subject": "DBMS",
            "class_id": "CSE Sem4 SecA",
            "department": "CSE",
            "semester": "4",
            "section": "A",
            "status": "scheduled"
        })
        test_scheduled.append(ref1[1].id)

        # 2.1 Overlap check for same class: CSE Sem4 SecA (10:30-11:30)
        print("  Checking overlap for same class: CSE Sem4 SecA (10:30-11:30)...")
        ov1 = _ss_overlap(db, teacher_id, "2026-06-07", "10:30", "11:30", "CSE", "4", "A")
        if ov1:
            print(f"    Correctly detected overlap with {ov1.get('subject')}")
        else:
            raise Exception("ERROR: Failed to detect overlap for same class!")

        # 2.2 Overlap check for different class/section: CSE Sem4 SecB (10:30-11:30)
        print("  Checking overlap for different section: CSE Sem4 SecB (10:30-11:30)...")
        ov2 = _ss_overlap(db, teacher_id, "2026-06-07", "10:30", "11:30", "CSE", "4", "B")
        if ov2:
            raise Exception(f"ERROR: Incorrectly flagged overlap for different section: {ov2.get('subject')}")
        else:
            print("    Correctly allowed (no overlap found) ✓")

        print("\n  => All _ss_overlap() validations PASSED ✓\n")

    finally:
        # Clean up Firestore database after test runs
        print("Cleaning up test documents...")
        for sid in test_sessions:
            print(f"  Ending active session {sid}...")
            db.collection("sessions").document(sid).update({"is_active": False})
        for scid in test_scheduled:
            print(f"  Deleting mock scheduled session {scid}...")
            db.collection("scheduled_sessions").document(scid).delete()
        print("Cleanup done.")

    print("\n" + "="*50)
    print("  ALL TESTS PASSED SUCCESSFULLY!")
    print("="*50 + "\n")

if __name__ == "__main__":
    run_tests()
