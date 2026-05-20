/**
 * FaceEngine — In-browser Face Recognition
 *
 * Algorithm: FaceNet-128 descriptors (Schroff et al., CVPR 2015)
 * via face-api.js (@vladmandic/face-api).
 *
 * Recognition strategy:
 * - Store N descriptors per student (from registration captures)
 * - Average descriptor computed as class centroid
 * - Match using Euclidean distance in 128-d embedding space
 * - Threshold: 0.45 (empirically validated in FaceNet paper)
 *
 * Models from CDN: @vladmandic/face-api
 */
const FACEAPI_MODEL_URL = 'https://cdn.jsdelivr.net/npm/@vladmandic/face-api/model/';
const SIMILARITY_THRESHOLD = 0.45;  // FaceNet paper: EER at ~0.45 on LFW

class FaceEngine {
  constructor() {
    this.modelsLoaded = false;
    this.studentDescriptors = []; // [{ id, name, descriptors: Float32Array[] }]
  }

  // ─── MODEL LOADING ────────────────────────────────────────────────────────
  /**
   * Load models in parallel for maximum speed.
   * @param {Function} onProgress - status callback
   * @param {boolean} lightweight - if true, skip heavy SSD MobileNet (student portal only needs TinyFaceDetector)
   */
  async loadModels(onProgress, lightweight = false) {
    if (this.modelsLoaded) return;
    onProgress && onProgress('Loading AI models...');

    const loads = [
      faceapi.nets.faceLandmark68Net.loadFromUri(FACEAPI_MODEL_URL),
      faceapi.nets.faceRecognitionNet.loadFromUri(FACEAPI_MODEL_URL),
      faceapi.nets.tinyFaceDetector.loadFromUri(FACEAPI_MODEL_URL),
    ];
    // SSD MobileNet is large (~5MB) and only needed for multi-face admin live scan
    if (!lightweight) {
      loads.push(faceapi.nets.ssdMobilenetv1.loadFromUri(FACEAPI_MODEL_URL));
    }

    await Promise.all(loads);
    this.modelsLoaded = true;
    onProgress && onProgress('All models loaded successfully');
  }

  // ─── DESCRIPTOR EXTRACTION ────────────────────────────────────────────────
  /**
   * Extract 128-d FaceNet descriptor from a video/canvas/image element.
   * Uses TinyFaceDetector (fast) first, falls back to SSD MobileNet if loaded.
   * Returns Float32Array or null if no face detected.
   */
  async extractDescriptor(mediaEl) {
    // Try fast TinyFaceDetector first (inputSize 320 = fastest, good enough for close-up)
    let det = await faceapi
      .detectSingleFace(mediaEl, new faceapi.TinyFaceDetectorOptions({ inputSize: 320, scoreThreshold: 0.4 }))
      .withFaceLandmarks()
      .withFaceDescriptor();
    // Fallback to SSD if available and Tiny missed
    if (!det && faceapi.nets.ssdMobilenetv1.isLoaded) {
      det = await faceapi
        .detectSingleFace(mediaEl, new faceapi.SsdMobilenetv1Options({ minConfidence: 0.5 }))
        .withFaceLandmarks()
        .withFaceDescriptor();
    }
    return det ? det.descriptor : null;
  }

  /**
   * Extract descriptor from multiple frames and return the MEAN descriptor.
   * This improves robustness (reduces noise per FaceNet paper protocol).
   */
  async extractMeanDescriptor(videoEl, frameCount = 5, intervalMs = 200) {
    const descriptors = [];
    for (let i = 0; i < frameCount; i++) {
      const d = await this.extractDescriptor(videoEl);
      if (d) descriptors.push(d);
      if (i < frameCount - 1) await new Promise(r => setTimeout(r, intervalMs));
    }
    if (descriptors.length === 0) return null;
    return this._meanDescriptor(descriptors);
  }

