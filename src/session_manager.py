"""
session_manager.py — Attendance Session Management

Handles:
  - Session creation with configurable duration + GPS
  - Firestore-backed session reads (no in-memory state)
  - Token issuance (HMAC-SHA256) after liveness verification
  - Token validation before any Firestore attendance write
  - Atomic duplicate-prevention using Firestore transactions

Session Firestore structure:
  sessions/{session_id}
    teacher_id: str
    class_id: str
    subject: str
    created_at: timestamp
    expires_at: timestamp
    is_active: bool
    duration_minutes: int
    location: { lat, lon }
    location_radius: int
    attendance_count: int
"""

import os
import hmac
import hashlib
import time
import uuid
import threading
from datetime import datetime, timezone, timedelta
from firebase_admin import firestore

try:
    from email_service import send_absence_notification, log_email
    _EMAIL_AVAILABLE = True
except ImportError:
    _EMAIL_AVAILABLE = False
    def send_absence_notification(*a, **kw): return False, "email_service not found"
    def log_email(*a, **kw): pass

# ── HMAC secret (set via environment variable in production) ─────────────────
_SECRET = os.environ.get("LIVENESS_SECRET", "smartattend-change-in-production-2026")
TOKEN_TTL_SECONDS = 180  # 3 minutes — enough time for liveness + face + location flow


# ═══════════════════════════════════════════════════════════════════════════════
#  TOKEN UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def generate_liveness_token(uid: str, session_id: str) -> str:
    """
    Issue a short-lived HMAC-SHA256 token after server-side liveness passes.
    Format: {timestamp}:{session_id}:{hmac}
    """
    ts = int(time.time())
    payload = f"{uid}:{session_id}:{ts}"
    sig = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{ts}:{session_id}:{sig}"


def validate_liveness_token(token: str, uid: str) -> tuple[bool, str]:
    """
    Validate a liveness token.
    Returns (valid: bool, message: str)
    """
    if not token:
        return False, "No liveness token provided"
    try:
        parts = token.split(":", 2)
        if len(parts) != 3:
            return False, "Malformed token"
        ts_str, session_id, sig = parts
        ts = int(ts_str)
        if time.time() - ts > TOKEN_TTL_SECONDS:
            return False, f"Token expired ({TOKEN_TTL_SECONDS}s TTL)"
        payload = f"{uid}:{session_id}:{ts}"
        expected = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False, "Token signature invalid"
        return True, session_id  # Return session_id so caller can use it
    except Exception as e:
        return False, f"Token validation error: {e}"


def generate_challenge_token(uid: str, session_id: str, challenges: list[str], device_fingerprint: str) -> str:
    """
    Generate a signed, short-lived challenge token.
    Format: {timestamp}:{challenge_sequence}:{sig}
    """
    ts = int(time.time())
    challenge_seq_str = ",".join(challenges)
    payload = f"{uid}:{session_id}:{ts}:{challenge_seq_str}:{device_fingerprint}"
    sig = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{ts}:{challenge_seq_str}:{sig}"


def verify_challenge_token(token: str, uid: str, session_id: str, device_fingerprint: str) -> tuple[bool, str, list[str]]:
    """
    Verify the signed challenge token.
    Returns (valid: bool, reason: str, challenges: list[str])
    """
    if not token:
        return False, "No challenge token provided", []
    try:
        parts = token.split(":", 2)
        if len(parts) != 3:
            return False, "Malformed challenge token", []
        ts_str, challenge_seq_str, sig = parts
        ts = int(ts_str)
        # 40 seconds limit for challenge verification
        if time.time() - ts > 40:
            return False, "Challenge token expired (40s limit)", []
        
        payload = f"{uid}:{session_id}:{ts}:{challenge_seq_str}:{device_fingerprint}"
        expected = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False, "Challenge token signature invalid", []
            
        challenges = challenge_seq_str.split(",")
        return True, "", challenges
    except Exception as e:
        return False, f"Challenge token validation error: {e}", []



