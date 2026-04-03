import cv2

for i in range(5):
    cap = cv2.VideoCapture(i, cv2.CAP_AVFOUNDATION)

    if cap.isOpened():
        ret, frame = cap.read()
        if ret:
            print(f"Camera index {i} working")
            cv2.imshow(f"Camera {i}", frame)
            cv2.waitKey(2000)  # shows for 2 seconds
            cap.release()

cv2.destroyAllWindows()