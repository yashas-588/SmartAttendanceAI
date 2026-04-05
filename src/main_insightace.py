import cv2
import os
import numpy as np
from insightface.app import FaceAnalysis

# -------- INIT MODEL --------
app = FaceAnalysis(name="buffalo_l")
app.prepare(ctx_id=0, det_size=(320, 320))  # 🔥 smaller = faster

# -------- LOAD DATASET --------
dataset_path = "dataset"

known_embeddings = []
known_names = []

for person in os.listdir(dataset_path):
    person_path = os.path.join(dataset_path, person)

    if not os.path.isdir(person_path):
        continue

    for img_name in os.listdir(person_path):
        img_path = os.path.join(person_path, img_name)

        img = cv2.imread(img_path)
        if img is None:
            continue

        faces = app.get(img)

        if len(faces) > 0:
            emb = faces[0].embedding
            emb = emb / np.linalg.norm(emb)
            known_embeddings.append(emb)
            known_names.append(person)

known_embeddings = np.array(known_embeddings)

print(f"✅ Dataset loaded: {len(known_embeddings)} faces")

# -------- RECOGNITION --------
def recognize(face_emb):
    face_emb = face_emb / np.linalg.norm(face_emb)

    similarities = np.dot(known_embeddings, face_emb)

    best_idx = np.argmax(similarities)
    best_score = similarities[best_idx]

    print(f"🔥 Similarity: {best_score:.2f}")

    if best_score > 0.65:
        return known_names[best_idx]
    else:
        return "Unknown"

# -------- CAMERA --------
cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)

print("🚀 Running (ESC to exit)")

while True:
    ret, frame = cap.read()
    if not ret:
        continue

    # 🔥 Resize frame (important for speed)
    small_frame = cv2.resize(frame, (640, 480))

    faces = app.get(small_frame)

    if len(faces) == 0:
        cv2.putText(frame, "No face", (50,50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0,0,255), 2)

    for face in faces:
        emb = face.embedding
        name = recognize(emb)

        x1, y1, x2, y2 = map(int, face.bbox)

        cv2.rectangle(small_frame, (x1,y1), (x2,y2), (0,255,0), 2)
        cv2.putText(small_frame, name, (x1, y1-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)

    cv2.imshow("Attendance", small_frame)

    if cv2.waitKey(1) & 0xFF == 27:
        break

cap.release()
cv2.destroyAllWindows()