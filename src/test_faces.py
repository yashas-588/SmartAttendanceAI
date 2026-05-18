import cv2
import os

dataset_path = "dataset/yashas"

face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

for img_name in os.listdir(dataset_path):
    img_path = os.path.join(dataset_path, img_name)

    img = cv2.imread(img_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    print(f"{img_name} -> Faces detected: {len(faces)}")