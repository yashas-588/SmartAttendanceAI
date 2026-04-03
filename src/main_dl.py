import cv2
import face_recognition
import os
from datetime import datetime
from collections import defaultdict
import firebase_admin
from firebase_admin import credentials, firestore
from firebase_upload import upload_image
from send_email import send_email

# ---------------- FIREBASE ----------------
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ---------------- LOAD DATASET ----------------
known_encodings = []
known_names = []

dataset_path = "dataset"

for person in os.listdir(dataset_path):
    person_path = os.path.join(dataset_path, person)

    if not os.path.isdir(person_path):
        continue

    for img_name in os.listdir(person_path):
        img_path = os.path.join(person_path, img_name)

        image = face_recognition.load_image_file(img_path)
        encodings = face_recognition.face_encodings(image)

        if encodings:
            known_encodings.append(encodings[0])
            known_names.append(person)

print("✅ Faces loaded")

# ---------------- STORAGE ----------------
present_students = defaultdict(set)
last_session = False

parent_emails = {
    "yashas": "yashasr416@gmail.com"
}

def get_all_students():
    return [name for name in os.listdir("dataset")
            if os.path.isdir(os.path.join("dataset", name))]

def mark_attendance(name, frame):
    today = datetime.now().strftime("%Y-%m-%d")

    if name in present_students["class"]:
        return

    now = datetime.now()

    image_url = upload_image(frame)

    db.collection("attendance").add({
        "name": name,
        "date": today,
        "time": now.strftime("%H:%M:%S"),
        "status": "Present",
        "image": image_url
    })

    present_students["class"].add(name)
    print(f"✅ {name} PRESENT")

def mark_absent():
    all_students = get_all_students()
    present = present_students["class"]
    today = datetime.now().strftime("%Y-%m-%d")

    for student in all_students:
        if student not in present:
            db.collection("attendance").add({
                "name": student,
                "date": today,
                "time": "--",
                "status": "Absent",
                "image": ""
            })

            print(f"❌ {student} ABSENT")

            if student in parent_emails:
                send_email(parent_emails[student], student, "Class")

# ---------------- CAMERA ----------------
cap = cv2.VideoCapture(1, cv2.CAP_AVFOUNDATION)

print("🚀 Smart DL Attendance Running")

THRESHOLD = 0.45   # 🔥 STRICT

while True:
    ret, frame = cap.read()
    if not ret:
        continue

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    face_locations = face_recognition.face_locations(rgb)
    face_encodings = face_recognition.face_encodings(rgb, face_locations)

    session_active = len(face_encodings) > 0

    # detect session end → mark absent
    if not session_active and last_session:
        print("📢 Session ended → marking absent")
        mark_absent()

    last_session = session_active

    for (top, right, bottom, left), face_encoding in zip(face_locations, face_encodings):

        face_distances = face_recognition.face_distance(known_encodings, face_encoding)

        name = "Unknown"
        distance = 1.0

        if len(face_distances) > 0:
            best_match_index = face_distances.argmin()
            distance = face_distances[best_match_index]

            if distance < THRESHOLD:
                name = known_names[best_match_index]

        # 🔥 mark only valid face
        if name != "Unknown":
            mark_attendance(name, frame)

        cv2.rectangle(frame, (left, top), (right, bottom), (0,255,0), 2)
        cv2.putText(frame, f"{name} ({distance:.2f})", (left, top-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)

    cv2.imshow("Attendance DL", frame)

    if cv2.waitKey(1) & 0xFF == 27:
        print("📢 Ending session manually → marking absent")
        mark_absent()
        break

cap.release()
cv2.destroyAllWindows()