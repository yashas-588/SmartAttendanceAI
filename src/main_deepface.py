import cv2
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import time
from deepface import DeepFace
import os

# 🔧 Base path (FIXED __file__)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 🔐 Firebase init
if not firebase_admin._apps:
    cred = credentials.Certificate(os.path.join(BASE_DIR, "firebase_key.json"))
    firebase_admin.initialize_app(cred)

db = firestore.client()

# 📁 Dataset path
DATASET_PATH = os.path.join(BASE_DIR, "dataset")

# 📷 Camera setup
cap = cv2.VideoCapture(0)

# ⏱️ tracking
last_marked = {}
frame_count = 0

def mark_attendance(name):
    now = datetime.now()
    date = now.strftime("%Y-%m-%d")
    time_now = now.strftime("%H:%M:%S")

    doc_ref = db.collection("attendance").document(date).collection("students").document(name)
    doc = doc_ref.get()

    if not doc.exists:
        doc_ref.set({
            "name": name,
            "time": time_now,
            "timestamp": firestore.SERVER_TIMESTAMP
        })
        print(f"✅ {name} marked at {time_now}")
    else:
        print(f"⚠️ {name} already marked today")

print("🔥 DeepFace Attendance Started...")

while True:
    ret, frame = cap.read()
    if not ret:
        break

    frame_count += 1

    # ⚡ Skip frames for performance
    if frame_count % 3 != 0:
        continue

    try:
        # 🔍 Face matching
        results = DeepFace.find(
            img_path=frame,
            db_path=DATASET_PATH,
            enforce_detection=False,
            model_name="Facenet",
            distance_metric="cosine"
        )

        name = "Unknown"

        if len(results) > 0 and len(results[0]) > 0:
            identity_path = results[0].iloc[0]["identity"]

            # extract folder name
            name = os.path.basename(os.path.dirname(identity_path))

            current_time = time.time()

            # ⏱️ prevent duplicate marking (5 min cooldown)
            if name not in last_marked or current_time - last_marked[name] > 300:
                last_marked[name] = current_time
                mark_attendance(name)

            # 🖊️ display name
            cv2.putText(frame, name, (50, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

    except Exception as e:
        print("Error:", e)

    # 🖥️ display window
    cv2.imshow("AI Attendance (DeepFace)", frame)

    # ❌ exit on ESC
    if cv2.waitKey(1) == 27:
        break

# 🔚 cleanup
cap.release()
cv2.destroyAllWindows()