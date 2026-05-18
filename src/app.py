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

# Setup path so we can import anti-spoofing API
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANTI_SPOOF_DIR = os.path.join(BASE_DIR, "anti-spoofing")
if ANTI_SPOOF_DIR not in sys.path:
    sys.path.insert(0, ANTI_SPOOF_DIR)

from api.api_integration import antispoof_bp

app = Flask(__name__)
CORS(app) # Allow frontend to communicate with backend API

# Register Anti-spoofing routes
app.register_blueprint(antispoof_bp)

# Firebase init
if not firebase_admin._apps:
    cred_path = os.path.join(BASE_DIR, "firebase_key.json")
    if os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    else:
        print("Warning: firebase_key.json not found. Attendance will fail.")

db = firestore.client() if firebase_admin._apps else None
DATASET_PATH = os.path.join(BASE_DIR, "dataset")

last_marked = {}

def decode_base64_image(base64_string):
    if not base64_string or len(base64_string) < 20:
        return None
    if "," in base64_string:
        base64_string = base64_string.split(',')[1]
    if not base64_string:
        return None
    img_data = base64.b64decode(base64_string)
    np_arr = np.frombuffer(img_data, np.uint8)
    if np_arr.size == 0:
        return None
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    return img

@app.route("/api/mark-attendance", methods=["POST"])
def mark_attendance_api():
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized. Check firebase_key.json"}), 500

    data = request.json
    if not data or 'image' not in data:
        return jsonify({"success": False, "message": "No image provided in request"}), 400
        
    lat = data.get('lat')
    lon = data.get('lon')
    
    try:
        frame = decode_base64_image(data['image'])
        if frame is None:
            return jsonify({"success": False, "message": "Invalid or empty image frame from camera."}), 400
            
        # Search for face in dataset using DeepFace
        results = DeepFace.find(
            img_path=frame,
            db_path=DATASET_PATH,
            enforce_detection=False,
            model_name="Facenet",
            distance_metric="cosine"
        )
        
        marked_names = []
        messages = []
        
        if isinstance(results, list):
            df_list = results
        else:
            df_list = [results]
            
        for df in df_list:
            if len(df) > 0:
                identity_path = df.iloc[0]["identity"]
                name = os.path.basename(os.path.dirname(identity_path))
                
                current_time = time.time()
                if name not in last_marked or current_time - last_marked[name] > 300: # 5 min cooldown
                    last_marked[name] = current_time
                    
                    now = datetime.now()
                    date_str = now.strftime("%Y-%m-%d")
                    time_str = now.strftime("%H:%M:%S")
                    
                    doc_ref = db.collection("attendance").document(f"{date_str}_{name}")
                    doc = doc_ref.get()
                    
                    if not doc.exists:
                        doc_data = {
                            "name": name,
                            "date": date_str,
                            "time": time_str,
                            "timestamp": firestore.SERVER_TIMESTAMP
                        }
                        if lat is not None and lon is not None:
                            doc_data["location"] = {"lat": lat, "lon": lon}
                            
                        doc_ref.set(doc_data)
                        marked_names.append(name)
                        messages.append(f"{name} (Marked)")
                    else:
                        marked_names.append(name)
                        messages.append(f"{name} (Already marked)")
                else:
                    marked_names.append(name)
                    messages.append(f"{name} (Cooldown)")

        if marked_names:
            final_name = ", ".join(marked_names)
            final_msg = " | ".join(messages)
            return jsonify({"success": True, "name": final_name, "message": final_msg})
        else:
            return jsonify({"success": False, "message": "Face not recognized."}), 401
            
    except Exception as e:
        print("Error during marking attendance:", e)
        return jsonify({"success": False, "message": f"System Error: {str(e)}"}), 500

@app.route("/api/students", methods=["GET"])
def get_students():
    if not os.path.exists(DATASET_PATH):
        return jsonify([])
    students = []
    for name in os.listdir(DATASET_PATH):
        if os.path.isdir(os.path.join(DATASET_PATH, name)) and not name.startswith('.'):
            students.append({
                "name": name,
                "usn": f"ID-{name[:3].upper()}001",
                "dept": "Engineering",
                "att": "100%",
                "last": "Today"
            })
    return jsonify(students)

