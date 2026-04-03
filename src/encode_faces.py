import face_recognition
import os
import pickle

dataset_path = "dataset"
encodings = []
names = []

print("Encoding faces...")

for person in os.listdir(dataset_path):
    person_path = os.path.join(dataset_path, person)

    for img_name in os.listdir(person_path):
        img_path = os.path.join(person_path, img_name)
        image = face_recognition.load_image_file(img_path)

        face_enc = face_recognition.face_encodings(image)

        if len(face_enc) > 0:
            encodings.append(face_enc[0])
            names.append(person)
        else:
            print(f"No face found in {img_path}")

# Save ONLY if data exists
if len(encodings) == 0:
    print("No encodings created ❌")
else:
    data = {"encodings": encodings, "names": names}

    os.makedirs("encodings", exist_ok=True)
    with open("encodings/encodings.pickle", "wb") as f:
        pickle.dump(data, f)

    print("Encoding complete ✅")