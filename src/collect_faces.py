import cv2
import os
import time

# 🔥 MAC FIXED CAMERA FUNCTION
def get_camera():
    for i in range(3):
        cap = cv2.VideoCapture(i, cv2.CAP_AVFOUNDATION)
        time.sleep(1)

        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None:
                print(f"✅ Using camera index: {i}")
                return cap

    print("❌ No working camera found")
    exit()

cap = get_camera()

# 🔥 FACE DETECTOR
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
)

# 🔥 INPUT NAME
name = input("Enter your name: ").strip().lower()

# 🔥 CREATE DATASET FOLDER
dataset_path = f"dataset/{name}"
os.makedirs(dataset_path, exist_ok=True)

count = 0
max_images = 50

print("📸 Look at camera. Move your face slowly...")

while True:
    ret, frame = cap.read()

    # 🔥 SAFE CHECK
    if not ret or frame is None:
        continue

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces:
        count += 1

        face_img = gray[y:y+h, x:x+w]

        file_path = os.path.join(dataset_path, f"{count}.jpg")
        cv2.imwrite(file_path, face_img)

        cv2.rectangle(frame, (x, y), (x+w, y+h), (0,255,0), 2)

    cv2.imshow("Collecting Faces", frame)

    # 🔥 STOP CONDITIONS
    if count >= max_images:
        print("✅ Done collecting images")
        break

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()