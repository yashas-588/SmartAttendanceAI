import firebase_admin
from firebase_admin import credentials, firestore

cred = credentials.Certificate("firebase_key.json")
firebase_admin.initialize_app(cred)

db = firestore.client()

docs = db.collection("attendance").stream()

count = 0

for doc in docs:
    doc.reference.delete()
    count += 1

print(f"🔥 Deleted {count} records")