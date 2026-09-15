// firebase-config.example.js — Template for Firebase Web Configuration
//
// INSTRUCTIONS:
// 1. Copy this file to `firebase-config.js`:
//    cp firebase-config.example.js firebase-config.js
// 2. Fill in your project's Firebase configuration values below from the
//    Firebase Console (Project Settings -> General -> Your apps -> Web app).
// 3. Keep firebase-config.js in your local directory (it is ignored by .gitignore).

const firebaseConfig = {
  apiKey: "YOUR_FIREBASE_API_KEY",
  authDomain: "YOUR_PROJECT_ID.firebaseapp.com",
  projectId: "YOUR_PROJECT_ID",
  storageBucket: "YOUR_PROJECT_ID.firebasestorage.app",
  messagingSenderId: "YOUR_MESSAGING_SENDER_ID",
  appId: "YOUR_APP_ID"
};

if (!firebase.apps.length) firebase.initializeApp(firebaseConfig);
const auth = firebase.auth();
const db   = firebase.firestore();

// Central environment-aware API URL generator
function getApiUrl(path) {
  // Override via global config (e.g., set window.BACKEND_URL before this script loads)
  if (window.BACKEND_URL) return window.BACKEND_URL + path;
  if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    return `http://localhost:5005${path}`;
  }
  // Production: uses firebase.json /api/** → Cloud Run rewrite
  return path;
}
