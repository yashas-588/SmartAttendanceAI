# Smart Attendance AI

An enterprise-grade, privacy-first biometric attendance verification system featuring edge-computed facial identification, multi-modal liveness detection, HMAC-SHA256 challenge-response verification, and GPS geofencing.

---

## Architecture Overview

- **Edge Processing (Browser)**: High-performance face detection, FaceNet-128 descriptor extraction, and active liveness verification run locally inside the client browser. Raw webcam video streams never leave the user's device.
- **Serverless Backend (Cloud Run & Flask)**: Manages HMAC-SHA256 challenge tokens, validates geofences via the Haversine formula, orchestrates database transactions, and triggers asynchronous notifications.
- **Database & Hosting (Firebase)**: Cloud Firestore for atomic attendance logging, Firebase Authentication for role-based access, and Firebase Hosting for client delivery.

---

## Quick Start / Setup Instructions

### 1. Prerequisites
- Python 3.10+
- Node.js & Firebase CLI (`npm install -g firebase-tools`)
- A Firebase project with **Authentication** (Email/Password) and **Cloud Firestore** enabled.

### 2. Client Firebase Configuration
1. Navigate to `web/` and copy the configuration template:
   ```bash
   cp web/firebase-config.example.js web/firebase-config.js
   ```
2. Open `web/firebase-config.js` and paste your web app credentials from the [Firebase Console](https://console.firebase.google.com/) (`Project Settings > General > Your apps > Web app`).
   > *Note: `web/firebase-config.js` is included in `.gitignore` so your project credentials will never be committed to source control.*

### 3. Backend Environment Setup
1. Copy the environment configuration template:
   ```bash
   cp .env.example .env   # Or edit your .env file
   ```
2. Configure your environment variables:
   ```env
   # Resend API Key for automated parent absence & student welcome emails
   RESEND_API_KEY=re_your_api_key_here
   EMAIL_FROM=onboarding@resend.dev
   PORTAL_URL=https://your-firebase-app.web.app

   # Cryptographic secret for server-side HMAC challenge tokens
   LIVENESS_SECRET=your-secure-random-secret-key

   FLASK_PORT=5005
   FLASK_DEBUG=false
   ```
3. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### 4. Running Locally
1. Start the Flask backend:
   ```bash
   python src/app.py
   ```
2. Serve the static frontend (via Firebase emulator or any static HTTP server):
   ```bash
   firebase emulators:start --only hosting
   # Or using Python http.server from the web directory:
   cd web && python -m http.server 8080
   ```

---

## Security & Publishing Notes

### Restricting the Google / Firebase API Key
The Firebase Web API Key is a client-side identifier used by the JavaScript SDK to connect to your project. When publishing your project to GitHub or deploying to production, ensure that:
1. In the **[Google Cloud Console](https://console.cloud.google.com/apis/credentials)**:
   - Select your Web API key under **Credentials**.
   - Under **Application restrictions**, select **Websites** and restrict referrers to your authorized domains (e.g. `https://your-project.web.app/*`, `http://localhost:*`).
   - Under **API restrictions**, restrict the key to only the services used: **Firebase Authentication API** and **Cloud Firestore API**.
2. Never commit service account private keys (`firebase_key.json`) or real `.env` secrets to git repositories.
