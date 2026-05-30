// firebase-config.js — Shared across all pages (compat SDK, works as global script)
const firebaseConfig = {
  apiKey: "AIzaSyCDBG4wECmxqmrcX5dkhBAA_r6bFTg4KCE",
  authDomain: "smart-attendance-ai-7139f.firebaseapp.com",
  projectId: "smart-attendance-ai-7139f",
  storageBucket: "smart-attendance-ai-7139f.firebasestorage.app",
  messagingSenderId: "452085670379",
  appId: "1:452085670379:web:1b7c62b26133c2c06c79ae"
};
if (!firebase.apps.length) firebase.initializeApp(firebaseConfig);
const auth = firebase.auth();
const db   = firebase.firestore();

// Central environment-aware API URL generator (Priority 1)
function getApiUrl(path) {
  // Override via global config (e.g., set window.BACKEND_URL before this script loads)
  if (window.BACKEND_URL) return window.BACKEND_URL + path;
  if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    return `http://localhost:5005${path}`;
  }
  // Production: uses firebase.json /api/** → Cloud Run rewrite
  return path;
}
