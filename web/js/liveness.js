/**
 * LivenessEngine v2.0 — Human-Friendly Multi-layer Anti-Spoofing
 *
 * v2 improvements:
 *  - Weighted confidence scoring (no hard-fail gates)
 *  - Relaxed thresholds for glasses, low light, blur
 *  - Pre-flight quality assessment with guidance
 *  - Rolling-average head pose for stability
 *  - Glass-aware blink detection
 *  - Rich UI callbacks for real-time guidance
 *  - Partial credit on timeout (no wasted attempts)
 *
 * Algorithms (IEEE-referenced):
 *  1. LBP Texture — IEEE TIFS 2011
 *  2. rPPG Signal — IEEE Trans. Biomed. Eng. 2013
 *  3. EAR Blink   — Soukupová & Čech, EECV 2016
 *  4. Optical Flow — temporal consistency
 *  5. Laplacian   — blur/sharpness detection
 *  6. Weighted Fusion — multi-modal soft decision
 */
class LivenessEngine {
  constructor(opts = {}) {
    // ── Thresholds (tuned for real-world: glasses, uneven light) ──
    this.EAR_THRESH      = opts.earThresh    || 0.26;   // ↑ from 0.22
    this.LBP_THRESH      = opts.lbpThresh    || 22;     // ↓ from 30
    this.MOTION_THRESH   = opts.motionThresh || 0.02;   // ↓ from 0.04
    this.RPPG_FRAMES     = opts.rppgFrames   || 30;
    this.RPPG_MIN_VAR    = 0.15;                        // ↓ from 0.3
    this.HEAD_YAW_THRESH = opts.headYaw      || 0.08;   // ↓ from 0.15
    this.MOIRE_LOW       = 30;
    this.MOIRE_HIGH      = 5000;
    this.TIMEOUT_MS      = opts.timeout      || 10000;  // ↑ from 6000

    // ── Weighted scoring ──
    this.WEIGHTS = { quality: 0.20, texture: 0.10, biometric: 0.10, head: 0.25, blink: 0.35 };
    this.PASS_THRESHOLD  = 0.45;

    // ── State ──
    this.rppgBuffer   = [];
    this.prevGrayData = null;
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  PRE-FLIGHT QUALITY
  // ═══════════════════════════════════════════════════════════════════════

  assessQuality(videoEl) {
    const c = document.createElement('canvas');
    const w = videoEl.videoWidth || 640, h = videoEl.videoHeight || 480;
    c.width = w; c.height = h;
    const ctx = c.getContext('2d');
    ctx.drawImage(videoEl, 0, 0, w, h);
    const img = ctx.getImageData(0, 0, w, h);

    const brightness = this._meanBrightness(img);
    const sharpness  = this._laplacianVar(img, w, h);
    const bOk = brightness > 35 && brightness < 230;
    const sOk = sharpness > 80;

    let hint = '';
    if (brightness < 35)      hint = 'Too dark — move to better lighting';
    else if (brightness > 230) hint = 'Too bright — reduce glare';
    else if (!sOk)             hint = 'Blurry — hold device steady';
    else                       hint = 'Quality OK';

    const score = (bOk ? 0.5 : brightness > 20 ? 0.25 : 0) +
                  (sOk ? 0.5 : sharpness > 40 ? 0.25 : 0);

    return { brightness: Math.round(brightness), sharpness: Math.round(sharpness),
             brightnessOk: bOk, sharpnessOk: sOk, overall: bOk && sOk, score, hint };
  }

  _meanBrightness(img) {
    const d = img.data; let s = 0, n = 0;
    for (let i = 0; i < d.length; i += 16) { s += 0.299*d[i]+0.587*d[i+1]+0.114*d[i+2]; n++; }
    return s / n;
  }

  _laplacianVar(img, w, h) {
    const d = img.data;
    const gray = new Float32Array(w * h);
    for (let i = 0; i < gray.length; i++) gray[i] = 0.299*d[i*4]+0.587*d[i*4+1]+0.114*d[i*4+2];
    let sum = 0, sq = 0, n = 0;
    for (let y = 1; y < h-1; y += 2) {
      for (let x = 1; x < w-1; x += 2) {
        const lap = gray[(y-1)*w+x]+gray[(y+1)*w+x]+gray[y*w+x-1]+gray[y*w+x+1]-4*gray[y*w+x];
        sum += lap; sq += lap*lap; n++;
      }
    }
    return n ? (sq/n)-(sum/n)**2 : 0;
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  LBP TEXTURE (IEEE TIFS 2011)
  // ═══════════════════════════════════════════════════════════════════════

  computeLBP(imageData, width, height) {
    const gray = new Float32Array(width * height);
    const d = imageData.data;
    for (let i = 0; i < gray.length; i++) gray[i] = 0.299*d[i*4]+0.587*d[i*4+1]+0.114*d[i*4+2];
    const dx = [-1,-1,0,1,1,1,0,-1], dy = [0,-1,-1,-1,0,1,1,1];
    let uniform = 0, total = 0;
    for (let y = 1; y < height-1; y++) {
      for (let x = 1; x < width-1; x++) {
        const c = gray[y*width+x]; let code = 0;
        for (let k = 0; k < 8; k++) if (gray[(y+dy[k])*width+x+dx[k]] >= c) code |= (1<<k);
        if (this._bitTransitions(code) <= 2) uniform++;
        total++;
      }
    }
    return (uniform / Math.max(total,1)) * 100;
  }

  _bitTransitions(code) {
    let c = 0;
    for (let i = 0; i < 8; i++) { if (((code>>i)&1) !== ((code>>((i+1)%8))&1)) c++; }
    return c;
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  rPPG (IEEE Trans. Biomed. Eng. 2013)
  // ═══════════════════════════════════════════════════════════════════════

  updateRPPG(imageData, faceBox, width) {
    const fx = Math.floor(faceBox.x), fy = Math.floor(faceBox.y);
    const fw = Math.floor(faceBox.width), fh = Math.floor(faceBox.height);
    const rh = Math.floor(fh * 0.25);
    let gSum = 0, count = 0;
    const d = imageData.data;
    for (let y = fy; y < fy+rh && y < imageData.height; y++) {
      for (let x = fx; x < fx+fw && x < width; x++) {
        gSum += d[(y*width+x)*4+1]; count++;
      }
    }
    if (!count) return;
    this.rppgBuffer.push(gSum/count);
    if (this.rppgBuffer.length > this.RPPG_FRAMES) this.rppgBuffer.shift();
  }

  getRPPGVariance() {
    if (this.rppgBuffer.length < 8) return 0;
    const m = this.rppgBuffer.reduce((a,b)=>a+b,0)/this.rppgBuffer.length;
    return Math.sqrt(this.rppgBuffer.reduce((s,v)=>s+(v-m)**2,0)/this.rppgBuffer.length);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  EAR BLINK (Soukupová & Čech 2016)
  // ═══════════════════════════════════════════════════════════════════════

  computeEAR(landmarks, isLeft = true) {
    const idx = isLeft ? [36,37,38,39,40,41] : [42,43,44,45,46,47];
    const p = idx.map(i => landmarks[i]);
    const dist = (a,b) => Math.hypot(a.x-b.x, a.y-b.y);
    const A = dist(p[1],p[5]), B = dist(p[2],p[4]), C = dist(p[0],p[3]);
    return C === 0 ? 0.35 : (A+B)/(2*C);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  MOTION
  // ═══════════════════════════════════════════════════════════════════════

  computeMotion(currentImageData, width, height) {
    const d = currentImageData.data;
    const grayNew = new Uint8Array(width * height);
    for (let i = 0; i < grayNew.length; i++) grayNew[i] = 0.299*d[i*4]+0.587*d[i*4+1]+0.114*d[i*4+2];
    if (!this.prevGrayData) { this.prevGrayData = grayNew; return 0; }
    let changed = 0;
    for (let i = 0; i < grayNew.length; i++) if (Math.abs(grayNew[i]-this.prevGrayData[i]) > 12) changed++;
    this.prevGrayData = grayNew;
    return (changed/grayNew.length)*100;
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  HEAD POSE
  // ═══════════════════════════════════════════════════════════════════════

  computeHeadPose(landmarks) {
    const nose = landmarks[30], le = landmarks[36], re = landmarks[45];
    const lm = landmarks[48], rm = landmarks[54];
    const ec = { x:(le.x+re.x)/2, y:(le.y+re.y)/2 };
    const mc = { x:(lm.x+rm.x)/2, y:(lm.y+rm.y)/2 };
    const fc = { x:(ec.x+mc.x)/2, y:(ec.y+mc.y)/2 };
    const eyeW = re.x - le.x;
    if (eyeW === 0) return { yaw: 0, pitch: 0 };
    return { yaw: (nose.x-fc.x)/eyeW, pitch: (nose.y-fc.y)/(mc.y-ec.y||1) };
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  MOIRÉ / SCREEN DETECTION
  // ═══════════════════════════════════════════════════════════════════════

  detectMoire(imageData, width, height) {
    const gray = new Float32Array(width * height);
    const d = imageData.data;
    for (let i = 0; i < gray.length; i++) gray[i] = 0.299*d[i*4]+0.587*d[i*4+1]+0.114*d[i*4+2];
    let sum = 0, sq = 0, n = 0;
    for (let y = 1; y < height-1; y++) {
      for (let x = 1; x < width-1; x++) {
        const lap = gray[(y-1)*width+x]+gray[(y+1)*width+x]+gray[y*width+x-1]+gray[y*width+x+1]-4*gray[y*width+x];
        sum += lap; sq += lap*lap; n++;
      }
    }
    const mean = sum/n;
    return (sq/n)-(mean*mean);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  FULL LIVENESS CHECK (v2 — weighted scoring)
  // ═══════════════════════════════════════════════════════════════════════

  async runFullCheck(videoEl, onStatus, blinkTimeout) {
    const timeout = blinkTimeout || this.TIMEOUT_MS;
    const canvas = document.createElement('canvas');
    canvas.width = videoEl.videoWidth || 640;
    canvas.height = videoEl.videoHeight || 480;
    const ctx = canvas.getContext('2d');

    const scores = { quality: 0, texture: 0, biometric: 0, head: 0, blink: 0 };
    const details = {
      face_detected: false,
      lbp_score: 0, lbp_pass: false,
      moire_score: 0, moire_pass: false,
      rppg_score: 0, rppg_pass: false,
      motion_score: 0, motion_pass: false,
      blink_pass: false,
      head_pose_pass: false,
      reason: '',
      // v2 additions
      weighted_score: 0,
      quality_hint: '',
      guidance: ''
    };

    // ── PHASE 0: Pre-flight Quality ──────────────────────────────────
    onStatus('Checking camera quality...', 'scanning');
    await this._sleep(300);
    const q = this.assessQuality(videoEl);
    scores.quality = q.score;
    details.quality_hint = q.hint;

    if (!q.overall) {
      onStatus(q.hint, 'warning');
      await this._sleep(1500); // Give user time to adjust
      // Re-check once
      const q2 = this.assessQuality(videoEl);
      scores.quality = q2.score;
      details.quality_hint = q2.hint;
    }

    // ── PHASE 1: Texture + Screen ────────────────────────────────────
    onStatus('Analyzing surface texture...', 'scanning');
    ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
    const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);

    details.lbp_score = this.computeLBP(imgData, canvas.width, canvas.height);
    details.lbp_pass  = details.lbp_score >= this.LBP_THRESH;

    details.moire_score = this.detectMoire(imgData, canvas.width, canvas.height);
    details.moire_pass  = details.moire_score > this.MOIRE_LOW && details.moire_score < this.MOIRE_HIGH;

    // Soft score: both pass = 1.0, one pass = 0.6, none = 0.15
    scores.texture = details.lbp_pass && details.moire_pass ? 1.0 :
                     details.lbp_pass || details.moire_pass ? 0.6 : 0.15;

    // ── PHASE 2: Motion + rPPG (~1s) ────────────────────────────────
    onStatus('Detecting micro-motion & pulse...', 'scanning');
    let motionScores = [];
    for (let i = 0; i < 12; i++) {
      await this._sleep(80);
      ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
      const fd = ctx.getImageData(0, 0, canvas.width, canvas.height);
      motionScores.push(this.computeMotion(fd, canvas.width, canvas.height));
      const fb = { x: canvas.width*0.2, y: 0, width: canvas.width*0.6, height: canvas.height*0.5 };
      this.updateRPPG(fd, fb, canvas.width);
    }

    details.motion_score = motionScores.reduce((a,b)=>a+b,0)/motionScores.length;
    details.motion_pass  = details.motion_score >= this.MOTION_THRESH;
    details.rppg_score   = this.getRPPGVariance();
    details.rppg_pass    = details.rppg_score >= this.RPPG_MIN_VAR;

    scores.biometric = details.motion_pass && details.rppg_pass ? 1.0 :
                       details.motion_pass || details.rppg_pass ? 0.65 : 0.1;

    // ── PHASE 3: Head Rotation ───────────────────────────────────────
    onStatus('Turn your head slightly left or right', 'challenge');
    const headResult = await this._runHeadChallenge(videoEl, timeout, (g) => {
      if (g.direction === 'left')  onStatus('← Turn head slightly LEFT', 'challenge');
      else if (g.direction === 'right') onStatus('→ Turn head slightly RIGHT', 'challenge');
      else onStatus('✓ Head movement detected!', 'challenge');
    });
    details.head_pose_pass = headResult.passed;
    scores.head = headResult.score;

    // ── PHASE 4: Blink ───────────────────────────────────────────────
    onStatus('BLINK NOW — Blink your eyes naturally', 'challenge');
    const blinkResult = await this._runBlinkChallenge(videoEl, timeout, (g) => {
      if (g.earRange > 0.03) onStatus('Almost... blink once more', 'challenge');
    });
    details.blink_pass = blinkResult.passed;
    scores.blink = blinkResult.score;

    // ── WEIGHTED DECISION ────────────────────────────────────────────
    let total = 0;
    for (const [k,w] of Object.entries(this.WEIGHTS)) total += (scores[k]||0) * w;
    details.weighted_score = Math.round(total * 100);
    details.scores = scores;

    const passed = total >= this.PASS_THRESHOLD;
    details.face_detected = passed;

    if (passed) {
      details.reason = `Liveness verified (${details.weighted_score}% confidence)`;
    } else {
      // Build human-friendly failure reason
      const weakest = Object.entries(scores).sort((a,b)=>a[1]-b[1])[0];
      const hints = {
        quality: 'Improve lighting and hold camera steady',
        texture: 'Move closer to the camera',
        biometric: 'Stay still and look at the camera',
        head: 'Turn your head slightly left then right',
        blink: 'Look at the camera and blink naturally'
      };
      details.reason = `Score: ${details.weighted_score}% (need ${Math.round(this.PASS_THRESHOLD*100)}%). ${hints[weakest[0]] || 'Try again in better conditions.'}`;
      details.guidance = hints[weakest[0]];
    }

    return { passed, details };
  }

  // ── HEAD CHALLENGE (relaxed, rolling average, partial credit) ──────

  async _runHeadChallenge(videoEl, timeout, onGuidance) {
    return new Promise(resolve => {
      let maxLeft = 0, maxRight = 0;
      const yawHistory = [];
      const start = Date.now();

      const check = async () => {
        if (Date.now() - start > timeout) {
          const range = Math.abs(maxLeft) + Math.abs(maxRight);
          const score = Math.min(1, range / (this.HEAD_YAW_THRESH * 1.5));
          resolve({ passed: score > 0.4, score, maxLeft, maxRight });
          return;
        }

        try {
          const det = await faceapi.detectSingleFace(videoEl,
            new faceapi.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.3 })
          ).withFaceLandmarks();

          if (det) {
            const { yaw } = this.computeHeadPose(det.landmarks.positions);
            yawHistory.push(yaw);
            // Rolling average (3 frames) for stability
            const recent = yawHistory.slice(-3);
            const avg = recent.reduce((a,b)=>a+b,0) / recent.length;

            if (avg < maxLeft)  maxLeft = avg;
            if (avg > maxRight) maxRight = avg;

            const leftOk  = maxLeft < -this.HEAD_YAW_THRESH;
            const rightOk = maxRight > this.HEAD_YAW_THRESH;
            const range   = Math.abs(maxLeft) + Math.abs(maxRight);

            if (onGuidance) onGuidance({
              yaw: avg, leftDone: leftOk, rightDone: rightOk,
              direction: !leftOk ? 'left' : !rightOk ? 'right' : 'done'
            });

            // Pass if either direction done OR total range is enough
            if ((leftOk && rightOk) || range > this.HEAD_YAW_THRESH * 1.5) {
              resolve({ passed: true, score: 1.0, maxLeft, maxRight });
              return;
            }
          }
        } catch(e) {}
        setTimeout(check, 150);
      };
      check();
    });
  }

  // ── BLINK CHALLENGE (glass-aware, partial credit) ──────────────────

  async _runBlinkChallenge(videoEl, timeout, onGuidance) {
    return new Promise(resolve => {
      let minEAR = 1, maxEAR = 0, lowCount = 0, blinkFound = false;
      const start = Date.now();

      const check = async () => {
        if (Date.now() - start > timeout) {
          const range = maxEAR - minEAR;
          const score = Math.min(1, range / 0.08);
          resolve({ passed: score > 0.35, score, minEAR, maxEAR });
          return;
        }
        if (blinkFound) return;

        try {
          const det = await faceapi.detectSingleFace(videoEl,
            new faceapi.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.3 })
          ).withFaceLandmarks();

          if (det) {
            const lm = det.landmarks.positions;
            const ear = (this.computeEAR(lm,true) + this.computeEAR(lm,false)) / 2;

            if (ear < minEAR) minEAR = ear;
            if (ear > maxEAR) maxEAR = ear;

            if (onGuidance) onGuidance({ ear, earRange: maxEAR - minEAR });

            if (ear < this.EAR_THRESH) {
              lowCount++;
            } else {
              if (lowCount >= 1) { // ↓ from 2 (accept faster/partial blinks)
                blinkFound = true;
                resolve({ passed: true, score: 1.0, minEAR, maxEAR });
                return;
              }
              lowCount = 0;
            }
          }
        } catch(e) {}
        setTimeout(check, 100);
      };
      check();
    });
  }

  _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
}
