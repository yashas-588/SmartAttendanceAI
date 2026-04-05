import cv2
import os

# 🔥 Ask user name
name = input("Enter your name: ").strip()

save_path = f"dataset/{name}"

if not os.path.exists(save_path):
    os.makedirs(save_path)

# Mac camera (LOCKED)
cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)

face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

count = 0

print("\n👉 Press SPACE to capture")
print("👉 Press ESC to exit\n")

while True:
    ret, frame = cap.read()
    if not ret:
        print("❌ Camera error")
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces:
        cv2.rectangle(frame, (x,y), (x+w,y+h), (0,255,0), 2)

    cv2.putText(frame, f"Images: {count}", (10,30),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)

    cv2.imshow("Collect Faces", frame)

    key = cv2.waitKey(1)

    # SPACE = capture
    if key == 32:
        for (x, y, w, h) in faces:
            face = gray[y:y+h, x:x+w]
            count += 1
            cv2.imwrite(f"{save_path}/{count}.jpg", face)
            print(f"📸 Captured {count}")

    # ESC = exit
    elif key == 27:
        break

cap.release()
cv2.destroyAllWindows()

print(f"\n✅ Done. Total images: {count}")