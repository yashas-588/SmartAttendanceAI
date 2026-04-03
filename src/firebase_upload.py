import uuid
import cv2
from firebase_admin import storage

def upload_image(frame):
    try:
        bucket = storage.bucket()

        filename = f"faces/{uuid.uuid4()}.jpg"
        blob = bucket.blob(filename)

        temp_path = "temp.jpg"
        cv2.imwrite(temp_path, frame)

        blob.upload_from_filename(temp_path)
        blob.make_public()

        image_url = f"https://storage.googleapis.com/{bucket.name}/{filename}"

        print("✅ Uploaded:", image_url)

        return image_url

    except Exception as e:
        print("❌ Upload error:", e)
        return None