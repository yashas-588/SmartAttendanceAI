import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import {
  getFirestore,
  collection,
  onSnapshot,
  query,
  orderBy,
  limit
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";

// 🔥 YOUR CONFIG (already correct)
const firebaseConfig = {
  apiKey: "AIzaSyCDBG4wECmxqmrcX5dkhBAA_r6bFTg4KCE",
  authDomain: "smart-attendance-ai-7139f.firebaseapp.com",
  projectId: "smart-attendance-ai-7139f",
  storageBucket: "smart-attendance-ai-7139f.firebasestorage.app",
  messagingSenderId: "452085670379",
  appId: "1:452085670379:web:1b7c62b26133c2c06c79ae",
  measurementId: "G-9NYBZ304CB"
};

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);

const dataDiv = document.getElementById("data");
const video = document.getElementById("webcam");
const canvas = document.getElementById("canvas");
const statusDiv = document.getElementById("status");

// 🔥 REAL-TIME DATA
const q = query(collection(db, "attendance"), orderBy("timestamp", "desc"), limit(20));

onSnapshot(q, (snapshot) => {
  dataDiv.innerHTML = "";

  snapshot.forEach((doc) => {
    const d = doc.data();

    dataDiv.innerHTML += `
      <div class="card">
        <div>
          <h3>${d.name || 'Unknown'}</h3>
          <p>${d.date || ''} ${d.time || ''}</p>
        </div>
        <div style="color: #4ade80;">✅ Logged</div>
      </div>
    `;
  });
});

// Anti-Spoofing & Webcam Logic
const BACKEND_URL = "http://127.0.0.1:5005";
let isProcessing = false;

async function setupCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: 640, height: 480 },
      audio: false
    });
    video.srcObject = stream;
    video.play().catch(e => console.error("Play error:", e));
    
    // Wait for video to be ready
    return new Promise((resolve) => {
      video.onloadedmetadata = () => {
        resolve(video);
      };
      // Fallback for Safari if onloadedmetadata is slow or doesn't fire
      setTimeout(() => resolve(video), 1000);
    });
  } catch (error) {
    setStatus("Camera access denied!", "error");
    console.error("Camera error:", error);
  }
}

function setStatus(text, type) {
  statusDiv.className = `status-box status-${type}`;
  if (type === 'verifying') {
    statusDiv.innerHTML = `<div class="spinner"></div> ${text}`;
  } else if (type === 'success') {
    statusDiv.innerHTML = `✅ ${text}`;
  } else if (type === 'error') {
    statusDiv.innerHTML = `❌ ${text}`;
  } else {
    statusDiv.innerHTML = text;
  }
}

function captureFrame() {
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.7);
}

async function verifyLivenessAndMark() {
  if (isProcessing) return;
  isProcessing = true;

  setStatus("Checking Liveness...", "verifying");

  // Capture a burst of frames for motion/blink detection
  const frames = [];
  for (let i = 0; i < 3; i++) {
    frames.push(captureFrame());
    if (i < 2) await new Promise(r => setTimeout(r, 150)); // 150ms interval
  }

  try {
    // 1. Verify Liveness
    const livenessRes = await fetch(`${BACKEND_URL}/api/verify-liveness`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ frames })
    });
    
    const livenessData = await livenessRes.json();

    if (livenessData.is_live) {
      setStatus("Live Face! Marking Attendance...", "verifying");
      
      // 2. Mark Attendance using the last frame
      const markRes = await fetch(`${BACKEND_URL}/api/mark-attendance`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: frames[frames.length - 1] })
      });
      
      const markData = await markRes.json();
      
      if (markData.success) {
        setStatus(`${markData.name}: ${markData.message}`, "success");
        setTimeout(() => { isProcessing = false; }, 3000); // Cooldown before next scan
      } else {
        setStatus(markData.message || "Failed to mark attendance", "error");
        setTimeout(() => { isProcessing = false; }, 2000);
      }
    } else {
      setStatus("Spoof Detected! Please try again.", "error");
      setTimeout(() => { isProcessing = false; }, 2000);
    }
  } catch (err) {
    console.error("API Error:", err);
    setStatus("Server Error! Make sure Flask is running.", "error");
    setTimeout(() => { isProcessing = false; }, 3000);
  }
}

// Initialize System
async function init() {
  await setupCamera();
  setStatus("Ready. Scanning...", "idle");
  
  // Continuous loop
  setInterval(() => {
    if (!isProcessing) {
      verifyLivenessAndMark();
    }
  }, 1000);
}

init();