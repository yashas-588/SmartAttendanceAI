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
