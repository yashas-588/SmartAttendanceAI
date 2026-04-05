import cv2
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore, storage
from datetime import datetime
import uuid
import os

# 🔐 Firebase init
cred = credentials.Certificate("firebase_key.json")

firebase_admin.initialize_app(cred, {
    'storageBucket': 'smart-attendance-ai-7139f.firebasestorage.app'
})

db = firestore.client()
bucket = storage.bucket()

# Load model
recognizer = cv2.face.LBPHFaceRecognizer_create()
recognizer.read("trainer.yml")

labels = np.load("labels.npy", allow_pickle=True).item()

face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

# Camera
cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)

marked_today = set()

# 🔥 FINAL FUNCTION (CORRECT IMAGE UPLOAD)
def mark_attendance(name, frame):
    today = datetime.now().strftime("%Y-%m-%d")
    time_now = datetime.now().strftime("%H:%M:%S")

    if name in marked_today:
        return

    # create temp folder
    if not os.path.exists("temp"):
        os.makedirs("temp")

    filename = f"{name}_{uuid.uuid4().hex}.jpg"
    filepath = f"temp/{filename}"

    # save image locally
    cv2.imwrite(filepath, frame)

    # upload to Firebase Storage
    blob = bucket.blob(f"attendance/{filename}")
    blob.upload_from_filename(filepath)

    # make image public
    blob.make_public()

    # get correct URL
    image_url = blob.public_url

    # store in Firestore
    db.collection("attendance").add({
        "name": name,
        "date": today,
        "time": time_now,
        "subject": "TEST",
        "image": image_url   # ✅ THIS IS THE FIX
    })

    marked_today.add(name)
    print(f"✅ Stored {name} with image URL")

# 🎥 MAIN LOOP
while True:
    ret, frame = cap.read()
    if not ret:
        print("❌ Camera error")
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces:
        face = gray[y:y+h, x:x+w]

        id_, conf = recognizer.predict(face)

        if conf < 40:
            name = labels[id_]
            mark_attendance(name, frame)
        else:
            name = "Unknown"

        cv2.rectangle(frame, (x,y), (x+w,y+h), (0,255,0), 2)
        cv2.putText(frame, name, (x,y-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)

    cv2.imshow("AI Attendance", frame)

    if cv2.waitKey(1) == 27:
        break

cap.release()
cv2.destroyAllWindows()