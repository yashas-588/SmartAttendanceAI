import cv2
import numpy as np
import mediapipe as mp
import urllib.request
import os
import torch
from scipy.spatial import distance as dist


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

        self.device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

        # EAR threshold — 0.20 is a reliable blink threshold for MediaPipe 478-point mesh.
        # Higher values (0.25+) cause false positives on naturally small eyes.
        self.EAR_THRESH = 0.20

        # Require 2 consecutive sub-threshold frames to register a blink.
        # EAR_CONSEC_FRAMES=1 fires on any single noisy frame; 2 is the minimum
        # reliable value without missing genuine fast blinks.
        self.EAR_CONSEC_FRAMES = 2

        # Laplacian variance threshold on the face ROI (not full frame).
        # Printed photos / screens score < 10 on a cropped face patch.
        self.TEXTURE_THRESH = 10.0

        # % of pixels that changed between consecutive frames.
        self.MOTION_THRESH = 0.05

        print(
            f"✅ SpoofEngine Initialized. Device: {self.device} | "
            f"Texture≥{self.TEXTURE_THRESH} | Motion≥{self.MOTION_THRESH}% | "
            f"EAR≤{self.EAR_THRESH} (consec={self.EAR_CONSEC_FRAMES})"
        )

    # ──────────────────────────────────────────────
    #  EAR — Eye Aspect Ratio
    # ──────────────────────────────────────────────
    def _eye_aspect_ratio(self, eye_pts):
        """
        Standard 6-point EAR formula.
        eye_pts must be ordered: [outer, top-outer, top-inner, inner, bot-inner, bot-outer]
        i.e. indices 0-3 are horizontal; 1&5, 2&4 are vertical pairs.

        EAR = (||p1-p5|| + ||p2-p4||) / (2 * ||p0-p3||)
        """
        A = dist.euclidean(eye_pts[1], eye_pts[5])   # top-outer ↔ bot-outer
        B = dist.euclidean(eye_pts[2], eye_pts[4])   # top-inner ↔ bot-inner
        C = dist.euclidean(eye_pts[0], eye_pts[3])   # horizontal width
        if C < 1e-6:
            return 0.3  # degenerate case → assume open
        return (A + B) / (2.0 * C)

    def _get_eye_points(self, landmarks, indices, w, h):
        """Extract (x, y) pixel coords for a list of landmark indices."""
        return np.array([[landmarks[i].x * w, landmarks[i].y * h] for i in indices])

    # ──────────────────────────────────────────────
    #  BLINK DETECTION
    # ──────────────────────────────────────────────
    def detect_blink(self, frames):
        """
        Detect a blink across the provided frames.

        MediaPipe 478-point Face Mesh landmark indices for the 6-point EAR model,
        ordered as [outer, top-outer, top-inner, inner, bot-inner, bot-outer]:

          Left eye  (from subject's perspective):
            outer=33, top-outer=160, top-inner=158, inner=133, bot-inner=153, bot-outer=144

          Right eye:
            outer=362, top-outer=385, top-inner=387, inner=263, bot-inner=373, bot-outer=380

        These indices and their geometric roles are documented in the MediaPipe canonical
        face mesh topology map and match the iris/eyelid contour points used by the
        Tasks API FaceLandmarker (same 478-point model as FaceMesh).

        Returns (blink_detected: bool, min_ear: float, face_detected: bool)
        """
        # Ordered: [outer, top-outer, top-inner, inner, bot-inner, bot-outer]
        LEFT_EYE  = [33,  160, 158, 133, 153, 144]
        RIGHT_EYE = [362, 385, 387, 263, 373, 380]

        frame_counter = 0
        blink_detected = False
        min_ear_seen = 1.0
        face_detected = False

        for frame in frames:
            img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
            result = self.face_landmarker.detect(mp_image)

            if not result.face_landmarks:
                # Reset counter on missing frames — don't carry state across gaps
                if frame_counter >= self.EAR_CONSEC_FRAMES:
                    blink_detected = True
                frame_counter = 0
                continue

            face_detected = True
            h, w = frame.shape[:2]

            # Use the first detected face only for blink logic
            # (multiple faces would need per-face state tracking)
            landmarks = result.face_landmarks[0]

            left_pts  = self._get_eye_points(landmarks, LEFT_EYE,  w, h)
            right_pts = self._get_eye_points(landmarks, RIGHT_EYE, w, h)

            left_ear  = self._eye_aspect_ratio(left_pts)
            right_ear = self._eye_aspect_ratio(right_pts)
            ear = (left_ear + right_ear) / 2.0
            min_ear_seen = min(min_ear_seen, ear)

            if ear < self.EAR_THRESH:
                frame_counter += 1
            else:
                if frame_counter >= self.EAR_CONSEC_FRAMES:
                    blink_detected = True
                frame_counter = 0

        # Catch a blink that was still closing on the last frame
        if frame_counter >= self.EAR_CONSEC_FRAMES:
            blink_detected = True

        return blink_detected, min_ear_seen, face_detected

    # ──────────────────────────────────────────────
    #  FACE ROI EXTRACTION
    # ──────────────────────────────────────────────
    def _get_face_roi(self, frame):
        """
        Detect the face bounding box and return a cropped face ROI.
        Falls back to the full frame if no face is detected.
        Analyzing only the face region removes background noise from
        the texture score, making the Laplacian threshold more reliable.
        """
        img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
        result = self.face_landmarker.detect(mp_image)

        if not result.face_landmarks:
            return frame  # fallback: full frame

        h, w = frame.shape[:2]
        landmarks = result.face_landmarks[0]

        xs = [lm.x * w for lm in landmarks]
        ys = [lm.y * h for lm in landmarks]

        x1 = max(0, int(min(xs)))
        y1 = max(0, int(min(ys)))
        x2 = min(w, int(max(xs)))
        y2 = min(h, int(max(ys)))

        if x2 <= x1 or y2 <= y1:
            return frame

        return frame[y1:y2, x1:x2]

    # ──────────────────────────────────────────────
    #  TEXTURE ANALYSIS (Laplacian Variance on face ROI)
    # ──────────────────────────────────────────────
    def analyze_texture(self, frame):
        """
        Compute Laplacian variance on the face ROI, not the full frame.
        A live face has fine skin texture → high variance.
        A printed photo or screen has smooth/blurred regions → low variance.
        """
        roi = self._get_face_roi(frame)
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        return float(laplacian.var())

    # ──────────────────────────────────────────────
    #  MOTION ANALYSIS (Frame Difference)
    # ──────────────────────────────────────────────
    def analyze_motion(self, frames):
        """
        Average % of pixels that changed between consecutive frames.
        Live people always have micro-motion; static spoofs are ~0.
        """
        if len(frames) < 2:
            return 0.0

        scores = []
        for i in range(1, len(frames)):
            g1 = cv2.cvtColor(frames[i - 1], cv2.COLOR_BGR2GRAY)
            g2 = cv2.cvtColor(frames[i],     cv2.COLOR_BGR2GRAY)
            diff = cv2.absdiff(g1, g2)
            _, thresh = cv2.threshold(diff, 15, 255, cv2.THRESH_BINARY)
            changed = np.count_nonzero(thresh)
            scores.append((changed / thresh.size) * 100)

        return float(np.mean(scores)) if scores else 0.0

    # ──────────────────────────────────────────────
    #  MAIN LIVENESS VERIFICATION
    # ──────────────────────────────────────────────
    def verify_liveness(self, frames):
        """
        Returns: (is_live: bool, confidence: float, details: dict)

        Decision logic:
          is_live = is_textured AND (has_blinked OR is_moving)

          Texture filters out blurry photos/screens.
          Blink OR motion catches biological activity without being too strict
          for short clips where only one signal may be present.

        Confidence is built from three independent components that sum to 1.0:
          - texture:  up to 0.30  (saturates at TEXTURE_THRESH * 5)
          - blink:    0.40        (binary, strong signal)
          - motion:   up to 0.30  (saturates at 1% changed pixels)
        Rejected results are capped at 0.45 so callers can threshold cleanly.
        """
        try:
            if not frames:
                return False, 0.0, {"error": "No frames provided", "face_detected": False}

            # Texture on middle frame (most stable; avoids motion blur at edges)
            mid_idx = len(frames) // 2
            texture_score = self.analyze_texture(frames[mid_idx])
            is_textured = texture_score >= self.TEXTURE_THRESH

            motion_score = self.analyze_motion(frames)
            is_moving = motion_score >= self.MOTION_THRESH

            has_blinked, min_ear, face_detected = self.detect_blink(frames)

            if not face_detected:
                print("❌ Anti-Spoofing: No face detected in frames")
                return False, 0.0, {
                    "face_detected":   False,
                    "texture_score":   texture_score,
                    "motion_score":    motion_score,
                    "blink_detected":  False,
                    "min_ear":         1.0,
                    "is_textured":     is_textured,
                    "is_moving":       is_moving,
                    "reason":          "No face detected"
                }

            is_live = bool(is_textured and (has_blinked or is_moving))

            # ── CONFIDENCE (three components, each independently bounded) ──
            # texture:  0–0.30, saturates at TEXTURE_THRESH * 5 (= 50 variance)
            texture_conf = min(texture_score / (self.TEXTURE_THRESH * 5), 1.0) * 0.30
            # blink:    binary 0 or 0.40
            blink_conf   = 0.40 if has_blinked else 0.0
            # motion:   0–0.30, saturates at 1% pixel change
            motion_conf  = min(motion_score / 1.0, 1.0) * 0.30

            conf = texture_conf + blink_conf + motion_conf
            if not is_live:
                conf = min(conf, 0.45)
            conf = round(max(0.0, min(1.0, conf)), 3)

            reason_parts = []
            if not is_textured:
                reason_parts.append("low texture (possible photo/screen)")
            if not has_blinked and not is_moving:
                reason_parts.append("no motion or blink detected")
            reason = "; ".join(reason_parts) if reason_parts else "all checks passed"

            print(
                f"→ Liveness | Texture: {texture_score:.1f} ({'✓' if is_textured else '✗'}) | "
                f"Motion: {motion_score:.3f}% ({'✓' if is_moving else '✗'}) | "
                f"Blink: {'✓' if has_blinked else '✗'} (min EAR={min_ear:.3f}) | "
                f"Face: {'✓' if face_detected else '✗'} | "
                f"Result: {'LIVE ✅' if is_live else 'SPOOF ❌'} ({conf:.3f})"
            )

            return is_live, conf, {
                "face_detected":   bool(face_detected),
                "texture_score":   round(texture_score, 2),
                "motion_score":    round(motion_score, 4),
                "blink_detected":  bool(has_blinked),
                "min_ear":         round(min_ear, 4),
                "is_textured":     bool(is_textured),
                "is_moving":       bool(is_moving),
                "reason":          reason,
                "frames_analyzed": len(frames),
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            return False, 0.0, {"error": str(e), "face_detected": False}