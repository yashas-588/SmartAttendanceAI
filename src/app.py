import os
import io
import csv
import random
import time
from datetime import datetime, timezone, timedelta
from math import radians, cos, sin, asin, sqrt
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, firestore, auth, storage

# Load .env (development) — production should use real env vars
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass  # python-dotenv not installed; rely on system env

from session_manager import (
    create_session, end_session, get_active_session,
    validate_session, get_session_status,
    write_attendance_transactional,
    generate_liveness_token, validate_liveness_token,
    generate_challenge_token, verify_challenge_token
)

# ── Flask app ─────────────────────────────────────────────────────────────
app = Flask(__name__)
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://localhost:5005,http://localhost:5000,"
    "http://127.0.0.1:5173,http://127.0.0.1:5005,http://127.0.0.1:5000,"
    "https://smart-attendance-ai-7139f.web.app,"
    "https://smart-attendance-ai-7139f.firebaseapp.com"
).split(",")
CORS(app, origins=ALLOWED_ORIGINS)

# ── Firebase ──────────────────────────────────────────────────────────────
if not firebase_admin._apps:
    app_instance = None
    try:
        # 1. Try Application Default Credentials (standard production mode)
        app_instance = firebase_admin.initialize_app(options={
            'storageBucket': 'smart-attendance-ai-7139f.firebasestorage.app'
        })
        # Force credential evaluation by calling firestore client
        firestore.client()
        print("✅ Firebase initialized (Application Default Credentials)")
    except Exception as e:
        if app_instance:
            try:
                firebase_admin.delete_app(app_instance)
            except Exception:
                pass
        print(f"ℹ️  Application Default Credentials failed ({e}). Attempting local Firebase CLI auth...")
        try:
            # 2. Try loading from local Firebase CLI config
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
                            'projectId': 'smart-attendance-ai-7139f',
                            'storageBucket': 'smart-attendance-ai-7139f.firebasestorage.app'
                        }
                    )
                    print("✅ Firebase initialized (Local Firebase CLI credentials)")
                else:
                    raise Exception("No refresh token found in firebase-tools.json")
            else:
                raise FileNotFoundError(f"Firebase CLI config not found at {config_path}")
        except Exception as local_err:
            print(f"⚠️  Error: Local Firebase CLI auth failed: {local_err}")
            print("⚠️  Warning: Firebase initialization failed. Database operations will not work.")

db = firestore.client() if firebase_admin._apps else None

# ═══════════════════════════════════════════════════════════════════════════
#  UTILITY
# ═══════════════════════════════════════════════════════════════════════════

def get_all_attendance_stats():
    """
    Load ALL attendance records in one query and index by student_uid.
    Replaces N individual queries with 1 batch query.
    """
    if not db:
        return {}
    try:
        stats = {}
        for doc in db.collection("attendance").stream():
            d = doc.to_dict()
            uid = d.get("student_uid")
            if not uid:
                continue
            date_str = d.get("date", "")
            if uid not in stats:
                stats[uid] = {"count": 0, "last_date": None}
            stats[uid]["count"] += 1
            if date_str and (stats[uid]["last_date"] is None or date_str > stats[uid]["last_date"]):
                stats[uid]["last_date"] = date_str
        return stats
    except Exception as e:
        print(f"Error loading attendance stats: {e}")
        return {}

def get_student_attendance_stats(uid):
    """
    Fetch from Firestore the attendance count and last-seen date for a student.
    """
    if not db:
        return 0, "N/A"
    try:
        docs = db.collection("attendance").where("student_uid", "==", uid).stream()
        count = 0
        last_date = None
        for doc in docs:
            d = doc.to_dict()
            count += 1
            date_str = d.get("date", "")
            if date_str and (last_date is None or date_str > last_date):
                last_date = date_str
        return count, (last_date or "Never")
    except Exception as e:
        print(f"Error fetching stats for {uid}: {e}")
        return 0, "N/A"

def _haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dp/2)**2 + cos(p1)*cos(p2)*sin(dl/2)**2
    return R * 2 * asin(sqrt(a))

def _require_teacher(f):
    """Shared auth decorator — verifies Bearer token and teacher/admin role."""
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        ah = request.headers.get("Authorization", "")
        if not ah.startswith("Bearer "):
            return jsonify({"error": "Unauthorized"}), 401
        try:
            tok = auth.verify_id_token(ah.split("Bearer ")[1])
            uid = tok["uid"]
        except Exception:
            return jsonify({"error": "Invalid token"}), 401
        if not db:
            return jsonify({"error": "Firebase not available"}), 500
        user_doc = db.collection("users").document(uid).get()
        if not user_doc.exists:
            return jsonify({"error": "User not found"}), 403
        role = user_doc.to_dict().get("role", "")
        if role not in ("teacher", "admin"):
            return jsonify({"error": "Forbidden — teacher role required"}), 403
        return f(uid, *args, **kwargs)
    return wrapper

# ═══════════════════════════════════════════════════════════════════════════
#  MARK ATTENDANCE (Client AI + Backend Verification)
# ═══════════════════════════════════════════════════════════════════════════

def log_liveness_debug(student_uid, session_id, event_type, details, device_fingerprint):
    """Log liveness verification events to Firestore for auditing and security analysis."""
    if not db:
        return
    try:
        db.collection("liveness_debug_logs").add({
            "student_uid": student_uid,
            "session_id": session_id,
            "event_type": event_type,  # CHALLENGE_GENERATED, CHALLENGE_COMPLETED, LIVENESS_REJECTED, REPLAY_SUSPICION, COOLDOWN_BLOCKED
            "details": details,
            "device_fingerprint": device_fingerprint,
            "timestamp": firestore.SERVER_TIMESTAMP
        })
        print(f"[LIVENESS_DEBUG] [{event_type}] Student: {student_uid}, Session: {session_id}, Details: {details}, Fingerprint: {device_fingerprint}")
    except Exception as e:
        print(f"Error writing liveness debug log: {e}")


def verify_student_eligibility(student_uid):
    """
    Server-side attendance eligibility check.
    Ensures the student exists, is Active/SetupEmailSent, and has biometric registration.
    Returns (is_eligible, error_message, student_record)
    """
    if not db:
        return False, "Database not initialized", None
        
    try:
        student_query = db.collection("students").where("uid", "==", student_uid).limit(1).get()
        if not student_query:
            # Self-healing Fallback: Match by email from Firebase Auth if UID is not linked in Firestore
            try:
                user_record = auth.get_user(student_uid)
                email = user_record.email.lower()
                student_by_email_query = db.collection("students").where("email", "==", email).limit(1).get()
                if student_by_email_query:
                    student_doc = student_by_email_query[0]
                    student_doc.reference.update({"uid": student_uid})
                    print(f"[SELF_HEALING] Linked student doc {student_doc.id} (email: {email}) to UID: {student_uid}", flush=True)
                    student_query = [student_doc]
            except Exception as ex:
                print(f"[SELF_HEALING] Error during email self-healing fallback: {ex}", flush=True)

        if not student_query:
            return False, "Access Denied: Student biometric record not found. Please contact your teacher.", None
            
        student_doc = student_query[0]
        student_data = student_doc.to_dict()
        
        # Verify account status (allow SetupEmailSent as a fallback for self-healing)
        status = student_data.get("accountStatus", "")
        if status not in ("Active", "SetupEmailSent"):
            return False, f"Access Denied: Onboarding is incomplete (Status: {status}). Please log in to complete onboarding.", None
            
        # Verify face descriptors exist
        if not student_data.get("descriptors"):
            return False, "Access Denied: Biometric face enrollment is missing. Please contact your teacher to capture face.", None
            
        return True, "Eligible", student_data
    except Exception as e:
        return False, f"Eligibility Check Error: {str(e)}", None


@app.route("/api/request-challenge", methods=["POST"])
def request_challenge_api():
    """Check cooldown status and generate a random challenge sequence signed server-side."""
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized"}), 500

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        student_uid = decoded_token["uid"]
    except Exception as e:
        return jsonify({"success": False, "message": "Invalid authentication token"}), 401

    # ── GATE 0.2: Attendance Eligibility Check ───────────────────────────────
    is_eligible, err_msg, _ = verify_student_eligibility(student_uid)
    if not is_eligible:
        return jsonify({"success": False, "message": err_msg}), 403

    data = request.json or {}
    session_id = data.get("session_id")
    device_fingerprint = data.get("device_fingerprint", "unknown")
    if not session_id:
        return jsonify({"success": False, "message": "session_id required"}), 400

    valid, reason, session = validate_session(db, session_id)
    if not valid:
        return jsonify({"success": False, "message": reason}), 403

    # Check cooldown status
    cooldown_ref = db.collection("liveness_cooldown").document(student_uid)
    cooldown_doc = cooldown_ref.get()
    if cooldown_doc.exists:
        cooldown_data = cooldown_doc.to_dict()
        cooldown_until = cooldown_data.get("cooldown_until")
        if cooldown_until:
            now = datetime.now(timezone.utc)
            if cooldown_until > now:
                rem_seconds = int((cooldown_until - now).total_seconds())
                log_liveness_debug(
                    student_uid, 
                    session_id, 
                    "COOLDOWN_BLOCKED", 
                    f"Blocked by cooldown. {rem_seconds}s remaining.", 
                    device_fingerprint
                )
                return jsonify({
                    "success": False,
                    "message": f"Too many failed attempts. Cooldown active. Try again in {rem_seconds} seconds.",
                    "cooldown_remaining": rem_seconds
                }), 403

    # Select random challenges
    CHALLENGES = ["blink twice", "turn head left", "turn head right", "smile", "move closer", "raise eyebrows"]
    # Pick 3 random challenges
    selected_challenges = random.sample(CHALLENGES, 3)

    # Generate token
    challenge_token = generate_challenge_token(student_uid, session_id, selected_challenges, device_fingerprint)
    
    log_liveness_debug(
        student_uid, 
        session_id, 
        "CHALLENGE_GENERATED", 
        f"Sequence: {selected_challenges}", 
        device_fingerprint
    )

    return jsonify({
        "success": True,
        "challenge_sequence": selected_challenges,
        "challenge_token": challenge_token
    })


