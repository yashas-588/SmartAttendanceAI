import os
import sys
import cv2
import numpy as np
import base64
import time
import csv
import io
from datetime import datetime
from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
from deepface import DeepFace
import firebase_admin
from firebase_admin import credentials, firestore

# ── Path setup ────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANTI_SPOOF_DIR = os.path.join(BASE_DIR, "anti-spoofing")
if ANTI_SPOOF_DIR not in sys.path:
    sys.path.insert(0, ANTI_SPOOF_DIR)

from api.api_integration import antispoof_bp

# ── Flask app ─────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app, origins="*")   # Allow all origins (localhost, Firebase hosting, ngrok)

# Register anti-spoofing blueprint
app.register_blueprint(antispoof_bp)

# ── Firebase ──────────────────────────────────────────────────────────────
if not firebase_admin._apps:
    cred_path = os.path.join(BASE_DIR, "firebase_key.json")
    if os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        print("✅ Firebase initialized")
    else:
        print("⚠️  Warning: firebase_key.json not found. Firebase features will be disabled.")

db = firestore.client() if firebase_admin._apps else None
DATASET_PATH = os.path.join(BASE_DIR, "dataset")

last_marked = {}   # cooldown tracker: {name: timestamp}


# ═══════════════════════════════════════════════════════════════════════════
#  UTILITY
# ═══════════════════════════════════════════════════════════════════════════

def decode_base64_image(b64_string):
    """Decode a base64 (data-URL or raw) string into a cv2 BGR image."""
    if not b64_string or len(b64_string) < 20:
        return None
    if "," in b64_string:
        b64_string = b64_string.split(",", 1)[1]
    if not b64_string:
        return None
    try:
        img_data = base64.b64decode(b64_string)
        np_arr   = np.frombuffer(img_data, np.uint8)
        if np_arr.size == 0:
            return None
        return cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    except Exception as e:
        print(f"decode_base64_image error: {e}")
        return None


def get_student_attendance_stats(name):
    """
    Fetch from Firestore the attendance count and last-seen date for a student.
    Returns (count, last_seen_str).
    """
    if not db:
        return 0, "N/A"
    try:
        docs = (
            db.collection("attendance")
            .where("name", "==", name)
            .stream()
        )
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
        print(f"Error fetching stats for {name}: {e}")
        return 0, "N/A"


