import cv2
import os
import numpy as np

data_path = "dataset"
faces = []
labels = []
label_map = {}
current_id = 0

for person in os.listdir(data_path):
    person_path = os.path.join(data_path, person)

    if not os.path.isdir(person_path):
        continue

    label_map[current_id] = person

    for img_name in os.listdir(person_path):
        img_path = os.path.join(person_path, img_name)
        img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)

        if img is None:
            continue

        faces.append(img)
        labels.append(current_id)

    current_id += 1

recognizer = cv2.face.LBPHFaceRecognizer_create()
recognizer.train(faces, np.array(labels))
recognizer.save("trainer.yml")

np.save("labels.npy", label_map)

print("✅ Training Completed")