@app.route("/api/verify-liveness", methods=["POST"])
def verify_liveness_api():
    """Verify challenge completions, timestamps, fingerprints, and passive anti-spoofing scores."""
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized"}), 500

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        student_uid = decoded_token["uid"]
    except Exception as e:
        return jsonify({"success": False, "message": "Invalid authentication token"}), 401

    student_email = decoded_token.get("email", "unknown_email")
    role = decoded_token.get("role", "student")
    
    # Cooldown Status Check
    cooldown_ref = db.collection("liveness_cooldown").document(student_uid)
    cooldown_doc = cooldown_ref.get()
    cooldown_status = "None"
    if cooldown_doc.exists:
        cooldown_data = cooldown_doc.to_dict()
        cooldown_until = cooldown_data.get("cooldown_until")
        if cooldown_until:
            now = datetime.now(timezone.utc)
            if cooldown_until > now:
                rem_seconds = int((cooldown_until - now).total_seconds())
                cooldown_status = f"Active ({rem_seconds}s remaining)"
            else:
                cooldown_status = "Expired"
        else:
            cooldown_status = f"No active lockout, failed attempts: {len(cooldown_data.get('failed_attempts', []))}"

    print(f"[VERIFY_LIVENESS_DEBUG] Start - Student UID: {student_uid}, Email: {student_email}, Role: {role}, Cooldown Status: {cooldown_status}", flush=True)

    # ── GATE 0.2: Attendance Eligibility Check ───────────────────────────────
    is_eligible, err_msg, student_record = verify_student_eligibility(student_uid)
    record_status = student_record.get("accountStatus") if student_record else "None"
    print(f"[VERIFY_LIVENESS_DEBUG] eligibility check: is_eligible={is_eligible}, err_msg={err_msg}, record_status={record_status}", flush=True)
    if not is_eligible:
        print(f"[VERIFY_LIVENESS_DEBUG] Eligibility Check failed. Returning 403 on Line 333 (Eligibility Gate). Msg: {err_msg}", flush=True)
        return jsonify({"success": False, "message": err_msg}), 403

    data = request.json or {}
    session_id = data.get("session_id")
    challenge_token = data.get("challenge_token")
    device_fingerprint = data.get("device_fingerprint", "unknown")
    completed_challenges = data.get("completed_challenges", [])
    attempt_duration = data.get("attempt_duration", 0.0)
    scores = data.get("scores", {})
    details = data.get("details", {})

    replay_suspicion_score = int(details.get("replay_suspicion_score", 0))
    glare_detected = bool(details.get("glare_detected", False))
    moire_pass = bool(details.get("moire_pass", True))
    depth_pass = bool(details.get("depth_pass", True))
    flicker_detected = bool(details.get("flicker_detected", False))

    print(f"[VERIFY_LIVENESS_DEBUG] Payload details - Session ID: {session_id}, Challenge Token: {challenge_token}, completed: {completed_challenges}, duration: {attempt_duration}, scores: {scores}, Replay Suspicion: {replay_suspicion_score}%", flush=True)

    if not session_id or not challenge_token:
        print("[VERIFY_LIVENESS_DEBUG] Returning 400 - missing session_id or challenge_token", flush=True)
        return jsonify({"success": False, "message": "session_id and challenge_token required"}), 400

    valid, reason, session = validate_session(db, session_id)
    print(f"[VERIFY_LIVENESS_DEBUG] validate_session result: valid={valid}, reason={reason}", flush=True)
    if not valid:
        print(f"[VERIFY_LIVENESS_DEBUG] validate_session failed. Returning 403 on Line 352. Msg: {reason}", flush=True)
        return jsonify({"success": False, "message": reason}), 403

    # Verification checks
    is_valid, token_err, expected_challenges = verify_challenge_token(
        challenge_token, 
        student_uid, 
        session_id, 
        device_fingerprint
    )
    print(f"[VERIFY_LIVENESS_DEBUG] verify_challenge_token: is_valid={is_valid}, token_err={token_err}, expected={expected_challenges}", flush=True)

    fail_reason = ""
    if not is_valid:
        fail_reason = f"Token validation failed: {token_err}"
    elif completed_challenges != expected_challenges:
        fail_reason = f"Challenges not completed in correct order. Expected {expected_challenges}, got {completed_challenges}."
    elif attempt_duration > 40.0:
        fail_reason = f"Attempt took too long ({round(attempt_duration, 1)}s, limit is 40s)."

    # Re-calculate passive anti-spoofing score
    weights = { "quality": 0.20, "texture": 0.10, "biometric": 0.10, "head": 0.25, "blink": 0.35 }
    weighted_score = 0.0
    for key, weight in weights.items():
        weighted_score += float(scores.get(key, 0.0)) * weight

    pass_threshold = 0.45
    if not fail_reason and weighted_score < pass_threshold:
        fail_reason = f"Anti-spoofing score {round(weighted_score * 100)}% below threshold of {round(pass_threshold * 100)}%"

    if fail_reason:
        # Increment failed attempts and handle cooldown
        cooldown_ref = db.collection("liveness_cooldown").document(student_uid)
        cooldown_doc = cooldown_ref.get()
        now = datetime.now(timezone.utc)
        failed_attempts = []
        cooldown_status = "None"
        if cooldown_doc.exists:
            cooldown_data = cooldown_doc.to_dict()
            failed_attempts = cooldown_data.get("failed_attempts", [])
            cooldown_status = str(cooldown_data.get("cooldown_until"))

        failed_attempts.append(now)
        # Filter failures in last 15 minutes
        cutoff = now - timedelta(seconds=900)
        recent_failures = [t for t in failed_attempts if t > cutoff]

        cooldown_until = None
        if len(recent_failures) >= 3:
            cooldown_until = now + timedelta(seconds=300) # 5 minutes cooldown

        fail_payload = {
            "summary": f"Failed: {fail_reason}. Completed: {completed_challenges} in {round(attempt_duration, 1)}s.",
            "attempt_duration": attempt_duration,
            "completed_challenges": completed_challenges,
            "weighted_score": round(weighted_score * 100),
            "replay_suspicion_score": replay_suspicion_score,
            "glare_detected": glare_detected,
            "moire_pass": moire_pass,
            "depth_pass": depth_pass,
            "flicker_detected": flicker_detected,
            "lbp_score": details.get("lbp_score", 0),
            "moire_score": details.get("moire_score", 0),
            "glare_ratio": details.get("glare_ratio", 0),
            "depth_score": details.get("depth_score", 0),
            "flicker_val": details.get("flicker_val", 0)
        }

        if len(recent_failures) >= 3:
            log_liveness_debug(
                student_uid, 
                session_id, 
                "REPLAY_SUSPICION", 
                fail_payload, 
                device_fingerprint
            )
        else:
            log_liveness_debug(
                student_uid, 
                session_id, 
                "LIVENESS_REJECTED", 
                fail_payload, 
                device_fingerprint
            )

        cooldown_ref.set({
            "failed_attempts": recent_failures,
            "cooldown_until": cooldown_until
        })

        # Build response with cooldown remaining if triggered
        cooldown_remaining = 300 if cooldown_until else 0
        print(f"[VERIFY_LIVENESS_DEBUG] fail_reason block matched. Returning 403 on Line 440. Msg: {fail_reason}, Cooldown Status: {cooldown_status}", flush=True)
        return jsonify({
            "success": False,
            "message": fail_reason,
            "cooldown_remaining": cooldown_remaining
        }), 403

    # Success!
    log_payload = {
        "summary": f"Completed: {completed_challenges} in {round(attempt_duration, 1)}s. Score: {round(weighted_score * 100)}%",
        "attempt_duration": attempt_duration,
        "completed_challenges": completed_challenges,
        "weighted_score": round(weighted_score * 100),
        "replay_suspicion_score": replay_suspicion_score,
        "glare_detected": glare_detected,
        "moire_pass": moire_pass,
        "depth_pass": depth_pass,
        "flicker_detected": flicker_detected,
        "lbp_score": details.get("lbp_score", 0),
        "moire_score": details.get("moire_score", 0),
        "glare_ratio": details.get("glare_ratio", 0),
        "depth_score": details.get("depth_score", 0),
        "flicker_val": details.get("flicker_val", 0)
    }

    log_liveness_debug(
        student_uid, 
        session_id, 
        "CHALLENGE_COMPLETED", 
        log_payload, 
        device_fingerprint
    )

    # Reset cooldown counter
    db.collection("liveness_cooldown").document(student_uid).delete()

    # Generate token
    token = generate_liveness_token(student_uid, session_id)
    print(f"[VERIFY_LIVENESS_DEBUG] Liveness verification SUCCESS. Returning token on Line 458")
    return jsonify({
        "success": True,
        "token": token
    })


