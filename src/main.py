import cv2
import os
import numpy as np
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore, storage
from firebase_upload import upload_image
from timetable import get_current_class

# ---------------- FIREBASE ----------------
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred, {
        'storageBucket': 'smart-attendance-ai-7139f.firebasestorage.app'
    })

db = firestore.client()

# ---------------- LOAD DATASET ----------------
dataset_path = "dataset"

faces = []
labels = []
label_map = {}
current_label = 0

for person in os.listdir(dataset_path):
    person_path = os.path.join(dataset_path, person)

    if not os.path.isdir(person_path):
        continue

    label_map[current_label] = person

    for img_name in os.listdir(person_path):
        img_path = os.path.join(person_path, img_name)

        img = cv2.imread(img_path)
        if img is None:
            continue

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces.append(gray)
        labels.append(current_label)

    current_label += 1

labels = np.array(labels)

# ---------------- TRAIN MODEL ----------------
recognizer = cv2.face.LBPHFaceRecognizer_create()
recognizer.train(faces, labels)

# ---------------- FACE DETECTOR ----------------
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

# ---------------- CAMERA (MAC FIXED) ----------------
cap = cv2.VideoCapture(1, cv2.CAP_AVFOUNDATION)

if not cap.isOpened():
    print("❌ Camera not working")
    exit()

# ---------------- CACHE ----------------
marked_today = set()

def already_marked(name, today):
    records = db.collection("attendance") \
        .where("name", "==", name) \
        .where("date", "==", today) \
        .stream()

    return len(list(records)) > 0

def mark_attendance(name, frame, subject):
    today = datetime.now().strftime("%Y-%m-%d")

    key = f"{name}_{today}_{subject}"

    if key in marked_today:
        return

    if already_marked(name, today):
        print(f"{name} already marked today")
        marked_today.add(key)
        return

    now = datetime.now()

    image_url = upload_image(frame)

    if image_url is None:
        return

    db.collection("attendance").add({
        "name": name,
        "date": today,
        "time": now.strftime("%H:%M:%S"),
        "subject": subject,
        "image": image_url
    })

    print(f"✅ {name} marked for {subject}")
    marked_today.add(key)

# ---------------- RUN ----------------
print("🚀 Smart Attendance Running...")

while True:
    ret, frame = cap.read()
    if not ret:
        continue

    current_class = get_current_class()

    # 🔴 NO CLASS
    if current_class is None:
        cv2.putText(frame, "No Class Now", (20,50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0,0,255), 2)

        cv2.imshow("Attendance", frame)

        if cv2.waitKey(1) & 0xFF == 27:
            break

        continue

    # 🟢 CLASS ACTIVE
    cv2.putText(frame, f"Class: {current_class}", (20,50),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces_detected = face_cascade.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces_detected:
        roi = gray[y:y+h, x:x+w]

        label, confidence = recognizer.predict(roi)
        name = label_map.get(label, "Unknown")

        if confidence < 80 and name != "Unknown":
            mark_attendance(name, frame, current_class)

        # draw box
        cv2.rectangle(frame, (x, y), (x+w, y+h), (0,255,0), 2)
        cv2.putText(frame, name, (x, y-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)

    cv2.imshow("Attendance", frame)

    if cv2.waitKey(1) & 0xFF == 27:
        break

cap.release()
cv2.destroyAllWindows()