# ═══════════════════════════════════════════════════════════════════════════════
#  SESSION CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_class_id(class_id: str) -> tuple[str, str, str]:
    """
    Parse class_id (e.g. 'ISE 4A', 'CS-A Sem 5', 'CSE 6B') into (department, semester, section).
    """
    if not class_id:
        return "", "", ""
    import re
    val = class_id.strip()
    dept = ""
    sem = ""
    sec = ""

    # Match department
    match_dept = re.match(r"^([a-zA-Z]+)", val)
    if match_dept:
        dept = match_dept.group(1).upper()
    
    # Match semester digit
    match_sem = re.search(r"(\d+)", val)
    if match_sem:
        sem = match_sem.group(1)
        
    # Match section
    match_sec = re.search(r"(?:Sem\s*\d+\s+([a-zA-Z]))|(?:Sem\s*([a-zA-Z])\s*\d+)|(?:[a-zA-Z]+-\s*([a-zA-Z]))|(?:\d+([a-zA-Z]))", val)
    if match_sec:
        sec = next((g for g in match_sec.groups() if g), "").upper()
    else:
        match_sec_fb = re.search(r"\s+([a-zA-Z])\b", val)
        if match_sec_fb:
            sec = match_sec_fb.group(1).upper()
            
    return dept, sem, sec


def create_session(db, teacher_id: str, class_id: str, subject: str,
                   duration_minutes: int, location: dict, location_radius: int,
                   department: str = None, semester: str = None, section: str = None) -> dict:
    """
    Create a new attendance session. Enforces one active session per teacher per class/section.
    Returns the created session dict with session_id.
    """
    # Parse class details from class_id if not explicitly provided
    dept = (department or "").strip().upper()
    sem = str(semester or "").strip()
    sec = (section or "").strip().upper()

    if not (dept and sem and sec) and class_id:
        p_dept, p_sem, p_sec = _parse_class_id(class_id)
        if not dept: dept = p_dept
        if not sem: sem = p_sem
        if not sec: sec = p_sec

    # Check for existing active session from this teacher for this specific class
    existing = (
        db.collection("sessions")
        .where("teacher_id", "==", teacher_id)
        .where("is_active", "==", True)
        .where("department", "==", dept)
        .where("semester", "==", sem)
        .where("section", "==", sec)
        .limit(1)
        .stream()
    )
    for doc in existing:
        return {"error": f"A session for {dept} Sem {sem} Sec {sec} is already active. End it before starting a new one.", "existing_id": doc.id}

    now_utc = datetime.now(timezone.utc)
    expires_utc = now_utc + timedelta(minutes=duration_minutes)
    session_id = str(uuid.uuid4())

    session_data = {
        "teacher_id": teacher_id,
        "class_id": class_id,
        "subject": subject,
        "created_at": firestore.SERVER_TIMESTAMP,
        "expires_at": expires_utc,          # Stored as datetime so we can compare server-side
        "is_active": True,
        "duration_minutes": duration_minutes,
        "location": location,
        "location_radius": location_radius,
        "attendance_count": 0,
        "department": dept,
        "semester": sem,
        "section": sec,
    }

    db.collection("sessions").document(session_id).set(session_data)
    return {"session_id": session_id, **session_data, "created_at": now_utc.isoformat(), "expires_at": expires_utc.isoformat()}

def send_absent_emails_async(db, session_id: str):
    """
    Run send_absent_emails in a background thread to prevent blocking the Flask request thread.
    """
    thread = threading.Thread(target=send_absent_emails, args=(db, session_id))
    thread.daemon = True
    thread.start()


