import uuid
import cv2
from firebase_admin import storage

def upload_image(frame):
    bucket = storage.bucket()

    filename = f"attendance/{uuid.uuid4().hex}.jpg"
    temp_path = "temp.jpg"

    cv2.imwrite(temp_path, frame)

    blob = bucket.blob(filename)
    blob.upload_from_filename(temp_path)
    blob.make_public()

    return blob.public_url