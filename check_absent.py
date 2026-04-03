import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
from send_email import send_email

# init firebase
cred = credentials.Certificate("firebase_key.json")
firebase_admin.initialize_app(cred)

db = firestore.client()

today = datetime.now().strftime("%Y-%m-%d")

# get students
students = db.collection("students").stream()
student_data = {}

for s in students:
    data = s.to_dict()
    student_data[data["name"]] = data["parent_email"]

# get today's attendance
attendance = db.collection("attendance").where("date", "==", today).stream()
present = set()

for a in attendance:
    present.add(a.to_dict()["name"])

# find absentees
absentees = set(student_data.keys()) - present

print("📋 Absentees:", absentees)

# send emails
for name in absentees:
    send_email(student_data[name], name)