import cv2
import os

name = input("Enter your name: ")

dataset_path = f"dataset/{name}"
os.makedirs(dataset_path, exist_ok=True)

# Use Mac camera properly
video = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)

if not video.isOpened():
    print("Camera failed to open ❌")
    exit()

count = 0
print("Press 's' to capture | ESC to exit")

while True:
    ret, frame = video.read()

    if not ret:
        print("Frame not received ❌")
        break

    cv2.imshow("Capture Faces", frame)

    key = cv2.waitKey(1)

    if key == ord('s'):
        img_path = f"{dataset_path}/{count}.jpg"
        cv2.imwrite(img_path, frame)
        print(f"Saved {img_path}")
        count += 1

    elif key == 27:
        break

video.release()
cv2.destroyAllWindows()