import cv2
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore, storage
from datetime import datetime
import uuid
import os
import smtplib
from email.mime.text import MIMEText
import pyttsx3
import time

# 🔐 Firebase init
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred, {
        'storageBucket': 'smart-attendance-ai-7139f.firebasestorage.app'
    })

db = firestore.client()
bucket = storage.bucket()

# 🔊 Voice
engine = pyttsx3.init()

# 🤖 Model
recognizer = cv2.face.LBPHFaceRecognizer_create()
recognizer.read("trainer.yml")

labels = np.load("labels.npy", allow_pickle=True).item()

face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

# 🎥 FINAL CAMERA (WORKS ON MAC)
cap = cv2.VideoCapture(0, cv2.CAP_ANY)

time.sleep(2)

if not cap.isOpened():
    print("❌ Camera not accessible")
    exit()

# 🔥 Warmup frames (IMPORTANT)
for _ in range(10):
    cap.read()

# 🔥 Resolution fix
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

# 🔥 cooldown system
last_marked = {}

# 📧 EMAIL
def send_email(name):
    try:
        sender = "yashasr435@gmail.com"
        password = "zxvvmzjsxrwvgkfm"
        receiver = "yashasr588@gmail.com"

        msg = MIMEText(f"{name} marked attendance.")
        msg["Subject"] = "Attendance Alert"
        msg["From"] = sender
        msg["To"] = receiver

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, receiver, msg.as_string())
        server.quit()

        print("📧 Email sent")
    except Exception as e:
        print("❌ Email error:", e)

# 📞 Voice alert
def fake_call_alert(name):
    try:
        os.system("afplay /System/Library/Sounds/Glass.aiff")
        engine.say(f"{name} marked attendance")
        engine.runAndWait()
    except:
        pass

# 🧠 Mark attendance
def mark_attendance(name, frame):
    now = datetime.now()

    if name in last_marked:
        if (now - last_marked[name]).seconds < 30:
            return

    last_marked[name] = now

    today = now.strftime("%Y-%m-%d")
    time_now = now.strftime("%H:%M:%S")

    os.makedirs("temp", exist_ok=True)

    filename = f"{name}_{uuid.uuid4().hex}.jpg"
    filepath = f"temp/{filename}"

    cv2.imwrite(filepath, frame)

    blob = bucket.blob(f"attendance/{filename}")
    blob.upload_from_filename(filepath)
    blob.make_public()

    image_url = blob.public_url

    db.collection("attendance").add({
        "name": name,
        "usn": "1DS24ISXXX",
        "date": today,
        "time": time_now,
        "subject": "TEST",
        "image": image_url
    })

    send_email(name)
    fake_call_alert(name)

    print(f"✅ Marked {name}")

# 🎥 MAIN LOOP
while True:
    ret, frame = cap.read()

    if not ret or frame is None:
        continue

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces:
        face = gray[y:y+h, x:x+w]

        try:
            id_, conf = recognizer.predict(face)
        except:
            continue

        if conf < 65:
            name = labels.get(id_, "Unknown")
            mark_attendance(name, frame)
        else:
            name = "Unknown"

        cv2.rectangle(frame, (x,y), (x+w,y+h), (0,255,0), 2)
        cv2.putText(frame, name, (x,y-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)

    cv2.imshow("Smart Attendance AI", frame)

    if cv2.waitKey(1) & 0xFF == 27:
        break

cap.release()
cv2.destroyAllWindows()