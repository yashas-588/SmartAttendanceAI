import cv2
import numpy as np
import mediapipe as mp
import urllib.request
import os
import torch
import torch.nn as nn
from scipy.spatial import distance as dist

class SimpleMPSModel(nn.Module):
    def __init__(self):
        super(SimpleMPSModel, self).__init__()
        # A simple feature extractor to demonstrate MPS compatibility
        self.conv1 = nn.Conv2d(3, 16, kernel_size=3, stride=2, padding=1)
        self.relu = nn.ReLU()
        self.pool = nn.AdaptiveAvgPool2d((1, 1))
        self.fc = nn.Linear(16, 1)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        x = self.conv1(x)
        x = self.relu(x)
        x = self.pool(x)
        x = torch.flatten(x, 1)
        x = self.fc(x)
        return self.sigmoid(x)

class SpoofEngine:
    def __init__(self):
        # Setup MediaPipe Face Landmarker (Tasks API)
        model_path = os.path.join(os.path.dirname(__file__), 'face_landmarker.task')
        if not os.path.exists(model_path):
            print("Downloading MediaPipe Face Landmarker model...")
            urllib.request.urlretrieve(
                'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task', 
                model_path
            )

        BaseOptions = mp.tasks.BaseOptions
        FaceLandmarker = mp.tasks.vision.FaceLandmarker
        FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
        VisionRunningMode = mp.tasks.vision.RunningMode

        options = FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            running_mode=VisionRunningMode.IMAGE,
            num_faces=4
        )
        self.face_landmarker = FaceLandmarker.create_from_options(options)

        # PyTorch MPS initialization
        self.device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
        self.deep_model = SimpleMPSModel().to(self.device)
        self.deep_model.eval()
        print(f"✅ SpoofEngine Initialized. Deep Model running on: {self.device}")

        # EAR thresholds
        self.EAR_THRESH = 0.20
        self.EAR_CONSEC_FRAMES = 1

    def _eye_aspect_ratio(self, eye_landmarks):
        A = dist.euclidean(eye_landmarks[1], eye_landmarks[5])
        B = dist.euclidean(eye_landmarks[2], eye_landmarks[4])
        C = dist.euclidean(eye_landmarks[0], eye_landmarks[3])
        return (A + B) / (2.0 * C)

    def detect_blink(self, frames):
        blink_detected = False
        frame_counter = 0

        LEFT_EYE = [33, 160, 158, 133, 153, 144]
        RIGHT_EYE = [362, 385, 387, 263, 373, 380]

        for frame in frames:
            img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
            
            result = self.face_landmarker.detect(mp_image)

            if result.face_landmarks:
                ear_for_frame = False
                h, w, _ = frame.shape
                
                for landmarks in result.face_landmarks:
                    left_eye_points = np.array([[landmarks[i].x * w, landmarks[i].y * h] for i in LEFT_EYE])
                    right_eye_points = np.array([[landmarks[i].x * w, landmarks[i].y * h] for i in RIGHT_EYE])

                    left_ear = self._eye_aspect_ratio(left_eye_points)
                    right_ear = self._eye_aspect_ratio(right_eye_points)
                    ear = (left_ear + right_ear) / 2.0

                    if ear < self.EAR_THRESH:
                        ear_for_frame = True
                        break

                if ear_for_frame:
                    frame_counter += 1
                else:
                    if frame_counter >= self.EAR_CONSEC_FRAMES:
                        blink_detected = True
                    frame_counter = 0

        if frame_counter >= self.EAR_CONSEC_FRAMES:
            blink_detected = True

        return blink_detected

    def analyze_texture(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        variance = cv2.Laplacian(gray, cv2.CV_64F).var()
        return variance

    def analyze_motion(self, frames):
        if len(frames) < 2:
            return 0.0
            
        motion_scores = []
        for i in range(1, len(frames)):
            gray1 = cv2.cvtColor(frames[i-1], cv2.COLOR_BGR2GRAY)
            gray2 = cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY)
            
            diff = cv2.absdiff(gray1, gray2)
            _, thresh = cv2.threshold(diff, 15, 255, cv2.THRESH_BINARY)
            
            changed_pixels = np.count_nonzero(thresh)
            total_pixels = thresh.size
            motion_score = (changed_pixels / total_pixels) * 100
            motion_scores.append(motion_score)
            
        return np.mean(motion_scores)

    def analyze_deep_features(self, frame):
        img_resized = cv2.resize(frame, (128, 128))
        img_rgb = cv2.cvtColor(img_resized, cv2.COLOR_BGR2RGB)
        
        img_tensor = torch.from_numpy(img_rgb).float().permute(2, 0, 1) / 255.0
        img_tensor = img_tensor.unsqueeze(0).to(self.device)

        with torch.no_grad():
            output = self.deep_model(img_tensor)
            
        return output.item()

    def verify_liveness(self, frames):
        try:
            if not frames:
                return False, 0.0, {"error": "No frames provided"}
                
            mid_idx = len(frames) // 2
            texture_score = self.analyze_texture(frames[mid_idx])
            # Lowered texture threshold (webcams in backlight can be soft/blurry)
            is_textured = texture_score > 3.0

            motion_score = self.analyze_motion(frames)
            # Relaxed motion threshold to account for minor noise or larger movements
            is_moving = motion_score > 0.01

            has_blinked = self.detect_blink(frames)
            deep_score = self.analyze_deep_features(frames[mid_idx])

            print(f"--> Anti-Spoofing Check | Texture: {texture_score:.2f} | Motion: {motion_score:.2f} | Blink: {has_blinked} | Deep: {deep_score:.2f}")

            # A live face must be somewhat textured, and either show motion, a blink or deep model confidence
            is_live = bool(is_textured and (has_blinked or is_moving or deep_score > 0.4))
        
            confidence = 0.95 if is_live else 0.20
            if not is_textured: confidence -= 0.3
            if has_blinked: confidence += 0.04

            confidence = max(0.0, min(1.0, confidence))

            details = {
                "texture_score": float(texture_score),
                "motion_score": float(motion_score),
                "blink_detected": bool(has_blinked),
                "deep_score": float(deep_score),
                "is_textured": bool(is_textured),
                "is_moving": bool(is_moving)
            }

            return is_live, confidence, details
        except Exception as e:
            print(f"Error in verify_liveness: {e}")
            return False, 0.0, {"error": str(e)}
