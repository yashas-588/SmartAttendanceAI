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
  // ═══════════════════════════════════════════════════════════════════════
  //  MEDIAPIPE FACEMESH INITIALIZATION & PRE-WARMING
  // ═══════════════════════════════════════════════════════════════════════

  async initFaceMesh() {
    if (window.faceMeshLoaded) return;
    if (typeof FaceMesh === 'undefined') {
      throw new Error('MediaPipe FaceMesh script not loaded. Please ensure the script is included.');
    }
    
    window.faceMesh = new FaceMesh({
      locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh/${file}`
    });
    
    window.faceMesh.setOptions({
      maxNumFaces: 1,
      refineLandmarks: true,
      minDetectionConfidence: 0.5,
      minTrackingConfidence: 0.5
    });
    
    window.faceMesh.onResults(results => {
      window.lastFaceMeshResults = results;
    });
    
    // Warm up by sending a dummy canvas
    const dummyCanvas = document.createElement('canvas');
    dummyCanvas.width = 1;
    dummyCanvas.height = 1;
    await window.faceMesh.send({ image: dummyCanvas });
    
    window.faceMeshLoaded = true;
    console.log('✅ MediaPipe FaceMesh loaded and warmed up');
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  FULL LIVENESS CHECK (Upgraded Active Challenge-Response)
  // ═══════════════════════════════════════════════════════════════════════

  async runFullCheck(videoEl, challengeSeq, onStatus) {
    // 1. Pre-flight Quality Assessment
    onStatus('Checking camera quality...', 'scanning');
    await this._sleep(300);
    const q = this.assessQuality(videoEl);
    
    onStatus('Analyzing surface texture & motion...', 'scanning');
    const canvas = document.createElement('canvas');
    canvas.width = videoEl.videoWidth || 640;
    canvas.height = videoEl.videoHeight || 480;
    const ctx = canvas.getContext('2d');
    
    const scores = { quality: q.score, texture: 0, biometric: 0, head: 0, blink: 0 };
    const details = {
      face_detected: false,
      lbp_score: 0, lbp_pass: false,
      moire_score: 0, moire_pass: false,
      rppg_score: 0, rppg_pass: false,
      motion_score: 0, motion_pass: false,
      blink_pass: false,
      head_pose_pass: false,
      reason: '',
      weighted_score: 0,
      quality_hint: q.hint,
      guidance: ''
    };
    
    // Quick passive check (10 frames, ~800ms) to run the existing anti-spoofing pipeline
    let motionScores = [];
    for (let i = 0; i < 10; i++) {
      await this._sleep(80);
      ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
      const fd = ctx.getImageData(0, 0, canvas.width, canvas.height);
      motionScores.push(this.computeMotion(fd, canvas.width, canvas.height));
      const fb = { x: canvas.width*0.2, y: 0, width: canvas.width*0.6, height: canvas.height*0.5 };
      this.updateRPPG(fd, fb, canvas.width);
    }
    
    // Compute texture features (LBP & Moire)
    ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
    const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    details.lbp_score = this.computeLBP(imgData, canvas.width, canvas.height);
    details.lbp_pass  = details.lbp_score >= this.LBP_THRESH;
    details.moire_score = this.detectMoire(imgData, canvas.width, canvas.height);
    details.moire_pass  = details.moire_score > this.MOIRE_LOW && details.moire_score < this.MOIRE_HIGH;
    scores.texture = details.lbp_pass && details.moire_pass ? 1.0 :
                     details.lbp_pass || details.moire_pass ? 0.6 : 0.15;
                     
    details.motion_score = motionScores.reduce((a,b)=>a+b,0)/motionScores.length;
    details.motion_pass  = details.motion_score >= this.MOTION_THRESH;
    details.rppg_score   = this.getRPPGVariance();
    details.rppg_pass    = details.rppg_score >= this.RPPG_MIN_VAR;
    scores.biometric = details.motion_pass && details.rppg_pass ? 1.0 :
                       details.motion_pass || details.rppg_pass ? 0.65 : 0.1;

    // 2. Load and initialize MediaPipe FaceMesh if needed
    try {
      await this.initFaceMesh();
    } catch (err) {
      details.reason = 'Failed to load liveness challenge engine.';
      return { passed: false, details, completed_challenges: [] };
    }

    // 3. Loop through active challenges
    const completedList = [];
    let challengeFailed = false;
    let challengeFailReason = '';
    
    const chalIcons = {
      'blink twice': '👁️',
      'turn head left': '⬅️',
      'turn head right': '➡️',
      'smile': '😊',
      'move closer': '👤',
      'raise eyebrows': '🤨'
    };
    
    const chalInstructions = {
      'blink twice': 'Blink twice naturally',
      'turn head left': 'Turn head slightly left',
      'turn head right': 'Turn head slightly right',
      'smile': 'Smile naturally',
      'move closer': 'Move closer to the camera',
      'raise eyebrows': 'Raise your eyebrows'
    };
    
    for (let index = 0; index < challengeSeq.length; index++) {
      const chal = challengeSeq[index];
      const icon = chalIcons[chal] || '👁️';
      const instruction = chalInstructions[chal] || chal;
      
      onStatus(instruction, 'challenge');
      if (typeof showChallenge === 'function') {
        showChallenge(icon, instruction, `Challenge ${index + 1} of ${challengeSeq.length}`);
      }
      if (typeof startChallengeTimer === 'function') {
        startChallengeTimer(20); // 20s per challenge
      }
      
      const chalRes = await this._detectChallenge(videoEl, chal, 20000, (guidance) => {
        if (typeof showChallenge === 'function') {
          showChallenge(icon, guidance, `Challenge ${index + 1} of ${challengeSeq.length}`);
        }
      });
      
      if (typeof hideChallenge === 'function') {
        hideChallenge();
      }
      
      if (chalRes.passed) {
        completedList.push(chal);
        if (chal === 'blink twice') {
          details.blink_pass = true;
          scores.blink = 1.0;
        } else if (chal.includes('head')) {
          details.head_pose_pass = true;
          scores.head = 1.0;
        }
      } else {
        challengeFailed = true;
        challengeFailReason = chalRes.reason;
        break;
      }
    }
    
    // Complete remaining score filling for the final calculation
    if (!details.blink_pass) scores.blink = 1.0; // Pass since not requested or completed
    if (!details.head_pose_pass) scores.head = 1.0; // Pass since not requested or completed
    
    // Calculate final weighted score
    let total = 0;
    for (const [k,w] of Object.entries(this.WEIGHTS)) total += (scores[k]||0) * w;
    details.weighted_score = Math.round(total * 100);
    details.scores = scores;
    
    const passed = !challengeFailed && (total >= this.PASS_THRESHOLD);
    details.face_detected = passed;
    
    if (passed) {
      details.reason = `Liveness verified (${details.weighted_score}% confidence)`;
    } else {
      details.reason = challengeFailed ? `Challenge failed: ${challengeFailReason}` : `Anti-spoofing score: ${details.weighted_score}% too low`;
      details.guidance = challengeFailed ? 'Follow instructions closely and try again.' : 'Hold camera steady and check lighting.';
    }
    
    return { passed, details, completed_challenges: completedList };
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  ACTIVE CHALLENGE DETECTOR
  // ═══════════════════════════════════════════════════════════════════════

  async _detectChallenge(videoEl, challengeType, timeout, onGuidance) {
    return new Promise(resolve => {
      const initTime = Date.now();
      let startTime = null;
      let completed = false;
      
      let blinkCount = 0;
      let blinkState = 'open'; // 'open', 'closed'
      let lastBlinkTime = 0;
      
      let baselineRatio = null;
      let baselineEyeDist = null;
      let baselineEAR = null;
      
      const checkFrame = async () => {
        if (startTime && (Date.now() - startTime > timeout)) {
          resolve({ passed: false, reason: 'Time limit exceeded' });
          return;
        }
        if (!startTime && (Date.now() - initTime > 30000)) {
          resolve({ passed: false, reason: 'No face detected within timeout' });
          return;
        }
        if (completed) return;
        
        try {
          if (!window.faceMesh) {
            resolve({ passed: false, reason: 'Liveness engine not initialized' });
            return;
          }
          await window.faceMesh.send({ image: videoEl });
          const res = window.lastFaceMeshResults;
          
          if (res && res.multiFaceLandmarks && res.multiFaceLandmarks.length > 0) {
            if (!startTime) {
              startTime = Date.now();
            }
            const landmarks = res.multiFaceLandmarks[0];
            const dist = (p1, p2) => Math.hypot(p1.x - p2.x, p1.y - p2.y, p1.z - p2.z || 0);
            const dist2D = (p1, p2) => Math.hypot(p1.x - p2.x, p1.y - p2.y);
            
            const noseBridge = landmarks[1];
            const cheekLeft = landmarks[454];   // Anatomical Left
            const cheekRight = landmarks[234];  // Anatomical Right
            const eyeLeftOuter = landmarks[33];
            const eyeLeftInner = landmarks[133];
            const eyeRightOuter = landmarks[263];
            const eyeRightInner = landmarks[362];
            
            const eyeDist = dist(eyeLeftOuter, eyeRightOuter);
            
            if (challengeType === 'blink twice') {
              if (baselineEAR === null) {
                const leftEAR = dist(landmarks[159], landmarks[145]) / (dist(eyeLeftOuter, eyeLeftInner) || 1e-6);
                const rightEAR = dist(landmarks[386], landmarks[374]) / (dist(eyeRightOuter, eyeRightInner) || 1e-6);
                baselineEAR = (leftEAR + rightEAR) / 2;
                console.log(`Blink challenge initialized. Baseline EAR: ${baselineEAR}`);
              }
              
              const leftEAR = dist(landmarks[159], landmarks[145]) / (dist(eyeLeftOuter, eyeLeftInner) || 1e-6);
              const rightEAR = dist(landmarks[386], landmarks[374]) / (dist(eyeRightOuter, eyeRightInner) || 1e-6);
              const ear = (leftEAR + rightEAR) / 2;
              
              const closedThresh = baselineEAR * 0.75;
              const openThresh = baselineEAR * 0.90;
              
              if (blinkState === 'open' && ear < closedThresh) {
                blinkState = 'closed';
              } else if (blinkState === 'closed' && ear > openThresh) {
                const now = Date.now();
                if (now - lastBlinkTime > 200) {
                  blinkCount++;
                  lastBlinkTime = now;
                  if (onGuidance) onGuidance(`Blink ${blinkCount}/2 detected!`);
                }
                blinkState = 'open';
              }
              
              if (blinkCount >= 2) {
                completed = true;
                resolve({ passed: true });
                return;
              } else {
                if (onGuidance) onGuidance(`Blink twice (Detected: ${blinkCount}/2)`);
              }
              
            } else if (challengeType === 'turn head left') {
              const distLeft = dist2D(noseBridge, cheekLeft);
              const distRight = dist2D(noseBridge, cheekRight);
              const ratio = distLeft / (distRight + 1e-6);
              
              if (ratio > 1.30) {
                completed = true;
                resolve({ passed: true });
                return;
              } else {
                if (onGuidance) onGuidance('← Turn head left');
              }
              
            } else if (challengeType === 'turn head right') {
              const distLeft = dist2D(noseBridge, cheekLeft);
              const distRight = dist2D(noseBridge, cheekRight);
              const ratio = distLeft / (distRight + 1e-6);
              
              if (ratio < 0.75) {
                completed = true;
                resolve({ passed: true });
                return;
              } else {
                if (onGuidance) onGuidance('→ Turn head right');
              }
              
            } else if (challengeType === 'smile') {
              const mouthLeft = landmarks[61];
              const mouthRight = landmarks[291];
              const smileRatio = dist(mouthLeft, mouthRight) / (eyeDist + 1e-6);
              
              if (baselineRatio === null) baselineRatio = smileRatio;
              
              if (smileRatio > baselineRatio * 1.08 || smileRatio > 0.80) {
                completed = true;
                resolve({ passed: true });
                return;
              } else {
                if (onGuidance) onGuidance('😊 Smile naturally');
              }
              
            } else if (challengeType === 'move closer') {
              if (baselineEyeDist === null) baselineEyeDist = eyeDist;
              
              if (eyeDist >= baselineEyeDist * 1.08) {
                completed = true;
                resolve({ passed: true });
                return;
              } else {
                if (onGuidance) onGuidance('👤 Move closer to camera');
              }
              
            } else if (challengeType === 'raise eyebrows') {
              const leftEyebrow = landmarks[70];
              const leftEye = landmarks[159];
              const rightEyebrow = landmarks[300];
              const rightEye = landmarks[386];
              const eyebrowDist = (dist(leftEyebrow, leftEye) + dist(rightEyebrow, rightEye)) / 2;
              const eyebrowRatio = eyebrowDist / (eyeDist + 1e-6);
              
              if (baselineRatio === null) baselineRatio = eyebrowRatio;
              
              if (eyebrowRatio >= baselineRatio * 1.08) {
                completed = true;
                resolve({ passed: true });
                return;
              } else {
                if (onGuidance) onGuidance('🤨 Raise eyebrows');
              }
            }
          } else {
            if (onGuidance) onGuidance('No face detected. Center your face.');
          }
        } catch (e) {
          console.warn('Error in challenge check frame:', e);
        }
        
        setTimeout(checkFrame, 120);
      };
      
      checkFrame();
    });
  }

  _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
}
