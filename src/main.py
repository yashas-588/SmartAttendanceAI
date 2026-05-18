import cv2
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import time

# 🔐 Firebase init
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()

recognizer = cv2.face.LBPHFaceRecognizer_create()
recognizer.read("trainer.yml")

labels = np.load("labels.npy", allow_pickle=True).item()
names = labels

face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

cap = cv2.VideoCapture(0)
cap.set(3, 640)
cap.set(4, 480)

last_marked = {}

def mark_attendance(name):
    now = datetime.now()
    date = now.strftime("%Y-%m-%d")
    time_now = now.strftime("%H:%M:%S")

    doc_ref = db.collection("attendance").document(date).collection("students").document(name)

    doc_ref.set({
        "name": name,
        "time": time_now,
        "timestamp": firestore.SERVER_TIMESTAMP
    })

    print(f"✅ {name} marked at {time_now}")

while True:
    ret, frame = cap.read()
    if not ret:
        break

    frame = cv2.resize(frame, (640, 480))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces:
        roi_gray = gray[y:y+h, x:x+w]
        try:
            id_, confidence = recognizer.predict(roi_gray)
        except:
            id_, confidence = None, 999

        name = "Unknown"

        if confidence < 80 and id_ in names:
            name = names[id_]

            current_time = time.time()

            if name not in last_marked or current_time - last_marked[name] > 300:
                last_marked[name] = current_time
                mark_attendance(name)

        cv2.putText(frame, name, (x, y-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0,255,0), 2)

        cv2.rectangle(frame, (x,y), (x+w,y+h), (255,0,0), 2)

    cv2.imshow("AI Attendance", frame)

    if cv2.waitKey(1) == 27:
        break

cap.release()
cv2.destroyAllWindows()