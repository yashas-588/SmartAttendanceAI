from flask import Blueprint, jsonify, request
import base64
import numpy as np
import cv2
from antispoofing.inference.spoof_engine import SpoofEngine

antispoof_bp = Blueprint("antispoof", __name__)

# Initialize engine globally so models stay loaded in memory
try:
    spoof_engine = SpoofEngine()
except Exception as e:
    print("Error initializing SpoofEngine:", e)
    spoof_engine = None

def decode_base64_image(base64_string):
    if not base64_string or len(base64_string) < 20:
        return None
    if "," in base64_string:
        base64_string = base64_string.split(',')[1]
    if not base64_string:
        return None
    img_data = base64.b64decode(base64_string)
    np_arr = np.frombuffer(img_data, np.uint8)
    if np_arr.size == 0:
        return None
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    return img

@antispoof_bp.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok" if spoof_engine else "error",
        "detectors": [
            "texture",
            "blink",
            "motion",
            "mps_deep_features"
        ]
    })

@antispoof_bp.route("/api/verify-liveness", methods=["POST"])
def verify_liveness():
    if not spoof_engine:
        return jsonify({"error": "Anti-spoofing engine not initialized"}), 500

    data = request.json
    if not data or 'frames' not in data:
        return jsonify({"error": "No frames provided"}), 400

    base64_frames = data['frames']
    
    if not isinstance(base64_frames, list) or len(base64_frames) == 0:
        return jsonify({"error": "Frames must be a non-empty list of base64 strings"}), 400

    try:
        decoded_frames = [decode_base64_image(f) for f in base64_frames]
        decoded_frames = [f for f in decoded_frames if f is not None]
        
        if len(decoded_frames) == 0:
            return jsonify({
                "is_spoof": True,
                "is_live": False,
                "confidence": 0.0,
                "message": "Camera starting up (empty frame).",
                "details": {}
            })
        
        is_live, confidence, details = spoof_engine.verify_liveness(decoded_frames)

        return jsonify({
            "is_spoof": not is_live,
            "is_live": is_live,
            "confidence": confidence,
            "message": "Liveness verified" if is_live else "Spoof detected!",
            "details": details
        })
    except Exception as e:
        print("Error during liveness verification:", e)
        return jsonify({"error": str(e)}), 500

def register_antispoofing_routes(app):
    app.register_blueprint(antispoof_bp)