def send_absent_emails(db, session_id: str):
    """
    Find absent students for session_id and send real notification emails to parents.
    Logs every delivery attempt to Firestore `email_logs`.
    Deduplicates: will not re-send for the same session+student.
    """
    try:
        session_ref  = db.collection("sessions").document(session_id)
        session_doc  = session_ref.get()
        if not session_doc.exists:
            return 0
        session_data = session_doc.to_dict()
        class_id     = session_data.get("class_id", "General")
        subject      = session_data.get("subject", "Class")
        date_str     = datetime.now().strftime("%Y-%m-%d")

        # 1. Get all registered students with face enrolled
        all_students = list(db.collection("students").stream())

        # 2. Get present student UIDs for this session
        present_docs = db.collection("attendance").where("session_id", "==", session_id).stream()
        present_uids = set()
        for doc in present_docs:
            d = doc.to_dict()
            if d.get("status") in ["Present", "Late"]:
                present_uids.add(d.get("student_uid"))

        # 2.5. Resolve session class for per-student filtering
        session_dept = (session_data.get("department") or "").strip().lower()
        session_sem  = str(session_data.get("semester") or "").strip()
        session_sec  = (session_data.get("section") or "").strip().lower()
        has_session_class = bool(session_dept and session_sem and session_sec)

        # 3. Compute absent list
        emails_sent = 0
        for stud in all_students:
            sdata       = stud.to_dict()
            student_uid = sdata.get("uid")
            if not student_uid:
                continue
            if student_uid in present_uids:
                continue

            # Class isolation: only notify students in the session's class.
            # If session has no structured fields (legacy), include everyone.
            if has_session_class:
                s_dept = (sdata.get("department") or sdata.get("dept") or "").strip().lower()
                s_sem  = str(sdata.get("semester") or "").strip()
                s_sec  = (sdata.get("section") or "").strip().lower()
                # Legacy student (no class data) → include (soft migration)
                if s_dept or s_sem or s_sec:
                    if s_dept != session_dept or s_sem != session_sem or s_sec != session_sec:
                        continue  # different class — skip


            parent_email = (sdata.get("parent_email") or sdata.get("parentEmail") or "").strip()
            if not parent_email or "@" not in parent_email:
                print(f"[EMAIL] No valid parent email for {sdata.get('name','?')} — skipping")
                continue

            student_name = sdata.get("name", "Student")

            # 4a. Dedup check — skip if already sent for this session+student
            existing_log = db.collection("email_logs") \
                             .where("session_id", "==", session_id) \
                             .where("student_uid", "==", student_uid) \
                             .where("status", "==", "sent") \
                             .limit(1).stream()
            if any(existing_log):
                print(f"[EMAIL] Skipped duplicate — {student_name} / {session_id}")
                continue

            # 4b. Send real email
            print(f"[EMAIL] Sending absence notification → {parent_email} for {student_name}")
            success, ref = send_absence_notification(
                parent_email, student_name, subject, class_id, date_str
            )

            # 4c. Log delivery result
            log_email(db, "absence_notification", parent_email, success, ref, extra={
                "student_uid":  student_uid,
                "student_name": student_name,
                "session_id":   session_id,
                "class_id":     class_id,
                "subject":      subject,
                "date":         date_str,
            })

            # 4d. Write Absent attendance record (idempotent)
            doc_id  = f"{session_id}_{student_uid}"
            att_ref = db.collection("attendance").document(doc_id)
            if not att_ref.get().exists:
                att_ref.set({
                    "name":       student_name,
                    "student_uid":student_uid,
                    "studentId":  student_uid,
                    "date":       date_str,
                    "time":       "--",
                    "status":     "Absent",
                    "method":     "Auto-System (Absent)",
                    "session_id": session_id,
                    "timestamp":  firestore.SERVER_TIMESTAMP,
                })

            if success:
                emails_sent += 1

        print(f"[SESSION] Absent email run complete — {emails_sent} sent for session {session_id}")
        return emails_sent

    except Exception as e:
        print(f"[SESSION] Error in send_absent_emails for {session_id}: {e}")
        return 0



def end_session(db, session_id: str, teacher_id: str) -> dict:
    """
    Explicitly terminate a session. Only the owning teacher can end it.
    """
    ref = db.collection("sessions").document(session_id)
    doc = ref.get()
    if not doc.exists:
        return {"error": "Session not found"}
    data = doc.to_dict()
    if data.get("teacher_id") != teacher_id:
        return {"error": "Unauthorized — not your session"}
    if not data.get("is_active"):
        return {"error": "Session already ended"}
    ref.update({"is_active": False, "ended_at": firestore.SERVER_TIMESTAMP})
    
    # Send emails to parents of absent students (async, non-blocking)
    send_absent_emails_async(db, session_id)
    
    return {"success": True, "session_id": session_id}