# ═══════════════════════════════════════════════════════════════════════════
#  MARK ATTENDANCE (AI Vision)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/mark-attendance", methods=["POST"])
def mark_attendance_api():
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized. Check firebase_key.json"}), 500

    data = request.json
    if not data or "image" not in data:
        return jsonify({"success": False, "message": "No image provided in request"}), 400

    lat = data.get("lat")
    lon = data.get("lon")

    try:
        frame = decode_base64_image(data["image"])
        if frame is None:
            return jsonify({"success": False, "message": "Invalid or empty image frame."}), 400

        # DeepFace face recognition
        results = DeepFace.find(
            img_path=frame,
            db_path=DATASET_PATH,
            enforce_detection=False,
            model_name="Facenet",
            distance_metric="cosine",
            silent=True
        )

        marked_names = []
        messages     = []

        df_list = results if isinstance(results, list) else [results]

        for df in df_list:
            if len(df) == 0:
                continue

            identity_path = df.iloc[0]["identity"]
            name = os.path.basename(os.path.dirname(identity_path))
            current_time = time.time()

            if name not in last_marked or current_time - last_marked[name] > 300:
                last_marked[name] = current_time
                now      = datetime.now()
                date_str = now.strftime("%Y-%m-%d")
                time_str = now.strftime("%H:%M:%S")

                doc_ref = db.collection("attendance").document(f"{date_str}_{name}")
                doc = doc_ref.get()

                if not doc.exists:
                    doc_data = {
                        "name":      name,
                        "date":      date_str,
                        "time":      time_str,
                        "status":    "Present",
                        "method":    "AI Vision",
                        "timestamp": firestore.SERVER_TIMESTAMP,
                    }
                    if lat is not None and lon is not None:
                        doc_data["location"] = {"lat": lat, "lon": lon}
                    doc_ref.set(doc_data)
                    marked_names.append(name)
                    messages.append(f"{name} — Marked Present ✓")
                else:
                    marked_names.append(name)
                    messages.append(f"{name} — Already marked today")
            else:
                remaining = int(300 - (current_time - last_marked[name]))
                marked_names.append(name)
                messages.append(f"{name} — Cooldown ({remaining}s left)")

        if marked_names:
            return jsonify({
                "success": True,
                "name":    ", ".join(marked_names),
                "message": " | ".join(messages)
            })
        else:
            return jsonify({"success": False, "message": "Face not recognized in database."}), 401

    except Exception as e:
        print(f"Error in mark-attendance: {e}")
        return jsonify({"success": False, "message": f"System Error: {str(e)}"}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  STUDENTS
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/students", methods=["GET"])
def get_students():
    """Return all registered students with real attendance stats from Firestore."""
    if not os.path.exists(DATASET_PATH):
        return jsonify([])

    students = []
    for name in sorted(os.listdir(DATASET_PATH)):
        folder = os.path.join(DATASET_PATH, name)
        if not os.path.isdir(folder) or name.startswith("."):
            continue

        count, last_seen = get_student_attendance_stats(name)

        # Compute attendance % using today's date as reference
        today = datetime.now()
        # Simple heuristic: 1 session per weekday since "2025-01-01"
        start = datetime(2025, 1, 1)
        total_days = max(1, (today - start).days)
        weekdays = sum(
            1 for i in range(total_days)
            if (start + __import__("datetime").timedelta(days=i)).weekday() < 5
        )
        att_pct = round((count / max(weekdays, 1)) * 100, 1)
        att_pct = min(att_pct, 100.0)

        students.append({
            "name":           name,
            "usn":            f"ID-{name[:3].upper()}{len(name):03d}",
            "dept":           "Engineering",
            "att":            f"{att_pct}%",
            "att_count":      count,
            "last":           last_seen,
            "image_count":    len([f for f in os.listdir(folder)
                                   if f.lower().endswith((".jpg", ".jpeg", ".png"))])
        })

    return jsonify(students)


@app.route("/api/students", methods=["POST"])
def add_student():
    """Register a new student with face images."""
    data   = request.json
    name   = data.get("name", "").strip()
    images = data.get("images", [])

    if not name or not images:
        return jsonify({"success": False, "message": "Name and images are required"}), 400

    student_dir = os.path.join(DATASET_PATH, name)
    os.makedirs(student_dir, exist_ok=True)

    saved_count = 0
    for idx, b64 in enumerate(images):
        img = decode_base64_image(b64)
        if img is not None:
            cv2.imwrite(os.path.join(student_dir, f"{idx}.jpg"), img)
            saved_count += 1

    # Invalidate DeepFace cache so it rebuilds embeddings on next scan
    for pkl_name in ["representations_facenet.pkl", "representations_vgg_face.pkl", "representations_arcface.pkl"]:
        pkl_path = os.path.join(DATASET_PATH, pkl_name)
        if os.path.exists(pkl_path):
            os.remove(pkl_path)

    return jsonify({
        "success": True,
        "message": f"Registered {name} with {saved_count} images. AI model will update on next scan."
    })


@app.route("/api/students/<student_name>", methods=["DELETE"])
def delete_student(student_name):
    import shutil
    student_dir = os.path.join(DATASET_PATH, student_name)
    if not os.path.exists(student_dir):
        return jsonify({"success": False, "message": "Student not found."}), 404

    shutil.rmtree(student_dir)
    for pkl_name in ["representations_facenet.pkl", "representations_vgg_face.pkl"]:
        pkl_path = os.path.join(DATASET_PATH, pkl_name)
        if os.path.exists(pkl_path):
            os.remove(pkl_path)

    return jsonify({"success": True, "message": f"Student '{student_name}' deleted."})


@app.route("/api/students/<student_name>", methods=["PUT"])
def edit_student(student_name):
    data     = request.json
    new_name = data.get("new_name", "").strip()
    if not new_name:
        return jsonify({"success": False, "message": "New name required."}), 400

    old_dir = os.path.join(DATASET_PATH, student_name)
    new_dir = os.path.join(DATASET_PATH, new_name)

    if not os.path.exists(old_dir):
        return jsonify({"success": False, "message": "Student not found."}), 404
    if os.path.exists(new_dir):
        return jsonify({"success": False, "message": f"A student named '{new_name}' already exists."}), 409

    os.rename(old_dir, new_dir)
    for pkl_name in ["representations_facenet.pkl", "representations_vgg_face.pkl"]:
        pkl_path = os.path.join(DATASET_PATH, pkl_name)
        if os.path.exists(pkl_path):
            os.remove(pkl_path)

    return jsonify({"success": True, "message": f"Student renamed to '{new_name}'."})


# ═══════════════════════════════════════════════════════════════════════════
#  MANUAL ATTENDANCE
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/manual-attendance", methods=["POST"])
def manual_attendance():
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized."}), 500

    data   = request.json
    name   = data.get("name")
    status = data.get("status")

    if not name or not status:
        return jsonify({"success": False, "message": "Name and status required"}), 400

    now      = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    doc_ref = db.collection("attendance").document(f"{date_str}_{name}")
    doc_ref.set({
        "name":      name,
        "date":      date_str,
        "time":      time_str,
        "status":    status,
        "method":    "Manual Override",
        "timestamp": firestore.SERVER_TIMESTAMP,
    }, merge=True)

    return jsonify({"success": True, "message": f"Marked {name} as {status}"})


# ═══════════════════════════════════════════════════════════════════════════
#  ATTENDANCE QUERY
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/attendance", methods=["GET"])
def get_attendance():
    """Return attendance records. Optional ?date=YYYY-MM-DD filter."""
    if not db:
        return jsonify({"error": "Firebase not initialized"}), 500

    date_filter = request.args.get("date")   # e.g. "2026-05-18"

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
                "method": d.get("method", "AI Vision"),
            })

        return jsonify(records)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  DEFAULTERS
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/defaulters", methods=["GET"])
def get_defaulters():
    """Return students with less than 75% attendance."""
    if not db:
        return jsonify({"error": "Firebase not initialized"}), 500

    threshold = float(request.args.get("threshold", 75.0))

    students = []
    if os.path.exists(DATASET_PATH):
        for name in sorted(os.listdir(DATASET_PATH)):
            if os.path.isdir(os.path.join(DATASET_PATH, name)) and not name.startswith("."):
                count, last_seen = get_student_attendance_stats(name)
                # Simple % relative to working days in the year so far
                today = datetime.now()
                start = datetime(today.year, 1, 1)
                total_days = max(1, (today - start).days)
                import datetime as dt_module
                weekdays = sum(
                    1 for i in range(total_days)
                    if (start + dt_module.timedelta(days=i)).weekday() < 5
                )
                att_pct = round((count / max(weekdays, 1)) * 100, 1)
                if att_pct < threshold:
                    students.append({
                        "name":      name,
                        "att_count": count,
                        "att_pct":   att_pct,
                        "last_seen": last_seen
                    })

    return jsonify(students)