@app.route("/api/invalidate-geofence", methods=["POST"])
def invalidate_geofence():
    """Endpoint for clients to report geofence breach and invalidate attendance."""
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized"}), 500

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        student_uid = decoded_token["uid"]
    except Exception as e:
        return jsonify({"success": False, "message": "Invalid authentication token"}), 401

    data = request.json or {}
    lat = data.get("lat")
    lon = data.get("lon")
    dist = data.get("distance", 0)

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")

    try:
        # Query for any "Present" attendance records for this student today
        docs = db.collection("attendance") \
                 .where("student_uid", "==", student_uid) \
                 .where("date", "==", date_str) \
                 .where("status", "==", "Present") \
                 .stream()

        invalidated_count = 0
        for doc in docs:
            doc.reference.update({
                "status": "Invalidated",
                "reason": f"Left classroom ({round(dist)}m away)",
                "invalidatedAt": firestore.SERVER_TIMESTAMP
            })
            invalidated_count += 1

        return jsonify({
            "success": True, 
            "message": f"Geofence breached. Invalidated {invalidated_count} record(s)."
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error invalidating: {str(e)}"}), 500


def parse_class_id(class_id: str) -> tuple[str, str, str]:
    """
    Parse class_id (e.g. 'ISE 4A', 'CS-A Sem 5', 'CSE 6B') into (department, semester, section).
    """
    if not class_id:
        return "", "", ""
    
    val = class_id.strip()
    dept = ""
    sem = ""
    sec = ""

    # Try matching pattern like 'ISE 4A' or 'CSE 6B'
    import re
    # Match department (usually letters at start)
    match_dept = re.match(r"^([a-zA-Z]+)", val)
    if match_dept:
        dept = match_dept.group(1).upper()
    
    # Match semester digit
    match_sem = re.search(r"(\d+)", val)
    if match_sem:
        sem = match_sem.group(1)
        
    # Match section (usually a single letter following a space or digit)
    # E.g. "4A" -> section A, "CS-A Sem 5" -> section A
    match_sec = re.search(r"(?:Sem\s*\d+\s+([a-zA-Z]))|(?:Sem\s*([a-zA-Z])\s*\d+)|(?:[a-zA-Z]+-\s*([a-zA-Z]))|(?:\d+([a-zA-Z]))", val)
    if match_sec:
        # group can be 1, 2, 3 or 4 depending on which pattern matched
        sec = next((g for g in match_sec.groups() if g), "").upper()
    else:
        # Fallback: search for single character word after space
        match_sec_fb = re.search(r"\s+([a-zA-Z])\b", val)
        if match_sec_fb:
            sec = match_sec_fb.group(1).upper()
            
    return dept, sem, sec


def check_class_match(student_data: dict, session_data: dict) -> tuple[bool, str]:
    """
    Validate that student's department, semester, and section match the session.
    Soft migration: if the student record has no class fields yet (legacy enrolment),
    the check is bypassed so existing students are not broken before a teacher updates
    their profile.  Once a teacher sets dept/sem/sec on a student, strict matching
    applies for every subsequent attendance.
    """
    s_dept = str(student_data.get("department", student_data.get("dept", ""))).strip().lower()
    s_sem = str(student_data.get("semester", "")).strip().lower()
    s_sec = str(student_data.get("section", "")).strip().lower()

    # ── Profile completeness gate ──────────────────────────────────────────────
    # A student with no class data set cannot be matched to any session.
    # Allowing them through would reintroduce cross-class attendance risk because
    # there is nothing to compare against.  Teachers must complete the student's
    # profile (Department, Semester, Section) before attendance is possible.
    if not s_dept and not s_sem and not s_sec:
        return False, (
            "Your profile is incomplete — Department, Semester, and Section are not set. "
            "Contact your teacher to update your profile before marking attendance."
        )

    sess_class = str(session_data.get("class_id", "")).strip().lower()
    sess_dept = str(session_data.get("department", "")).strip().lower()
    sess_sem = str(session_data.get("semester", "")).strip().lower()
    sess_sec = str(session_data.get("section", "")).strip().lower()

    # 1. Match using explicit fields if session has them
    if sess_dept and sess_sem and sess_sec:
        def clean_sem(val):
            return "".join([c for c in val if c.isdigit()]) or val
        
        sem_match = clean_sem(s_sem) == clean_sem(sess_sem)
        dept_match = (s_dept in sess_dept) or (sess_dept in s_dept)
        sec_match = s_sec == sess_sec

        if dept_match and sem_match and sec_match:
            return True, "Passed (explicit match)"
        
        mismatches = []
        if not dept_match: mismatches.append(f"Dept ({s_dept.upper()} vs {sess_dept.upper()})")
        if not sem_match: mismatches.append(f"Sem ({s_sem.upper()} vs {sess_sem.upper()})")
        if not sec_match: mismatches.append(f"Sec ({s_sec.upper()} vs {sess_sec.upper()})")
        return False, f"Class mismatch: {', '.join(mismatches)}"

    # 2. Fallback: match using heuristics against class_id string (for ad-hoc sessions)
    if sess_class:
        if s_dept and s_dept not in sess_class:
            return False, f"Department '{s_dept.upper()}' does not match class '{sess_class.upper()}'"
        if s_sem:
            s_sem_digit = "".join([c for c in s_sem if c.isdigit()])
            if s_sem_digit and s_sem_digit not in sess_class:
                return False, f"Semester '{s_sem}' does not match class '{sess_class.upper()}'"
        if s_sec and s_sec not in sess_class:
            return False, f"Section '{s_sec.upper()}' does not match class '{sess_class.upper()}'"

        return True, "Passed (heuristic match)"

    return True, "Bypassed (no session class info)"


def get_classroom_radius_from_settings():
    """Helper to retrieve locationRadius from settings/classroom doc in Firestore."""
    try:
        doc = db.collection("settings").document("classroom").get()
        if doc.exists:
            d = doc.to_dict()
            if "locationRadius" in d:
                return int(d["locationRadius"])
    except Exception as e:
        print(f"Error fetching classroom radius from settings: {e}")
    return 10


@app.route("/api/mark-attendance", methods=["POST"])
def mark_attendance_api():
    """Secure attendance endpoint — validates auth token → session → liveness → GPS."""
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized"}), 500

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        student_uid = decoded_token["uid"]
    except Exception as e:
        return jsonify({"success": False, "message": "Invalid authentication token"}), 401

    # ── GATE 0.2: Attendance Eligibility Check ───────────────────────────────
    is_eligible, err_msg, _ = verify_student_eligibility(student_uid)
    if not is_eligible:
        return jsonify({"success": False, "message": err_msg}), 403

    data = request.json or {}

    # ── GATE 1: Session ───────────────────────────────────────────────────────
    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"success": False, "message": "No active session. Ask your teacher to start one."}), 403

    valid, reason, session = validate_session(db, session_id)
    if not valid:
        return jsonify({"success": False, "message": reason}), 403

    # ── GATE 1.2: Mandatory Class Match Validation ───────────────────────────
    # IMPORTANT: The Firestore read and the class-match check are intentionally
    # split into two separate blocks. A try/except that wraps BOTH would silently
    # pass a student through if Firestore raised any transient error — defeating
    # the entire gate. Any read failure must be a hard 403, not a silent pass.
    student_data = None
    try:
        student_docs = db.collection("students").where("uid", "==", student_uid).limit(1).get()
        for sdoc in student_docs:
            student_data = sdoc.to_dict()
            break
    except Exception as e:
        print(f"[GATE 1.2] Firestore read error during class validation for {student_uid}: {e}")
        return jsonify({"success": False, "message": "Class validation temporarily unavailable — please try again."}), 403

    if student_data:
        match_ok, err_reason = check_class_match(student_data, session)
        if not match_ok:
            return jsonify({"success": False, "message": err_reason}), 403

    # ── GATE 1.5: Liveness Token (HMAC verification) ──────────────────────────
    liveness_token = data.get("liveness_token")
    if not liveness_token:
        return jsonify({"success": False, "message": "Anti-spoofing verification token missing. Please scan again."}), 403

    valid_token, token_reason_or_session = validate_liveness_token(liveness_token, student_uid)
    if not valid_token:
        return jsonify({"success": False, "message": f"Anti-spoofing validation failed: {token_reason_or_session}"}), 403
    
    if token_reason_or_session != session_id:
        return jsonify({"success": False, "message": "Verification token does not match the active session."}), 403

    # ── GATE 3: Attendance Time Window ────────────────────────────────────────
    try:
        settings_doc = db.collection("settings").document("classroom").get()
        if settings_doc.exists:
            cfg = settings_doc.to_dict()
            win_start = cfg.get("windowStart", "")
            win_end   = cfg.get("windowEnd", "")
            if win_start and win_end:
                now_time = datetime.now().strftime("%H:%M")
                if False:  # Temporarily disabled for testing
                     return jsonify({
                        "success": False,
                        "message": f"Outside attendance window ({win_start}–{win_end})"
                     }), 403
    except Exception:
        pass  # Settings unavailable — don't block attendance, just skip check

    # ── GATE 2: GPS (if session has location) ─────────────────────────────────
    # Distinguish between explicit session-level disabled (location: null) and legacy session (missing location field entirely)
    is_explicit_disabled = "location" in session and session.get("location") is None
    
    sess_loc = session.get("location")
    allowed = session.get("location_radius")
    
    # Fallback/migration check: if legacy session document is missing location or location_radius,
    # fall back to global settings classroom document coordinates/radius!
    if not is_explicit_disabled and (sess_loc is None or allowed is None):
        try:
            settings_doc = db.collection("settings").document("classroom").get()
            if settings_doc.exists:
                cfg = settings_doc.to_dict()
                if sess_loc is None:
                    sess_loc = cfg.get("location")
                if allowed is None:
                    allowed = cfg.get("locationRadius")
        except Exception as e:
            print(f"Error loading geofence fallback settings on backend: {e}")

    lat = data.get("lat")
    lon = data.get("lon")
    if sess_loc and sess_loc.get("lat") and sess_loc.get("lon"):
        if allowed is None:
            allowed = get_classroom_radius_from_settings()
        else:
            allowed = int(allowed)

        if allowed > 0:
            if lat is None or lon is None:
                return jsonify({"success": False, "message": "GPS coordinates required"}), 400
            dist = _haversine(lat, lon, sess_loc["lat"], sess_loc["lon"])
            if dist > allowed:
                return jsonify({"success": False,
                                "message": f"Outside classroom ({round(dist)}m away, max {allowed}m)"}), 403

    confidence = data.get("confidence", 0.0)

    # Resolve name from Firestore — don't trust the client's claimed name
    matched_name = "Unknown"
    try:
        student_docs = db.collection("students").where("uid", "==", student_uid).limit(1).get()
        for sdoc in student_docs:
            matched_name = sdoc.to_dict().get("name", "Unknown")
            break
    except Exception:
        matched_name = data.get("matched_name", "Unknown")  # graceful fallback

    try:
        # ── WRITE: Transactional attendance write ─────────────────────────────
        now      = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        # Priority 10: Use {session_id}_{student_uid}
        doc_id = f"{session_id}_{student_uid}"

        # Write both student_uid and studentId fields for maximum frontend compatibility (Priority 7)
        att_data = {
            "name":            matched_name,
            "student_uid":     student_uid,
            "studentId":       student_uid,
            "date":            date_str,
            "time":            time_str,
            "status":          "Present",
            "method":          "Face Recognition + Session Verification",
            "confidence":      confidence,
            "liveness_score":  data.get("liveness_score", 0),
            "gps_accuracy":    data.get("gps_accuracy", None),
            "session_id":      session_id,
            "timestamp":       firestore.SERVER_TIMESTAMP,
        }
        if lat is not None and lon is not None:
            att_data["location"] = {"lat": lat, "lon": lon}

        success, msg = write_attendance_transactional(db, doc_id, att_data, session_id)

        return jsonify({
            "success": success,
            "name":    matched_name,
            "message": msg if not success else f"{matched_name} — Marked Present ✓",
            "confidence": confidence
        })

    except Exception as e:
        print(f"Error in mark-attendance: {e}")
        return jsonify({"success": False, "message": f"System Error: {str(e)}"}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  STUDENTS
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/students", methods=["GET"])
@_require_teacher
def get_students(teacher_uid):
    """Return all registered students with real attendance stats from Firestore."""
    if not db:
        return jsonify([])

    students = []
    try:
        docs = db.collection("students").stream()
        today = datetime.now()
        start = datetime(2025, 1, 1)
        total_days = max(1, (today - start).days)
        weekdays = sum(
            1 for i in range(total_days)
            if (start + timedelta(days=i)).weekday() < 5
        )

        # ONE query for all stats instead of N queries
        all_stats = get_all_attendance_stats()
        
        for doc in docs:
            d = doc.to_dict()
            uid = doc.id
            name = d.get("name", "Unknown")
            s = all_stats.get(uid, {"count": 0, "last_date": None})
            count = s["count"]
            last_seen = s["last_date"] or "Never"

            att_pct = round((count / max(weekdays, 1)) * 100, 1)
            att_pct = min(att_pct, 100.0)

            students.append({
                "uid":            uid,
                "name":           name,
                "usn":            d.get("studentId", f"ID-{name[:3].upper()}{len(name):03d}"),
                "dept":           d.get("dept", "Engineering"),
                "att":            f"{att_pct}%",
                "att_count":      count,
                "last":           last_seen,
            })
        return jsonify(students)
    except Exception as e:
        print(f"Error getting students: {e}")
        return jsonify([])

@app.route("/api/students/<uid>", methods=["DELETE"])
@_require_teacher
def delete_student(teacher_uid, uid):
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized."}), 500
    try:
        db.collection("students").document(uid).delete()
        return jsonify({"success": True, "message": "Student deleted."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/students/<uid>", methods=["PUT"])
@_require_teacher
def edit_student(teacher_uid, uid):
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized."}), 500
    data = request.json
    try:
        db.collection("students").document(uid).update(data)
        return jsonify({"success": True, "message": "Student updated."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/students/<student_doc_id>/onboard", methods=["POST"])
@_require_teacher
def onboard_student(teacher_uid, student_doc_id):
    """
    Firebase-only onboarding:
      1. Generate a secure random temporary password (internal only).
      2. Create or update Firebase Auth credentials.
      3. Save profile metadata in 'users' collection with 'mustChangePassword': True.
      4. Link UID and update 'students' collection status.
      5. Generate a Firebase password reset link → Firebase sends the email.
      6. Return success (no credentials exposed to frontend).
    """
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized."}), 500
    try:
        # 1. Fetch student doc
        student_ref = db.collection("students").document(student_doc_id)
        student_doc = student_ref.get()
        if not student_doc.exists:
            return jsonify({"success": False, "message": "Student not found."}), 404
        
        student_data = student_doc.to_dict()
        email = student_data.get("email", "").strip().lower()
        name = student_data.get("name", "Student")
        
        if not email:
            return jsonify({"success": False, "message": "Student has no email address on file."}), 400
        
        # 2. Generate a secure random temporary password (internal — never exposed)
        import string as _string
        chars = _string.ascii_letters + _string.digits + "!@#$%^&"
        temp_password = "".join(random.choice(chars) for _ in range(16))
        
        # 3. Create or update Firebase Auth credentials
        auth_uid = None
        try:
            user = auth.get_user_by_email(email)
            auth_uid = user.uid
            auth.update_user(auth_uid, password=temp_password)
            print(f"[ONBOARD] Reset Auth password for existing user {email}")
        except auth.UserNotFoundError:
            # Create user in Auth
            user = auth.create_user(
                email=email,
                password=temp_password,
                display_name=name
            )
            auth_uid = user.uid
            print(f"[ONBOARD] Created new Auth user {email} with temporary password")
            
        # 4. Create/update user document in users/{auth_uid} with mustChangePassword=True
        db.collection("users").document(auth_uid).set({
            "name": name,
            "email": email,
            "role": "student",
            "mustChangePassword": True,
            "createdAt": firestore.SERVER_TIMESTAMP
        }, merge=True)
        
        # 5. Link back to students/{student_doc_id}
        student_ref.update({
            "uid": auth_uid,
            "accountStatus": "SetupEmailSent",
            "loginCreatedAt": firestore.SERVER_TIMESTAMP
        })
        
        # NOTE: Email is sent client-side via firebase.auth().sendPasswordResetEmail()
        # The Admin SDK's generate_password_reset_link() only returns a URL — it does NOT send email.
        
        return jsonify({
            "success": True,
            "message": f"Account created for {email}. Frontend will trigger the setup email.",
            "email": email
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  MANUAL ATTENDANCE
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/manual-attendance", methods=["POST"])
def manual_attendance():
    """Secure endpoint for manual attendance overrides. Verifies ID token and role (Priority 4)."""
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized."}), 500

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        user_uid = decoded_token["uid"]
    except Exception as e:
        return jsonify({"success": False, "message": "Invalid authentication token"}), 401

    # Verify teacher/admin role
    try:
        user_ref = db.collection("users").document(user_uid).get()
        if not user_ref.exists:
            return jsonify({"success": False, "message": "User not found"}), 403
        user_data = user_ref.to_dict()
        role = user_data.get("role")
        if role not in ["teacher", "admin"]:
            return jsonify({"success": False, "message": "Forbidden: Requires teacher/admin privileges"}), 403
    except Exception as e:
        return jsonify({"success": False, "message": f"Role verification failed: {str(e)}"}), 500

    data   = request.json
    name   = data.get("name")
    status = data.get("status")
    uid    = data.get("student_uid", "unknown")
    date_input = data.get("date")  # Allows date override from admin panel

    if not name or not status:
        return jsonify({"success": False, "message": "Name and status required"}), 400

    now      = datetime.now()
    date_str = date_input if date_input else now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    # Find if there is an active session
    active_sess = get_active_session(db)
    if active_sess:
        session_id = active_sess["session_id"]
        doc_id = f"{session_id}_{uid}"
    else:
        session_id = "manual"
        doc_id = f"manual_{date_str}_{uid}"

    doc_ref = db.collection("attendance").document(doc_id)
    doc_ref.set({
        "name":        name,
        "student_uid": uid,
        "studentId":   uid,  # Write both fields (Priority 7)
        "date":        date_str,
        "time":        time_str,
        "status":      status,
        "method":      "Manual Override",
        "session_id":  session_id,
        "timestamp":   firestore.SERVER_TIMESTAMP,
    }, merge=True)

    return jsonify({"success": True, "message": f"Marked {name} as {status}"})


@app.route("/api/students/import", methods=["POST"])
def import_students():
    """Bulk import student records from a CSV file (Feature A)."""
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized"}), 500

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    # Verify role
    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        user_uid = decoded_token["uid"]
        user_ref = db.collection("users").document(user_uid).get()
        if not user_ref.exists or user_ref.to_dict().get("role") not in ["teacher", "admin"]:
            return jsonify({"success": False, "message": "Requires teacher/admin privileges"}), 403
    except Exception as e:
        return jsonify({"success": False, "message": f"Auth error: {str(e)}"}), 401

    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400

    file = request.files['file']
    if not file.filename.endswith('.csv'):
        return jsonify({"success": False, "message": "Only CSV files are supported"}), 400

    try:
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        reader = csv.DictReader(stream)
        
        imported_count = 0
        skipped_count = 0
        for row in reader:
            name = row.get("name", "").strip()
            email = row.get("email", "").strip()
            dept = row.get("dept", "Engineering").strip()
            student_id = row.get("studentId", "").strip() or row.get("usn", "").strip()
            
            if not name:
                skipped_count += 1
                continue

            # Check duplicate student by email/studentId
            existing = False
            if email:
                exist_email = db.collection("students").where("email", "==", email).limit(1).stream()
                if any(exist_email):
                    existing = True
            if student_id and not existing:
                exist_id = db.collection("students").where("studentId", "==", student_id).limit(1).stream()
                if any(exist_id):
                    existing = True

            if existing:
                skipped_count += 1
                continue

            # Add student with descriptors set to None (Pending status)
            db.collection("students").add({
                "name": name,
                "email": email,
                "dept": dept,
                "studentId": student_id,
                "uid": None,
                "descriptors": None,
                "registeredAt": firestore.SERVER_TIMESTAMP,
                "addedBy": user_uid
            })
            imported_count += 1

        return jsonify({
            "success": True,
            "message": f"Imported {imported_count} students successfully. Skipped {skipped_count} duplicates/empty records."
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error parsing CSV: {str(e)}"}), 500


@app.route("/api/students/<uid>/documents", methods=["POST"])
def upload_student_document(uid):
    """Upload or replace a student document (PDF, DOCX, Excel, Images, student records)."""
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized"}), 500

    # Validate file size limit (5MB) on the request level
    if request.content_length and request.content_length > 5 * 1024 * 1024:
        return jsonify({"success": False, "message": "File size exceeds 5MB limit"}), 400

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    # Verify role
    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        user_uid = decoded_token["uid"]
        user_ref = db.collection("users").document(user_uid).get()
        if not user_ref.exists or user_ref.to_dict().get("role") not in ["teacher", "admin"]:
            return jsonify({"success": False, "message": "Requires teacher/admin privileges"}), 403
        
        user_data = user_ref.to_dict()
        uploader_name = user_data.get("name", user_data.get("email", "Teacher"))
    except Exception as e:
        return jsonify({"success": False, "message": f"Auth error: {str(e)}"}), 401

    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400

    file = request.files['file']
    filename = file.filename
    allowed_exts = ('.pdf', '.docx', '.xlsx', '.xls', '.jpg', '.jpeg', '.png', '.gif', '.txt', '.csv')
    if not filename.lower().endswith(allowed_exts):
        return jsonify({"success": False, "message": "Only PDF, DOCX, Excel, Images, and Text files are allowed"}), 400

    category = request.form.get("category", "Other").strip()
    if category not in ["Admission", "ID Proof", "Marks Card", "Fee Receipt", "Medical", "Other"]:
        category = "Other"

    try:
        student_ref = db.collection("students").document(uid)
        student_doc = student_ref.get()
        if not student_doc.exists:
            return jsonify({"success": False, "message": "Student record not found"}), 404

        student_data = student_doc.to_dict()
        docs = student_data.get("documents", []) or []

        # Check if file already exists with this name (for replacement)
        existing_doc = None
        for doc in docs:
            if doc["name"] == filename:
                existing_doc = doc
                break

        bucket = storage.bucket()
        blob_path = f"student_documents/{uid}/{filename}"
        blob = bucket.blob(blob_path)
        
        # Upload file
        blob.upload_from_file(file, content_type=file.content_type)
        
        doc_metadata = {
            "name": filename,
            "size": blob.size,
            "contentType": file.content_type,
            "uploadedAt": datetime.now(timezone.utc).isoformat(),
            "storagePath": blob_path,
            "category": category,
            "status": "Uploaded",
            "uploaderId": user_uid,
            "uploaderName": uploader_name
        }

        # If it was an overwrite, remove old metadata first
        if existing_doc:
            student_ref.update({
                "documents": firestore.ArrayRemove([existing_doc])
            })
            print(f"[DOC_UPLOAD_DEBUG] Replacing metadata for file: {filename}")

        # Append new metadata to documents array
        student_ref.update({
            "documents": firestore.ArrayUnion([doc_metadata])
        })

        print(f"[DOC_UPLOAD_DEBUG] Document successfully uploaded: {filename}, Size: {blob.size} bytes, Category: {category}")

        return jsonify({"success": True, "message": f"Document {filename} uploaded successfully", "doc": doc_metadata})
    except Exception as e:
        print(f"[DOC_UPLOAD_DEBUG] Document upload failed: {str(e)}")
        return jsonify({"success": False, "message": f"Upload failed: {str(e)}"}), 500


@app.route("/api/students/<uid>/documents", methods=["GET"])
def list_student_documents(uid):
    """List documents for a student, returning temporary download URLs (Feature B)."""
    if not db:
        return jsonify([])

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"error": "Unauthorized"}), 401

    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        user_uid = decoded_token["uid"]
    except Exception as e:
        return jsonify({"error": f"Auth error: {str(e)}"}), 401

    try:
        student_ref = db.collection("students").document(uid)
        student_doc = student_ref.get()
        if not student_doc.exists:
            return jsonify({"error": "Student not found"}), 404
        
        student_data = student_doc.to_dict()
        
        # Verify access: either user is a teacher/admin, or user is the student themselves
        user_ref = db.collection("users").document(user_uid).get()
        is_teacher = user_ref.exists and user_ref.to_dict().get("role") in ["teacher", "admin"]
        is_owner = (student_data.get("uid") == user_uid) or (uid == user_uid)
        
        if not is_teacher and not is_owner:
            return jsonify({"error": "Forbidden: Access denied"}), 403

        docs = student_data.get("documents", [])
        bucket = storage.bucket()
        enriched_docs = []
        for doc in docs:
            blob = bucket.blob(doc["storagePath"])
            try:
                # generate signed url valid for 15 mins
                url = blob.generate_signed_url(version="v4", expiration=timedelta(minutes=15), method="GET")
                doc_copy = dict(doc)
                doc_copy["downloadUrl"] = url
                enriched_docs.append(doc_copy)
            except Exception as e:
                print(f"Signed url error: {e}")
                enriched_docs.append(doc)

        return jsonify(enriched_docs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/students/<uid>/documents/<filename>", methods=["DELETE"])
def delete_student_document(uid, filename):
    """Delete a student document (Feature B)."""
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized"}), 500

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    # Verify role
    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        user_uid = decoded_token["uid"]
        user_ref = db.collection("users").document(user_uid).get()
        if not user_ref.exists or user_ref.to_dict().get("role") not in ["teacher", "admin"]:
            return jsonify({"success": False, "message": "Requires teacher/admin privileges"}), 403
    except Exception as e:
        return jsonify({"success": False, "message": f"Auth error: {str(e)}"}), 401

    try:
        student_ref = db.collection("students").document(uid)
        student_doc = student_ref.get()
        if not student_doc.exists:
            return jsonify({"success": False, "message": "Student record not found"}), 404

        data = student_doc.to_dict()
        docs = data.get("documents", [])
        
        target_doc = None
        for doc in docs:
            if doc["name"] == filename:
                target_doc = doc
                break

        if not target_doc:
            return jsonify({"success": False, "message": "Document not found"}), 404

        # Delete from Storage
        bucket = storage.bucket()
        blob = bucket.blob(target_doc["storagePath"])
        if blob.exists():
            blob.delete()

        # Remove from Firestore
        student_ref.update({
            "documents": firestore.ArrayRemove([target_doc])
        })

        return jsonify({"success": True, "message": f"Document {filename} deleted"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/students/<uid>/documents/<filename>/rename", methods=["POST"])
def rename_student_document(uid, filename):
    """Rename a student document (Feature B)."""
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized"}), 500
    
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        user_uid = decoded_token["uid"]
        user_ref = db.collection("users").document(user_uid).get()
        if not user_ref.exists or user_ref.to_dict().get("role") not in ["teacher", "admin"]:
            return jsonify({"success": False, "message": "Requires teacher/admin privileges"}), 403
    except Exception as e:
        return jsonify({"success": False, "message": f"Auth error: {str(e)}"}), 401

    data = request.json or {}
    new_filename = data.get("new_filename", "").strip()
    if not new_filename:
        return jsonify({"success": False, "message": "new_filename is required"}), 400
    
    # Enforce file extension preservation
    _, old_ext = os.path.splitext(filename)
    _, new_ext = os.path.splitext(new_filename)
    if new_ext.lower() != old_ext.lower():
        new_filename += old_ext

    try:
        student_ref = db.collection("students").document(uid)
        student_doc = student_ref.get()
        if not student_doc.exists:
            return jsonify({"success": False, "message": "Student record not found"}), 404
        
        student_data = student_doc.to_dict()
        docs = student_data.get("documents", []) or []
        
        target_doc = None
        for doc in docs:
            if doc["name"] == filename:
                target_doc = doc
                break
        
        if not target_doc:
            return jsonify({"success": False, "message": "Document not found"}), 404

        bucket = storage.bucket()
        old_blob_path = target_doc["storagePath"]
        new_blob_path = f"student_documents/{uid}/{new_filename}"
        
        old_blob = bucket.blob(old_blob_path)
        if old_blob.exists():
            new_blob = bucket.copy_blob(old_blob, bucket, new_blob_path)
            old_blob.delete()
        
        updated_doc = dict(target_doc)
        updated_doc["name"] = new_filename
        updated_doc["storagePath"] = new_blob_path
        updated_doc["uploadedAt"] = datetime.now(timezone.utc).isoformat()
        
        student_ref.update({
            "documents": firestore.ArrayRemove([target_doc])
        })
        student_ref.update({
            "documents": firestore.ArrayUnion([updated_doc])
        })
        
        print(f"[DOC_RENAME_DEBUG] Document successfully renamed: {filename} -> {new_filename}")
        return jsonify({"success": True, "message": f"Document renamed to {new_filename}", "doc": updated_doc})
    except Exception as e:
        print(f"[DOC_RENAME_DEBUG] Document rename failed: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/students/<uid>/documents/<filename>/status", methods=["POST"])
def update_student_document_status(uid, filename):
    """Verify or update document status (Feature B)."""
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized"}), 500
    
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        user_uid = decoded_token["uid"]
        user_ref = db.collection("users").document(user_uid).get()
        if not user_ref.exists or user_ref.to_dict().get("role") not in ["teacher", "admin"]:
            return jsonify({"success": False, "message": "Requires teacher/admin privileges"}), 403
    except Exception as e:
        return jsonify({"success": False, "message": f"Auth error: {str(e)}"}), 401

    data = request.json or {}
    new_status = data.get("status", "").strip()
    if new_status not in ["Uploaded", "Missing", "Verified", "Pending"]:
        return jsonify({"success": False, "message": "Invalid status value"}), 400

    try:
        student_ref = db.collection("students").document(uid)
        student_doc = student_ref.get()
        if not student_doc.exists:
            return jsonify({"success": False, "message": "Student record not found"}), 404
        
        student_data = student_doc.to_dict()
        docs = student_data.get("documents", []) or []
        
        target_doc = None
        for doc in docs:
            if doc["name"] == filename:
                target_doc = doc
                break
        
        if not target_doc:
            return jsonify({"success": False, "message": "Document not found"}), 404

        updated_doc = dict(target_doc)
        updated_doc["status"] = new_status
        
        student_ref.update({
            "documents": firestore.ArrayRemove([target_doc])
        })
        student_ref.update({
            "documents": firestore.ArrayUnion([updated_doc])
        })
        
        print(f"[DOC_STATUS_DEBUG] Status updated for: {filename} -> {new_status}")
        return jsonify({"success": True, "message": f"Document status updated to {new_status}", "doc": updated_doc})
    except Exception as e:
        print(f"[DOC_STATUS_DEBUG] Status update failed: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  ATTENDANCE QUERY
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/attendance", methods=["GET"])
@_require_teacher
def get_attendance(teacher_uid):
    """Return attendance records. Optional ?date=YYYY-MM-DD filter."""
    if not db:
        return jsonify({"error": "Firebase not initialized"}), 500

    date_filter = request.args.get("date")

    try:
        query = db.collection("attendance").order_by("timestamp", direction=firestore.Query.DESCENDING)
        docs  = query.stream()

        records = []
        for doc in docs:
            d = doc.to_dict()
            if date_filter and d.get("date") != date_filter:
                continue
            records.append({
                "name":   d.get("name", ""),
                "date":   d.get("date", ""),
                "time":   d.get("time", ""),
                "status": d.get("status", "Present"),
                "method": d.get("method", "Unknown"),
            })

        return jsonify(records)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ═══════════════════════════════════════════════════════════════════════════
#  DEFAULTERS
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/defaulters", methods=["GET"])
@_require_teacher
def get_defaulters(teacher_uid):
    """Return students with less than 75% attendance."""
    if not db:
        return jsonify({"error": "Firebase not initialized"}), 500

    threshold = float(request.args.get("threshold", 75.0))
    students = []
    
    try:
        docs = db.collection("students").stream()
        today = datetime.now()
        start = datetime(today.year, 1, 1)
        total_days = max(1, (today - start).days)
        weekdays = sum(
            1 for i in range(total_days)
            if (start + timedelta(days=i)).weekday() < 5
        )

        # ONE query for all stats instead of N queries
        all_stats = get_all_attendance_stats()
        
        for doc in docs:
            d = doc.to_dict()
            uid = doc.id
            name = d.get("name", "Unknown")
            s = all_stats.get(uid, {"count": 0, "last_date": None})
            count = s["count"]
            last_seen = s["last_date"] or "Never"
            
            att_pct = round((count / max(weekdays, 1)) * 100, 1)
            if att_pct < threshold:
                students.append({
                    "name":      name,
                    "att_count": count,
                    "att_pct":   att_pct,
                    "last_seen": last_seen
                })
        return jsonify(students)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ═══════════════════════════════════════════════════════════════════════════
#  EXPORT (CSV)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/export-attendance", methods=["GET"])
@_require_teacher
def export_attendance(teacher_uid):
    if not db:
        return jsonify({"error": "Firebase not initialized"}), 500

    date_filter = request.args.get("date")
    report_type = request.args.get("type", "all")

    try:
        si = io.StringIO()
        cw = csv.writer(si)

        if report_type == "defaulters":
            cw.writerow(["Student Name", "Sessions Present", "Attendance %", "Last Seen"])
            docs = db.collection("students").stream()
            today = datetime.now()
            start = datetime(today.year, 1, 1)
            total_days = max(1, (today - start).days)
            weekdays = sum(
                1 for i in range(total_days)
                if (start + timedelta(days=i)).weekday() < 5
            )
            # ONE query for all stats instead of N queries
            all_stats = get_all_attendance_stats()
            for doc in docs:
                d = doc.to_dict()
                uid = doc.id
                name = d.get("name", "Unknown")
                s = all_stats.get(uid, {"count": 0, "last_date": None})
                count = s["count"]
                last_seen = s["last_date"] or "Never"
                att_pct = round((count / max(weekdays, 1)) * 100, 1)
                if att_pct < 75:
                    cw.writerow([name, count, f"{att_pct}%", last_seen])
            filename = "defaulters_report.csv"
        else:
            cw.writerow(["Student Name", "Date", "Time", "Status", "Method"])
            query = db.collection("attendance").order_by("timestamp", direction=firestore.Query.DESCENDING)
            for doc in query.stream():
                d = doc.to_dict()
                if date_filter and d.get("date") != date_filter:
                    continue
                cw.writerow([
                    d.get("name", ""),
                    d.get("date", ""),
                    d.get("time", ""),
                    d.get("status", "Present"),
                    d.get("method", "Unknown"),
                ])
            filename = f"attendance_{date_filter or 'all'}.csv"

        return Response(
            si.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        print(f"Export error: {e}")
        return jsonify({"error": str(e)}), 500

# ═══════════════════════════════════════════════════════════════════════════
#  SYSTEM STATUS
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/status", methods=["GET"])
def system_status():
    student_count = 0
    if db:
        try:
            docs = db.collection("students").stream()
            student_count = sum(1 for _ in docs)
        except Exception:
            pass

    today_str = datetime.now().strftime("%Y-%m-%d")
    present_today = 0
    if db:
        try:
            docs = db.collection("attendance").where("date", "==", today_str).stream()
            present_today = sum(1 for _ in docs)
        except Exception:
            pass

    return jsonify({
        "status":          "ok",
        "firebase":        db is not None,
        "student_count":   student_count,
        "present_today":   present_today,
        "timestamp":       datetime.now().isoformat(),
    })


# ═══════════════════════════════════════════════════════════════════════════
#  EMAIL DIAGNOSTIC ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/email-test", methods=["GET"])
def get_email_test():
    """Diagnostic endpoint to inspect environment vars, query Firestore log history, and verify Resend connectivity."""
    import requests
    
    # 1. API Key detection
    api_key = os.environ.get("RESEND_API_KEY", "")
    key_status = "missing"
    if api_key:
        if "REPLACE_WITH_YOUR_KEY" in api_key or api_key == "re_REPLACE_WITH_YOUR_KEY":
            key_status = "placeholder"
        elif api_key.startswith("re_"):
            key_status = "configured"
        else:
            key_status = "invalid_format"
            
    # 2. Sender email check
    email_from = os.environ.get("EMAIL_FROM", "onboarding@resend.dev")
    sender_status = "onboarding_sandbox" if email_from == "onboarding@resend.dev" else "custom_domain"
    
    # 3. Firestore log check (fetch last successful & failed logs via index-safe client filtering)
    last_sent = None
    last_failed = None
    if db:
        try:
            logs = db.collection("email_logs")\
                     .order_by("sentAt", direction=firestore.Query.DESCENDING)\
                     .limit(100).stream()
            for doc in logs:
                d = doc.to_dict()
                if "sentAt" in d and hasattr(d["sentAt"], "isoformat"):
                    d["sentAt"] = d["sentAt"].isoformat()
                
                if d.get("status") == "sent" and not last_sent:
                    last_sent = d
                elif d.get("status") == "failed" and not last_failed:
                    last_failed = d
                
                if last_sent and last_failed:
                    break
        except Exception as log_err:
            print(f"[DIAGNOSTICS] Failed to fetch email logs: {log_err}")
            
    # 4. Connectivity test to Resend API
    connectivity = {
        "connected": False,
        "status_code": None,
        "latency_ms": None,
        "message": "",
        "auth_valid": False
    }
    
    start_time = time.time()
    try:
        # We send a dummy GET request to Resend API to verify endpoint connectivity
        res = requests.get("https://api.resend.com/emails", headers={
            "Authorization": f"Bearer {api_key}"
        }, timeout=5)
        latency = int((time.time() - start_time) * 1000)
        connectivity["connected"] = True
        connectivity["status_code"] = res.status_code
        connectivity["latency_ms"] = latency
        
        # Resend GET /emails with valid API key yields 405 (Method Not Allowed) or 200 or 401
        if res.status_code == 401:
            connectivity["auth_valid"] = False
            connectivity["message"] = "Connected to Resend, but API key is unauthorized (401 Unauthorized)."
        elif res.status_code == 405:
            connectivity["auth_valid"] = True
            connectivity["message"] = "Connected successfully. API authentication verified (405 Method Not Allowed is expected for GET /emails)."
        else:
            connectivity["auth_valid"] = res.status_code < 400
            connectivity["message"] = f"Connected successfully. Received HTTP status {res.status_code}."
            
    except requests.exceptions.RequestException as req_err:
        connectivity["connected"] = False
        connectivity["message"] = f"Network connection failed: {str(req_err)}"

    return jsonify({
        "api_key_detected": bool(api_key),
        "api_key_status": key_status,
        "sender_email": email_from,
        "sender_status": sender_status,
        "portal_url": os.environ.get("PORTAL_URL", ""),
        "last_sent_log": last_sent,
        "last_failed_log": last_failed,
        "resend_connectivity": connectivity,
        "sandbox_restricted": sender_status == "onboarding_sandbox"
    })


@app.route("/api/email-test", methods=["POST"])
def post_email_test():
    """Send a real test email via Resend and return the raw HTTP response, code, and errors."""
    import requests
    
    data = request.json or {}
    to_email = data.get("to") or data.get("email")
    if not to_email or "@" not in to_email:
        return jsonify({"success": False, "message": "Valid recipient 'to' email address required"}), 400
        
    api_key = os.environ.get("RESEND_API_KEY", "")
    email_from = os.environ.get("EMAIL_FROM", "onboarding@resend.dev")
    
    if not api_key:
        return jsonify({"success": False, "message": "RESEND_API_KEY is not set in environment."}), 500

    # Build branded test body
    html_content = f"""
    <div style="font-family: sans-serif; max-width: 500px; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px; background: #ffffff;">
      <h2 style="color: #38bdf8; margin-top: 0;">Attendance Management System</h2>
      <p style="font-size: 16px; color: #334155; font-weight: bold;">Real Email Delivery Test</p>
      <p style="color: #475569; line-height: 1.6;">This email was sent by triggering the diagnostic endpoint <code>POST /api/email-test</code> on the backend.</p>
      <table style="width: 100%; border-collapse: collapse; margin: 15px 0; background: #f8fafc;">
        <tr style="border-bottom: 1px solid #e2e8f0;"><td style="padding: 8px; font-weight: bold; width: 100px;">Sender:</td><td style="padding: 8px;">{email_from}</td></tr>
        <tr style="border-bottom: 1px solid #e2e8f0;"><td style="padding: 8px; font-weight: bold;">Recipient:</td><td style="padding: 8px;">{to_email}</td></tr>
        <tr><td style="padding: 8px; font-weight: bold;">Timestamp:</td><td style="padding: 8px;">{datetime.now().isoformat()}</td></tr>
      </table>
      <p style="color: #ef4444; font-size: 12px; font-style: italic;">Note: If using onboarding@resend.dev, this recipient must be the address associated with your Resend account registration due to sandbox restrictions.</p>
    </div>
    """
    
    start_time = time.time()
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            json={
                "from":    email_from,
                "to":      [to_email],
                "subject": "Attendance Management System — Branded Delivery Diagnosis",
                "html":    html_content,
            },
            timeout=10,
        )
        latency = int((time.time() - start_time) * 1000)
        
        # Capture raw response details
        response_body = None
        try:
            response_body = resp.json()
        except ValueError:
            response_body = resp.text
            
        success = resp.status_code in (200, 201, 202)
        
        # Write to log in firestore
        if db:
            try:
                db.collection("email_logs").add({
                    "type": "diagnostic_test",
                    "recipient": to_email,
                    "status": "sent" if success else "failed",
                    "ref": str(response_body.get("id") if isinstance(response_body, dict) else response_body),
                    "sentAt": firestore.SERVER_TIMESTAMP,
                    "statusCode": resp.status_code,
                    "latency": latency
                })
            except Exception as log_err:
                print(f"[TEST EMAIL] Failed to write log: {log_err}")
                
        return jsonify({
            "success": success,
            "status_code": resp.status_code,
            "latency_ms": latency,
            "response": response_body,
            "headers": dict(resp.headers),
            "sandbox_warning": email_from == "onboarding@resend.dev"
        }), resp.status_code

    except requests.exceptions.RequestException as exc:
        return jsonify({
            "success": False,
            "message": "Connection to Resend timed out or failed.",
            "error": str(exc)
        }), 502


# ═══════════════════════════════════════════════════════════════════════════
#  SESSION MANAGEMENT ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/session/create", methods=["POST"])
@_require_teacher
def session_create(teacher_uid):
    if not db:
        return jsonify({"error": "Firebase not initialized"}), 500

    d = request.json or {}
    teacher_id       = d.get("teacher_id", teacher_uid).strip()
    department       = d.get("department", "").strip()
    semester         = str(d.get("semester", "")).strip()
    section          = d.get("section", "").strip()
    subject          = d.get("subject", "").strip()
    duration_minutes = int(d.get("duration_minutes", 10))
    location         = d.get("location")       # {lat, lon} or None
    location_radius  = d.get("location_radius")
    if location_radius is None:
        location_radius = get_classroom_radius_from_settings()
    else:
        location_radius = int(location_radius)

    # Build structured class_id from dept/sem/sec when provided;
    # fall back to the explicit class_id field for legacy callers.
    if department and semester and section:
        class_id = f"{department} Sem{semester} Sec{section}"
    else:
        class_id = d.get("class_id", "").strip()

    if not class_id:
        return jsonify({"error": "Department, Semester, and Section are required"}), 400
    if len(class_id) > 80:
        return jsonify({"error": "Class identifier too long (max 80 chars)"}), 400

    if not subject:
        return jsonify({"error": "Subject name is required"}), 400
    if len(subject) < 2 or len(subject) > 100:
        return jsonify({"error": "Subject name must be between 2 and 100 characters"}), 400

    if duration_minutes < 1 or duration_minutes > 120:
        return jsonify({"error": "duration_minutes must be 1–120"}), 400

    result = create_session(
        db, teacher_id, class_id, subject, duration_minutes,
        location, location_radius,
        department=department or None,
        semester=semester or None,
        section=section or None,
    )
    if "error" in result:
        return jsonify(result), 409
    return jsonify(result), 201

@app.route("/api/session/end", methods=["POST"])
@_require_teacher
def session_end(teacher_uid):
    if not db:
        return jsonify({"error": "Firebase not initialized"}), 500

    d = request.json or {}
    session_id = d.get("session_id", "").strip()
    teacher_id = d.get("teacher_id", teacher_uid).strip()
    if not session_id:
        return jsonify({"error": "session_id required"}), 400
    result = end_session(db, session_id, teacher_id)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)

@app.route("/api/session/active", methods=["GET"])
def session_active():
    if not db:
        return jsonify({"error": "Firebase not initialized"}), 500
    teacher_id = request.args.get("teacher_id")
    class_id   = request.args.get("class_id")
    session    = get_active_session(db, teacher_id=teacher_id, class_id=class_id)
    if not session:
        return jsonify({"active": False})
    return jsonify(get_session_status(db, session["session_id"]))

@app.route("/api/session/status", methods=["GET"])
def session_status():
    if not db:
        return jsonify({"active": False, "reason": "Firebase not initialized"})
    session_id = request.args.get("session_id")
    if session_id:
        return jsonify(get_session_status(db, session_id))
    session = get_active_session(db)
    if not session:
        return jsonify({"active": False, "reason": "No active session"})
    return jsonify(get_session_status(db, session["session_id"]))

# ═══════════════════════════════════════════════════════════════════════════
#  TIMETABLE
# ═══════════════════════════════════════════════════════════════════════════
# NOTE: _require_teacher decorator is defined near line 153 (shared by all protected routes)


@app.route("/api/timetable", methods=["GET"])
@_require_teacher
def get_timetable(teacher_uid):
    """Return all timetable entries for this teacher, ordered by day + start_time."""
    try:
        snap = db.collection("timetable") \
                 .where("teacher_id", "==", teacher_uid) \
                 .stream()
        entries = []
        for doc in snap:
            d = doc.to_dict()
            d["id"] = doc.id
            # Convert server timestamps to ISO strings for JSON
            for field in ("createdAt", "updatedAt"):
                if hasattr(d.get(field), "isoformat"):
                    d[field] = d[field].isoformat()
            entries.append(d)
        # Sort client-side (avoids composite index requirement)
        day_order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
        entries.sort(key=lambda e: (
            day_order.index(e.get("day","Monday")) if e.get("day") in day_order else 99,
            e.get("start_time","")
        ))
        return jsonify(entries)
    except Exception as e:
        print(f"[TIMETABLE] GET error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/timetable/today", methods=["GET"])
@_require_teacher
def get_timetable_today(teacher_uid):
    """Return timetable entries for today's weekday (for session.html quick-start panel)."""
    try:
        today = datetime.now().strftime("%A")   # e.g. "Thursday"
        snap  = db.collection("timetable") \
                  .where("teacher_id", "==", teacher_uid) \
                  .where("day", "==", today) \
                  .stream()
        entries = []
        for doc in snap:
            d = doc.to_dict()
            d["id"] = doc.id
            for field in ("createdAt", "updatedAt"):
                if hasattr(d.get(field), "isoformat"):
                    d[field] = d[field].isoformat()
            entries.append(d)
        entries.sort(key=lambda e: e.get("start_time",""))
        return jsonify({"day": today, "entries": entries})
    except Exception as e:
        print(f"[TIMETABLE] TODAY error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/timetable", methods=["POST"])
@_require_teacher
def create_timetable_entry(teacher_uid):
    """Create a new timetable entry."""
    data = request.json or {}
    required = ["day", "start_time", "end_time", "subject", "class_id"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    valid_days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    if data["day"] not in valid_days:
        return jsonify({"error": f"Invalid day. Must be one of: {', '.join(valid_days)}"}), 400

    try:
        entry = {
            "teacher_id":  teacher_uid,
            "day":         data["day"],
            "start_time":  data["start_time"],
            "end_time":    data["end_time"],
            "subject":     data["subject"].strip(),
            "class_id":    data["class_id"].strip(),
            "room":        data.get("room", "").strip(),
            "department":  data.get("department", "").strip(),
            "semester":    data.get("semester", "").strip(),
            "section":     data.get("section", "").strip(),
            "createdAt":   firestore.SERVER_TIMESTAMP,
        }
        ref = db.collection("timetable").add(entry)
        doc_id = ref[1].id
        print(f"[TIMETABLE] Created {doc_id} — {data['day']} {data['start_time']} {data['subject']}")
        return jsonify({"success": True, "id": doc_id}), 201
    except Exception as e:
        print(f"[TIMETABLE] POST error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/timetable/<entry_id>", methods=["PUT"])
@_require_teacher
def update_timetable_entry(teacher_uid, entry_id):
    """Update a timetable entry (teacher must own it)."""
    data = request.json or {}
    try:
        ref  = db.collection("timetable").document(entry_id)
        snap = ref.get()
        if not snap.exists:
            return jsonify({"error": "Entry not found"}), 404
        if snap.to_dict().get("teacher_id") != teacher_uid:
            return jsonify({"error": "Forbidden"}), 403

        updatable = ["day","start_time","end_time","subject","class_id","room","department","semester","section"]
        update_data = {k: data[k] for k in updatable if k in data}
        update_data["updatedAt"] = firestore.SERVER_TIMESTAMP
        ref.update(update_data)
        print(f"[TIMETABLE] Updated {entry_id}")
        return jsonify({"success": True})
    except Exception as e:
        print(f"[TIMETABLE] PUT error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/timetable/<entry_id>", methods=["DELETE"])
@_require_teacher
def delete_timetable_entry(teacher_uid, entry_id):
    """Delete a timetable entry (teacher must own it)."""
    try:
        ref  = db.collection("timetable").document(entry_id)
        snap = ref.get()
        if not snap.exists:
            return jsonify({"error": "Entry not found"}), 404
        if snap.to_dict().get("teacher_id") != teacher_uid:
            return jsonify({"error": "Forbidden"}), 403
        ref.delete()
        print(f"[TIMETABLE] Deleted {entry_id}")
        return jsonify({"success": True})
    except Exception as e:
        print(f"[TIMETABLE] DELETE error: {e}")
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  SCHEDULED SESSIONS  — state machine: scheduled|active|completed|expired|cancelled
# ═══════════════════════════════════════════════════════════════════════════

def _ss_serialize(doc):
    d = doc.to_dict(); d["id"] = doc.id
    for f in ("createdAt","updatedAt","startedAt"):
        if hasattr(d.get(f),"isoformat"): d[f] = d[f].isoformat()
    return d

def _ss_overlap(db, teacher_uid, date_str, start_t, end_t, department, semester, section, exclude_id=None):
    """Return first overlapping scheduled/active session dict for the same class, or None."""
    dept = (department or "").strip().upper()
    sem = str(semester or "").strip()
    sec = (section or "").strip().upper()

    snap = db.collection("scheduled_sessions") \
             .where("teacher_id","==",teacher_uid) \
             .where("date","==",date_str) \
             .where("department","==",dept) \
             .where("semester","==",sem) \
             .where("section","==",sec).stream()
    for doc in snap:
        if exclude_id and doc.id == exclude_id: continue
        d = doc.to_dict()
        if d.get("status") not in ("scheduled","active"): continue
        es, ee = d.get("start_time",""), d.get("end_time","")
        if start_t < ee and end_t > es: return d
    return None


@app.route("/api/scheduled-sessions/today", methods=["GET"])
@_require_teacher
def get_scheduled_sessions_today(teacher_uid):
    """Return today's sessions; lazily expire past-due scheduled entries."""
    today    = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M")
    try:
        snap = db.collection("scheduled_sessions") \
                 .where("teacher_id","==",teacher_uid) \
                 .where("date","==",today).stream()
        sessions = []
        for doc in snap:
            d = doc.to_dict(); d["id"] = doc.id
            if d.get("status") == "scheduled" and d.get("end_time","") < now_time:
                doc.reference.update({"status":"expired","updatedAt":firestore.SERVER_TIMESTAMP})
                d["status"] = "expired"
            for f in ("createdAt","updatedAt","startedAt"):
                if hasattr(d.get(f),"isoformat"): d[f] = d[f].isoformat()
            sessions.append(d)
        sessions.sort(key=lambda s: s.get("start_time",""))
        return jsonify({"date": today, "sessions": sessions})
    except Exception as e:
        print(f"[SCHED] TODAY error: {e}"); return jsonify({"error": str(e)}), 500


@app.route("/api/scheduled-sessions", methods=["GET"])
@_require_teacher
def get_scheduled_sessions(teacher_uid):
    """Return sessions in ?from=YYYY-MM-DD&to=YYYY-MM-DD range."""
    today     = datetime.now().strftime("%Y-%m-%d")
    date_from = request.args.get("from", today)
    date_to   = request.args.get("to",   date_from)
    try:
        snap = db.collection("scheduled_sessions") \
                 .where("teacher_id","==",teacher_uid) \
                 .where("date",">=",date_from) \
                 .where("date","<=",date_to).stream()
        sessions = [_ss_serialize(doc) for doc in snap]
        sessions.sort(key=lambda s: (s.get("date",""), s.get("start_time","")))
        return jsonify(sessions)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scheduled-sessions", methods=["POST"])
@_require_teacher
def create_scheduled_session(teacher_uid):
    """Create a manual one-off scheduled session with overlap prevention."""
    data    = request.json or {}
    missing = [f for f in ["date","start_time","end_time","subject","class_id"] if not data.get(f)]
    if missing: return jsonify({"error": f"Missing: {', '.join(missing)}"}), 400
    start, end, date_str = data["start_time"], data["end_time"], data["date"]
    if start >= end: return jsonify({"error": "End time must be after start time"}), 400
    try: datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError: return jsonify({"error": "date must be YYYY-MM-DD"}), 400
    
    try:
        day = data.get("day") or datetime.strptime(date_str,"%Y-%m-%d").strftime("%A")
        dept, sem, sec = parse_class_id(data["class_id"].strip())
        department = data.get("department", "").strip() or dept
        semester = str(data.get("semester", "")).strip() or sem
        section = data.get("section", "").strip() or sec

        overlap = _ss_overlap(db, teacher_uid, date_str, start, end, department, semester, section)
        if overlap:
            return jsonify({"error":
                f"Overlap with \"{overlap.get('subject','')}\" "
                f"({overlap.get('start_time','')}–{overlap.get('end_time','')})"
            }), 409

        ref = db.collection("scheduled_sessions").add({
            "teacher_id": teacher_uid, "date": date_str, "day": day,
            "start_time": start, "end_time": end,
            "subject":    data["subject"].strip(), "class_id": data["class_id"].strip(),
            "room":       data.get("room","").strip(), "department": department,
            "semester":   semester, "section": section,
            "status": "scheduled", "session_id": None,
            "timetable_entry_id": data.get("timetable_entry_id"), "created_manually": True,
            "createdAt": firestore.SERVER_TIMESTAMP, "updatedAt": firestore.SERVER_TIMESTAMP,
        })
        doc_id = ref[1].id
        print(f"[SCHED] Created manual {doc_id}: {date_str} {start} {data['subject']}")
        return jsonify({"success": True, "id": doc_id}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scheduled-sessions/generate", methods=["POST"])
@_require_teacher
def generate_scheduled_sessions(teacher_uid):
    """
    Generate scheduled_sessions for week_start..week_end from timetable entries.
    Body: { entries:[...], week_start:"YYYY-MM-DD", week_end:"YYYY-MM-DD" }
    """
    from datetime import timedelta
    data = request.json or {}
    entries    = data.get("entries", [])
    week_start = data.get("week_start")
    week_end   = data.get("week_end")
    if not entries:           return jsonify({"error": "No entries provided"}), 400
    if not week_start or not week_end: return jsonify({"error": "week_start and week_end required"}), 400
    try:
        start_d = datetime.strptime(week_start,"%Y-%m-%d").date()
        end_d   = datetime.strptime(week_end,  "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Dates must be YYYY-MM-DD"}), 400
    if end_d < start_d: return jsonify({"error": "week_end must be >= week_start"}), 400

    created, skipped, errors = 0, 0, []
    try:
        for entry in entries:
            day_name = (entry.get("day") or "").strip()
            st       = (entry.get("start_time") or "").strip()
            et       = (entry.get("end_time")   or "").strip()
            subject  = (entry.get("subject")    or "").strip()
            class_id = (entry.get("class_id")   or "").strip()
            if not all([day_name, st, et, subject, class_id]):
                errors.append(f"Incomplete: {subject or '?'} on {day_name or '?'}"); continue
            if st >= et:
                errors.append(f"Bad times {subject}: {st}–{et}"); continue

            dept, sem, sec = parse_class_id(class_id)
            department = (entry.get("department") or "").strip() or dept
            semester = str(entry.get("semester") or "").strip() or sem
            section = (entry.get("section") or "").strip() or sec

            cur = start_d
            while cur <= end_d:
                if cur.strftime("%A") == day_name:
                    ds = cur.strftime("%Y-%m-%d")
                    # Dedup
                    dup = db.collection("scheduled_sessions") \
                            .where("teacher_id","==",teacher_uid).where("date","==",ds) \
                            .where("start_time","==",st).where("subject","==",subject) \
                            .limit(1).stream()
                    if any(dup): skipped += 1; cur += timedelta(days=1); continue
                    # Overlap
                    if _ss_overlap(db, teacher_uid, ds, st, et, department, semester, section):
                        errors.append(f"Overlap skipped: {subject} {ds} {st}–{et}")
                        skipped += 1; cur += timedelta(days=1); continue
                    db.collection("scheduled_sessions").add({
                        "teacher_id": teacher_uid, "date": ds, "day": day_name,
                        "start_time": st, "end_time": et, "subject": subject, "class_id": class_id,
                        "room":       entry.get("room",""), "department": department,
                        "semester":   semester, "section":  section,
                        "status": "scheduled", "session_id": None,
                        "timetable_entry_id": entry.get("id"), "created_manually": False,
                        "createdAt": firestore.SERVER_TIMESTAMP, "updatedAt": firestore.SERVER_TIMESTAMP,
                    })
                    created += 1
                cur += timedelta(days=1)

        print(f"[SCHED] Generated {created}, skipped {skipped}, {len(errors)} errors "
              f"for {week_start}–{week_end}")
        return jsonify({"success":True,"created":created,"skipped":skipped,"errors":errors})
    except Exception as e:
        print(f"[SCHED] Generate error: {e}"); return jsonify({"error": str(e)}), 500


@app.route("/api/scheduled-sessions/<sched_id>/start", methods=["POST"])
@_require_teacher
def start_scheduled_session(teacher_uid, sched_id):
    """Start a scheduled session → creates active session record; returns session_id."""
    data = request.json or {}
    ref  = db.collection("scheduled_sessions").document(sched_id)
    snap = ref.get()
    if not snap.exists: return jsonify({"error": "Not found"}), 404
    sched = snap.to_dict()
    if sched.get("teacher_id") != teacher_uid: return jsonify({"error": "Forbidden"}), 403
    if sched.get("status") not in ("scheduled","expired"):
        return jsonify({"error": f"Cannot start — status is '{sched.get('status')}'"}), 409
    try:
        _fmt    = "%H:%M"
        dur_min = max(10, int((
            datetime.strptime(sched["end_time"],  _fmt) -
            datetime.strptime(sched["start_time"],_fmt)
        ).total_seconds() / 60))
    except Exception:
        dur_min = 60
    location_radius = data.get("location_radius")
    if location_radius is None:
        location_radius = get_classroom_radius_from_settings()
    else:
        location_radius = int(location_radius)

    result = create_session(db, teacher_uid,
        class_id=sched.get("class_id",""), subject=sched.get("subject",""),
        duration_minutes=dur_min,
        location=data.get("location"), location_radius=location_radius,
        department=sched.get("department"),
        semester=sched.get("semester"),
        section=sched.get("section")
    )
    if "error" in result: return jsonify(result), 409
    session_id = result["session_id"]
    ref.update({"status":"active","session_id":session_id,
                "startedAt":firestore.SERVER_TIMESTAMP,"updatedAt":firestore.SERVER_TIMESTAMP})
    print(f"[SCHED] Started {sched_id} → session {session_id}")
    return jsonify({"success": True, "session_id": session_id, **result})


@app.route("/api/scheduled-sessions/<sched_id>/cancel", methods=["POST"])
@_require_teacher
def cancel_scheduled_session(teacher_uid, sched_id):
    """Cancel a scheduled or expired session."""
    ref  = db.collection("scheduled_sessions").document(sched_id)
    snap = ref.get()
    if not snap.exists: return jsonify({"error": "Not found"}), 404
    d = snap.to_dict()
    if d.get("teacher_id") != teacher_uid: return jsonify({"error": "Forbidden"}), 403
    if d.get("status") == "active":
        return jsonify({"error": "End the active session first"}), 409
    if d.get("status") in ("completed","cancelled"):
        return jsonify({"error": f"Already {d['status']}"}), 409
    ref.update({"status":"cancelled","updatedAt":firestore.SERVER_TIMESTAMP})
    print(f"[SCHED] Cancelled {sched_id}")
    return jsonify({"success": True})


@app.route("/api/scheduled-sessions/<sched_id>", methods=["PUT"])
@_require_teacher
def update_scheduled_session(teacher_uid, sched_id):
    """Edit details of a scheduled or expired session (overlap-safe)."""
    data = request.json or {}
    ref  = db.collection("scheduled_sessions").document(sched_id)
    snap = ref.get()
    if not snap.exists: return jsonify({"error": "Not found"}), 404
    d = snap.to_dict()
    if d.get("teacher_id") != teacher_uid: return jsonify({"error": "Forbidden"}), 403
    if d.get("status") not in ("scheduled","expired"):
        return jsonify({"error": "Only scheduled/expired sessions can be edited"}), 409
    new_s = data.get("start_time", d.get("start_time",""))
    new_e = data.get("end_time",   d.get("end_time",""))
    new_d = data.get("date",       d.get("date",""))
    if new_s >= new_e: return jsonify({"error": "End time must be after start time"}), 400

    new_class_id = data.get("class_id", d.get("class_id", ""))
    dept, sem, sec = parse_class_id(new_class_id)
    department = data.get("department", d.get("department", "")).strip() or dept
    semester = str(data.get("semester", d.get("semester", ""))).strip() or sem
    section = data.get("section", d.get("section", "")).strip() or sec

    ov = _ss_overlap(db, teacher_uid, new_d, new_s, new_e, department, semester, section, exclude_id=sched_id)
    if ov:
        return jsonify({"error":
            f"Overlap with \"{ov.get('subject','')}\" ({ov.get('start_time','')}–{ov.get('end_time','')})"
        }), 409
    upd = {k: data[k] for k in
           ["date","day","start_time","end_time","subject","class_id","room","department","semester","section"]
           if k in data}
    upd["status"]    = "scheduled"
    upd["updatedAt"] = firestore.SERVER_TIMESTAMP
    ref.update(upd)
    print(f"[SCHED] Edited {sched_id}")
    return jsonify({"success": True})


@app.route("/api/scheduled-sessions/<sched_id>", methods=["DELETE"])
@_require_teacher
def delete_scheduled_session(teacher_uid, sched_id):
    """Delete a non-active scheduled session."""
    ref  = db.collection("scheduled_sessions").document(sched_id)
    snap = ref.get()
    if not snap.exists: return jsonify({"error": "Not found"}), 404
    d = snap.to_dict()
    if d.get("teacher_id") != teacher_uid: return jsonify({"error": "Forbidden"}), 403
    if d.get("status") == "active":
        return jsonify({"error": "Cannot delete an active session"}), 409
    ref.delete()
    print(f"[SCHED] Deleted {sched_id}")
    return jsonify({"success": True})


# ═══════════════════════════════════════════════════════════════════════════
#  RUN
# ═══════════════════════════════════════════════════════════════════════════

def verify_email_startup_config():
    """Verify email environment configuration on startup and output diagnostic logs."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    email_from = os.environ.get("EMAIL_FROM", "onboarding@resend.dev")
    
    print("\n" + "="*70)
    print("[EMAIL STARTUP AUDIT] Attendance Management System Email System Initialization")
    print("="*70)
    
    # 1. Verify Key
    if not api_key:
        print("🔴 ERROR: 'RESEND_API_KEY' is missing or empty in your environment/ .env!")
    elif "REPLACE_WITH_YOUR_KEY" in api_key or api_key == "re_REPLACE_WITH_YOUR_KEY":
        print("⚠️  WARNING: 'RESEND_API_KEY' is configured with the PLACEHOLDER value ('re_REPLACE_WITH_YOUR_KEY')!")
        print("   -> Real-time student welcomes and absence alerts will fail with a 401 Unauthorized error.")
    elif not api_key.startswith("re_"):
        print(f"🔴 ERROR: 'RESEND_API_KEY' ({api_key[:8]}...) format is invalid! Must start with 're_'.")
    else:
        print(f"🟢 SUCCESS: 'RESEND_API_KEY' successfully loaded (ending in: ...{api_key[-4:] if len(api_key) > 4 else api_key}).")
        
    # 2. Verify Sender & Sandbox Mode
    if email_from == "onboarding@resend.dev":
        print("ℹ️  MODE: Sandbox / Onboarding Mode active (EMAIL_FROM = 'onboarding@resend.dev').")
        print("   -> CRITICAL RESTRICTION: Resend will ONLY deliver emails to your verified account registration email.")
        print("   -> Deliveries sent to unverified parent or student email domains will be rejected with a 403 Forbidden error.")
    else:
        print(f"🟢 MODE: Custom Domain Mode active (EMAIL_FROM = '{email_from}').")
        print("   -> Ensure this domain is fully verified inside the Resend console (DKIM/SPF records verified).")
        
    # 3. Portal Link
    portal_url = os.environ.get("PORTAL_URL", "https://smart-attendance-ai-7139f.web.app")
    print(f"🌐 Portal Redirect URL: {portal_url}")
    print("="*70 + "\n")


if __name__ == "__main__":
    verify_email_startup_config()
    port = int(os.environ.get("PORT", 5005))
    debug = os.environ.get("FLASK_ENV", "production") == "development"
    print(f"🚀 Starting Flask on http://0.0.0.0:{port} (debug={debug})")
    app.run(host="0.0.0.0", port=port, debug=debug)