def get_active_session(db, teacher_id: str = None, class_id: str = None) -> dict | None:
    """
    Fetch the currently active session for a teacher or class.
    Also lazily expires sessions that have passed their expires_at.
    Returns session dict or None.
    """
    query = db.collection("sessions").where("is_active", "==", True)
    if teacher_id:
        query = query.where("teacher_id", "==", teacher_id)
    if class_id:
        query = query.where("class_id", "==", class_id)

    docs = list(query.limit(1).stream())
    if not docs:
        return None

    doc = docs[0]
    data = doc.to_dict()
    data["session_id"] = doc.id

    # Lazy expiry: if expires_at is in the past, deactivate and return None
    expires_at = data.get("expires_at")
    if expires_at:
        # Firestore returns datetime objects for timestamp fields
        if isinstance(expires_at, datetime):
            exp = expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp:
                doc.reference.update({"is_active": False})
                send_absent_emails_async(db, doc.id)
                return None

    return data


def validate_session(db, session_id: str) -> tuple[bool, str, dict | None]:
    """
    Validate a session is active and not expired.
    Returns (valid, reason, session_data)
    """
    ref = db.collection("sessions").document(session_id)
    doc = ref.get()
    if not doc.exists:
        return False, "Session not found", None

    data = doc.to_dict()
    data["session_id"] = session_id

    if not data.get("is_active"):
        return False, "Session has been ended by teacher", None

    expires_at = data.get("expires_at")
    if expires_at:
        if isinstance(expires_at, datetime):
            exp = expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp:
                ref.update({"is_active": False})
                send_absent_emails_async(db, session_id)
                return False, "Session has expired", None

    return True, "Active", data


def get_classroom_radius_from_settings(db) -> int:
    """Read the dynamic classroom geofence radius from settings/classroom in Firestore."""
    try:
        doc = db.collection("settings").document("classroom").get()
        if doc.exists:
            d = doc.to_dict()
            if "locationRadius" in d:
                return int(d["locationRadius"])
    except Exception as e:
        print(f"Error fetching classroom radius: {e}")
    return 10


def get_session_status(db, session_id: str) -> dict:
    """
    Return session status with remaining seconds. Used by student polling.
    """
    valid, reason, data = validate_session(db, session_id)
    if not valid:
        return {"active": False, "reason": reason}

    expires_at = data.get("expires_at")
    remaining = 0
    if isinstance(expires_at, datetime):
        exp = expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        remaining = max(0, int((exp - datetime.now(timezone.utc)).total_seconds()))

    location_radius = data.get("location_radius")
    if location_radius is None:
        location_radius = get_classroom_radius_from_settings(db)
    else:
        location_radius = int(location_radius)

    return {
        "active": True,
        "session_id": session_id,
        "class_id": data.get("class_id"),
        "subject": data.get("subject"),
        "teacher_id": data.get("teacher_id"),
        "duration_minutes": data.get("duration_minutes"),
        "remaining_seconds": remaining,
        "attendance_count": data.get("attendance_count", 0),
        "location": data.get("location"),
        "location_radius": location_radius,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  ATTENDANCE WRITE (transactional)
# ═══════════════════════════════════════════════════════════════════════════════

def write_attendance_transactional(db, doc_id: str, attendance_data: dict, session_id: str) -> tuple[bool, str]:
    """
    Write attendance record using a Firestore transaction to prevent race conditions.
    Also increments session.attendance_count atomically.
    Returns (success: bool, message: str)
    """
    att_ref = db.collection("attendance").document(doc_id)
    session_ref = db.collection("sessions").document(session_id)

    @firestore.transactional
    def _transact(transaction):
        att_snap = att_ref.get(transaction=transaction)
        if att_snap.exists:
            existing = att_snap.to_dict()
            if existing.get("status") == "Present":
                return False, f"Already marked at {existing.get('time', 'earlier')}"
        transaction.set(att_ref, attendance_data)
        transaction.update(session_ref, {"attendance_count": firestore.Increment(1)})
        return True, "Marked"

    transaction = db.transaction()
    try:
        return _transact(transaction)
    except Exception as e:
        return False, f"Transaction failed: {e}"