# ═══════════════════════════════════════════════════════════════════════════
#  EXPORT (CSV)
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/api/export-attendance", methods=["GET"])
def export_attendance():
    if not db:
        return jsonify({"error": "Firebase not initialized"}), 500

    date_filter = request.args.get("date")
    report_type = request.args.get("type", "all")   # all | defaulters

    try:
        si = io.StringIO()
        cw = csv.writer(si)

        if report_type == "defaulters":
            # Defaulters CSV
            cw.writerow(["Student Name", "Sessions Present", "Attendance %", "Last Seen"])
            if os.path.exists(DATASET_PATH):
                for name in sorted(os.listdir(DATASET_PATH)):
                    if os.path.isdir(os.path.join(DATASET_PATH, name)) and not name.startswith("."):
                        count, last_seen = get_student_attendance_stats(name)
                        today = datetime.now()
                        start = datetime(today.year, 1, 1)
                        total_days = max(1, (today - start).days)
                        import datetime as dt_module
                        weekdays = sum(
                            1 for i in range(total_days)
                            if (start + dt_module.timedelta(days=i)).weekday() < 5
                        )
                        att_pct = round((count / max(weekdays, 1)) * 100, 1)
                        if att_pct < 75:
                            cw.writerow([name, count, f"{att_pct}%", last_seen])
            filename = "defaulters_report.csv"
        else:
            # Full attendance CSV
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
                    d.get("method", "AI Vision"),
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
    if os.path.exists(DATASET_PATH):
        student_count = sum(
            1 for n in os.listdir(DATASET_PATH)
            if os.path.isdir(os.path.join(DATASET_PATH, n)) and not n.startswith(".")
        )

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
        "dataset_path":    DATASET_PATH,
        "timestamp":       datetime.now().isoformat(),
    })


# ═══════════════════════════════════════════════════════════════════════════
#  RUN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    os.makedirs(DATASET_PATH, exist_ok=True)
    print(f"📂 Dataset path: {DATASET_PATH}")
    print(f"🚀 Starting Flask on http://0.0.0.0:5005")
    app.run(host="0.0.0.0", port=5005, debug=True)