@app.route("/api/students", methods=["POST"])
def add_student():
    data = request.json
    name = data.get('name', '').strip()
    images = data.get('images', [])
    
    if not name or not images:
        return jsonify({"success": False, "message": "Name and images required"}), 400
        
    student_dir = os.path.join(DATASET_PATH, name)
    os.makedirs(student_dir, exist_ok=True)
    
    saved_count = 0
    for idx, b64 in enumerate(images):
        try:
            img = decode_base64_image(b64)
            if img is not None:
                cv2.imwrite(os.path.join(student_dir, f"{idx}.jpg"), img)
                saved_count += 1
        except Exception as e:
            print(f"Error saving image {idx}:", e)
            
    # Remove DeepFace representations so it rebuilds the embeddings on next scan
    rep_file = os.path.join(DATASET_PATH, "representations_facenet.pkl")
    if os.path.exists(rep_file):
        os.remove(rep_file)
        
    return jsonify({"success": True, "message": f"Saved {saved_count} images. AI model will retrain on next scan."})

@app.route("/api/manual-attendance", methods=["POST"])
def manual_attendance():
    if not db:
        return jsonify({"success": False, "message": "Firebase not initialized."}), 500
        
    data = request.json
    name = data.get("name")
    status = data.get("status")
    
    if not name or not status:
        return jsonify({"success": False, "message": "Name and status required"}), 400
        
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    
    doc_ref = db.collection("attendance").document(f"{date_str}_{name}")
    doc_ref.set({
        "name": name,
        "date": date_str,
        "time": time_str,
        "status": status,
        "method": "Manual Override",
        "timestamp": firestore.SERVER_TIMESTAMP
    })
    
    return jsonify({"success": True, "message": f"Marked {name} as {status}"})

@app.route("/api/export-attendance", methods=["GET"])
def export_attendance():
    if not db:
        return jsonify({"error": "Firebase not initialized"}), 500
        
    docs = db.collection("attendance").order_by("timestamp", direction=firestore.Query.DESCENDING).get()
    
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['Student Name', 'Date', 'Time', 'Status', 'Method'])
    
    for doc in docs:
        d = doc.to_dict()
        cw.writerow([
            d.get('name', ''), 
            d.get('date', ''), 
            d.get('time', ''), 
            d.get('status', 'Present'), 
            d.get('method', 'AI Vision')
        ])
        
    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=attendance_report.csv"}
    )

@app.route("/api/students/<student_name>", methods=["DELETE"])
def delete_student(student_name):
    import shutil
    student_dir = os.path.join(DATASET_PATH, student_name)
    if os.path.exists(student_dir):
        shutil.rmtree(student_dir)
        # Remove DeepFace representations so it rebuilds the embeddings
        rep_file = os.path.join(DATASET_PATH, "representations_facenet.pkl")
        if os.path.exists(rep_file):
            os.remove(rep_file)
        return jsonify({"success": True, "message": f"Student {student_name} deleted."})
    return jsonify({"success": False, "message": "Student not found."}), 404

@app.route("/api/students/<student_name>", methods=["PUT"])
def edit_student(student_name):
    data = request.json
    new_name = data.get("new_name", "").strip()
    if not new_name:
        return jsonify({"success": False, "message": "New name required."}), 400
        
    old_dir = os.path.join(DATASET_PATH, student_name)
    new_dir = os.path.join(DATASET_PATH, new_name)
    
    if not os.path.exists(old_dir):
        return jsonify({"success": False, "message": "Student not found."}), 404
        
    os.rename(old_dir, new_dir)
    # Remove DeepFace representations
    rep_file = os.path.join(DATASET_PATH, "representations_facenet.pkl")
    if os.path.exists(rep_file):
        os.remove(rep_file)
        
    return jsonify({"success": True, "message": f"Student renamed to {new_name}."})

if __name__ == "__main__":
    # Ensure dataset exists before starting
    os.makedirs(DATASET_PATH, exist_ok=True)
    app.run(host="0.0.0.0", port=5005, debug=True)