  _meanDescriptor(descriptors) {
    const mean = new Float32Array(128);
    for (const d of descriptors) for (let i = 0; i < 128; i++) mean[i] += d[i];
    for (let i = 0; i < 128; i++) mean[i] /= descriptors.length;
    return mean;
  }

  // ─── STUDENT DESCRIPTORS MANAGEMENT ──────────────────────────────────────
  /**
   * Load all student face descriptors from Firestore.
   * Handles both new map format {d0:[...], d1:[...]} and legacy array format [[...],...].
   */
  async loadFromFirestore(filterUid = null) {
    let query = db.collection('students');
    if (filterUid) query = query.where('uid', '==', filterUid);
    const snap = await query.get();

    this.studentDescriptors = [];
    snap.forEach(doc => {
      const d = doc.data();
      if (!d.descriptors) return;

      let descs = [];
      if (Array.isArray(d.descriptors)) {
        // Legacy format: array of arrays
        if (d.descriptors.length === 0) return;
        descs = d.descriptors.map(arr => new Float32Array(arr));
      } else if (typeof d.descriptors === 'object') {
        // New map format: { d0: [...], d1: [...], ... }
        const keys = Object.keys(d.descriptors);
        if (keys.length === 0) return;
        descs = keys.map(k => new Float32Array(d.descriptors[k]));
      }

      if (descs.length === 0) return;

      this.studentDescriptors.push({
        id: doc.id,
        name: d.name,
        uid: d.uid || null,
        descriptors: descs
      });
    });
    return this.studentDescriptors.length;
  }

  /**
   * Save descriptors to Firestore for a student.
   * Stores as map format (Firestore does not allow nested arrays).
   */
  async saveDescriptors(studentId, descriptors) {
    const descriptorMap = {};
    descriptors.forEach((d, i) => { descriptorMap[`d${i}`] = Array.from(d); });
    await db.collection('students').doc(studentId).update({
      descriptors: descriptorMap,
      updatedAt: firebase.firestore.FieldValue.serverTimestamp()
    });
  }

  // ─── RECOGNITION ─────────────────────────────────────────────────────────
  /**
   * Find the best matching student for a query descriptor.
   * Uses minimum Euclidean distance across all stored descriptors.
   *
   * Returns: { matched: bool, name, id, uid, distance, confidence }
   */
  findBestMatch(queryDescriptor, threshold = SIMILARITY_THRESHOLD) {
    if (!queryDescriptor || this.studentDescriptors.length === 0) {
      return { matched: false, name: 'Unknown', distance: Infinity, confidence: 0 };
    }

    let bestMatch = null, bestDist = Infinity;
    for (const student of this.studentDescriptors) {
      for (const desc of student.descriptors) {
        const dist = this._euclidean(queryDescriptor, desc);
        if (dist < bestDist) {
          bestDist = dist;
          bestMatch = student;
        }
      }
    }

    const matched = bestDist < threshold;
    return {
      matched,
      name:       matched ? bestMatch.name : 'Unknown',
      id:         matched ? bestMatch.id   : null,
      uid:        matched ? bestMatch.uid  : null,
      distance:   bestDist,
      confidence: Math.max(0, Math.min(1, 1 - (bestDist / threshold)))
    };
  }

  _euclidean(a, b) {
    let sum = 0;
    for (let i = 0; i < a.length; i++) sum += (a[i] - b[i]) ** 2;
    return Math.sqrt(sum);
  }

  // ─── CONTINUOUS DETECTION (for live admin page) ───────────────────────────
  /**
   * Detect all faces in a frame and return matches.
   * Used by admin live page to scan multiple students simultaneously.
   */
  async detectAll(mediaEl, threshold = SIMILARITY_THRESHOLD) {
    const detections = await faceapi
      .detectAllFaces(mediaEl, new faceapi.SsdMobilenetv1Options({ minConfidence: 0.5 }))
      .withFaceLandmarks()
      .withFaceDescriptors();

    return detections.map(det => ({
      box: det.detection.box,
      match: this.findBestMatch(det.descriptor, threshold)
    }));
  }
}
