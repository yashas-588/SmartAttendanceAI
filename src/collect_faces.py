import cv2
import os

name = "yashas"
save_path = f"dataset/{name}"

os.makedirs(save_path, exist_ok=True)

cap = cv2.VideoCapture(1, cv2.CAP_AVFOUNDATION)

face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

count = 0

print("📸 Press SPACE to capture | ESC to exit")

while True:
    ret, frame = cap.read()
    if not ret:
        continue

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces:
        face = frame[y:y+h, x:x+w]

        cv2.rectangle(frame, (x, y), (x+w, y+h), (0,255,0), 2)

    cv2.imshow("Capture Faces", frame)

    key = cv2.waitKey(1)

    if key == 32:  # SPACE
        count += 1
        img_path = f"{save_path}/{count}.jpg"
        cv2.imwrite(img_path, face)
        print(f"Saved {img_path}")

    elif key == 27:  # ESC
        break

cap.release()
cv2.destroyAllWindows()